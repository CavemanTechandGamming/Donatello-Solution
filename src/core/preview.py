"""
In-app preview: PyAV decode (HEVC 10-bit + AC3 OK) + sounddevice audio.

Video/audio run on a worker thread; the UI only drains a frame queue so Tk
never blocks on decode.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
import sounddevice as sd
from PIL import Image


@dataclass(frozen=True)
class PreviewFrame:
    image: Image.Image
    position: float


class PreviewPlayer:
    """Play / pause / scrub one MKV without blocking the UI thread."""

    def __init__(
        self,
        *,
        on_frame: Callable[[PreviewFrame], None] | None = None,
        on_position: Callable[[float], None] | None = None,
        on_ended: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        max_width: int = 720,
    ) -> None:
        self._on_frame = on_frame
        self._on_position = on_position
        self._on_ended = on_ended
        self._on_error = on_error
        self._max_width = max_width

        self._path: Path | None = None
        self._duration: float | None = None
        self._position = 0.0
        self._playing = False
        self._fps = 24.0
        self._frame_duration = 1.0 / 24.0

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._frame_queue: queue.Queue[PreviewFrame | None] = queue.Queue(maxsize=4)
        self._audio_stream: sd.OutputStream | None = None
        self._audio_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=64)
        self._sample_rate = 48000
        self._channels = 2

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def duration(self) -> float | None:
        return self._duration

    @property
    def position(self) -> float:
        return self._position

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_duration(self) -> float:
        return self._frame_duration

    def open(self, path: Path, duration: float | None = None) -> None:
        self.stop()
        path = Path(path).expanduser().resolve()
        self._path = path
        self._duration = duration
        self._position = 0.0
        self._fps = 24.0
        self._frame_duration = 1.0 / 24.0
        try:
            with av.open(str(path)) as container:
                if self._duration is None:
                    if container.duration is not None:
                        self._duration = float(container.duration) / av.time_base
                if container.streams.video:
                    video = container.streams.video[0]
                    rate = video.average_rate or video.base_rate
                    if rate is not None:
                        try:
                            fps = float(rate)
                            if fps > 1:
                                self._fps = fps
                                self._frame_duration = 1.0 / fps
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
                if container.streams.audio:
                    ctx = container.streams.audio[0].codec_context
                    if ctx.sample_rate:
                        self._sample_rate = int(ctx.sample_rate)
                    self._channels = 2
        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"Could not open preview: {exc}")
            return
        self.show_frame_at(0.0)

    def close(self) -> None:
        self.stop()
        self._path = None
        self._duration = None
        self._position = 0.0
        self._drain_frame_queue()

    def show_frame_at(self, seconds: float) -> None:
        """Seek and decode a single frame (scrub). Safe to call from UI thread."""
        if self._path is None:
            return
        if self._playing:
            self.pause()
        seconds = max(0.0, seconds)
        if self._duration is not None:
            seconds = min(seconds, max(0.0, self._duration - 0.05))
        self._position = seconds
        try:
            image = self._decode_still(self._path, seconds)
        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"Preview seek failed: {exc}")
            return
        if image is None:
            return
        frame = PreviewFrame(image=image, position=seconds)
        if self._on_frame:
            self._on_frame(frame)
        if self._on_position:
            self._on_position(seconds)

    def step_frames(self, delta: int) -> None:
        """Move ±N frames from the current position (pauses playback)."""
        if self._path is None or delta == 0:
            return
        self.show_frame_at(self._position + delta * self._frame_duration)

    def step_seconds(self, delta: float) -> None:
        """Move ±seconds from the current position (pauses playback)."""
        if self._path is None or delta == 0:
            return
        self.show_frame_at(self._position + delta)

    def play(self) -> None:
        if self._path is None or self._playing:
            return
        self._playing = True
        self._stop.clear()
        self._drain_frame_queue()
        self._drain_audio_queue()
        self._start_audio_stream()
        self._thread = threading.Thread(target=self._playback_loop, name="donatello-preview", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._stop_audio_stream()
        self._drain_audio_queue()

    def stop(self) -> None:
        was_playing = self._playing
        self.pause()
        if was_playing or self._position:
            self._position = 0.0
        self._drain_frame_queue()

    def poll(self) -> None:
        """Drain decoded frames onto the UI callbacks (call from Tk ``after``)."""
        while True:
            try:
                item = self._frame_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._playing = False
                self._stop_audio_stream()
                if self._on_ended:
                    self._on_ended()
                continue
            self._position = item.position
            if self._on_frame:
                self._on_frame(item)
            if self._on_position:
                self._on_position(item.position)

    # ── internals ───────────────────────────────────────────────────────

    def _emit_error(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)

    def _drain_frame_queue(self) -> None:
        while True:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    def _drain_audio_queue(self) -> None:
        while True:
            try:
                self._audio_q.get_nowait()
            except queue.Empty:
                break

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:  # noqa: ANN001
        if status:
            pass
        collected = np.zeros((frames, self._channels), dtype=np.float32)
        filled = 0
        while filled < frames:
            try:
                chunk = self._audio_q.get_nowait()
            except queue.Empty:
                break
            if chunk is None:
                break
            need = frames - filled
            take = min(need, chunk.shape[0])
            collected[filled : filled + take] = chunk[:take]
            filled += take
            if take < chunk.shape[0]:
                # put remainder back
                try:
                    self._audio_q.put_nowait(chunk[take:])
                except queue.Full:
                    pass
        outdata[:] = collected

    def _start_audio_stream(self) -> None:
        self._stop_audio_stream()
        try:
            self._audio_stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=1024,
            )
            self._audio_stream.start()
        except Exception as exc:  # noqa: BLE001
            self._audio_stream = None
            self._emit_error(f"Audio preview unavailable: {exc}")

    def _stop_audio_stream(self) -> None:
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None

    def _scale(self, image: Image.Image) -> Image.Image:
        """Cap decode size for RAM; UI aspect-fits into the preview pane."""
        w, h = image.size
        max_w = max(320, self._max_width)
        if w <= max_w:
            return image
        ratio = max_w / float(w)
        return image.resize((max_w, max(1, int(h * ratio))), Image.Resampling.BILINEAR)

    def _decode_still(self, path: Path, seconds: float) -> Image.Image | None:
        with av.open(str(path)) as container:
            if not container.streams.video:
                return None
            video = container.streams.video[0]
            # Seek near target (microseconds on container timeline)
            offset = int(max(0.0, seconds) / video.time_base) if video.time_base else 0
            try:
                container.seek(offset, stream=video, any_frame=False, backward=True)
            except av.AVError:
                container.seek(0)
            for frame in container.decode(video=0):
                pts = float(frame.pts * video.time_base) if frame.pts is not None else seconds
                if pts + 0.05 < seconds:
                    continue
                return self._scale(frame.to_image())
            # Fallback: first frame after seek
            container.seek(0)
            for frame in container.decode(video=0):
                return self._scale(frame.to_image())
        return None

    def _playback_loop(self) -> None:
        path = self._path
        if path is None:
            return
        start_pos = self._position
        try:
            with av.open(str(path)) as container:
                video = container.streams.video[0] if container.streams.video else None
                audio = container.streams.audio[0] if container.streams.audio else None
                if video is None:
                    self._emit_error("No video stream to preview.")
                    self._frame_queue.put(None)
                    return

                if audio is not None:
                    ctx = audio.codec_context
                    if ctx.sample_rate:
                        self._sample_rate = int(ctx.sample_rate)
                    self._channels = 2

                offset = int(max(0.0, start_pos) / video.time_base) if video.time_base else 0
                try:
                    container.seek(offset, stream=video, any_frame=False, backward=True)
                except av.AVError:
                    container.seek(0)

                wall0 = time.perf_counter()
                streams = [video] + ([audio] if audio is not None else [])

                for packet in container.demux(streams):
                    if self._stop.is_set():
                        break
                    try:
                        frames = packet.decode()
                    except av.AVError:
                        continue
                    for frame in frames:
                        if self._stop.is_set():
                            break
                        if isinstance(frame, av.AudioFrame):
                            self._enqueue_audio(frame)
                            continue
                        if not isinstance(frame, av.VideoFrame):
                            continue
                        pts = (
                            float(frame.pts * video.time_base)
                            if frame.pts is not None
                            else start_pos
                        )
                        if pts + 0.02 < start_pos:
                            continue
                        # Pace to wall clock from start_pos
                        target = wall0 + (pts - start_pos)
                        delay = target - time.perf_counter()
                        while delay > 0.002 and not self._stop.is_set():
                            time.sleep(min(delay, 0.02))
                            delay = target - time.perf_counter()
                        image = self._scale(frame.to_image())
                        preview = PreviewFrame(image=image, position=pts)
                        # Drop oldest if UI is slow
                        try:
                            self._frame_queue.put(preview, timeout=0.5)
                        except queue.Full:
                            try:
                                self._frame_queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                self._frame_queue.put_nowait(preview)
                            except queue.Full:
                                pass
                        self._position = pts
                        if self._duration is not None and pts >= self._duration - 0.04:
                            self._stop.set()
                            break

        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"Preview playback failed: {exc}")
        finally:
            try:
                self._frame_queue.put_nowait(None)
            except queue.Full:
                pass
            self._playing = False

    def _enqueue_audio(self, frame: av.AudioFrame) -> None:
        try:
            arr = frame.to_ndarray()
        except Exception:
            return
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        # PyAV often returns (channels, samples)
        if arr.shape[0] <= 8 and arr.shape[0] < arr.shape[1]:
            arr = arr.T
        if arr.dtype != np.float32:
            arr = arr.astype(np.float32, copy=False)
        # Match stereo monitor
        if arr.shape[1] > 2:
            # Simple downmix for preview only
            arr = arr.mean(axis=1, keepdims=True).repeat(2, axis=1).astype(np.float32)
        elif arr.shape[1] == 1:
            arr = np.repeat(arr, 2, axis=1)
        elif arr.shape[1] != 2:
            pad = np.zeros((arr.shape[0], 2 - arr.shape[1]), dtype=np.float32)
            arr = np.concatenate([arr, pad], axis=1)
        self._channels = 2
        try:
            self._audio_q.put(arr, timeout=0.2)
        except queue.Full:
            pass
