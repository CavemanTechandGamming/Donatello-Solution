"""
In-app preview: PyAV decode (HEVC 10-bit + AC3 OK) + sounddevice audio.

Video/audio run on a worker thread; the UI only drains a frame queue so Tk
never blocks on decode.

Preview monitor: sample rate + stereo/mono downmix from Settings (Export unchanged).
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
from av.audio.resampler import AudioResampler
from PIL import Image

from src.core import settings as app_settings
from src.core.preview_subs import (
    ActiveSubtitle,
    actives_from_subset,
    composite_subtitles,
    expire_actives,
    subtitle_set_times,
    visible_actives,
)

_AUDIO_Q_SIZE = 256


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
        self._audio_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_AUDIO_Q_SIZE)
        self._sample_rate = app_settings.DEFAULT_PREVIEW_SAMPLE_RATE
        self._channels = 2
        self._audio_remainder: np.ndarray | None = None
        self._monitor_layout = "stereo"
        self._volume = 1.0
        self._audio_type_index = 0
        # None = subtitles hidden in preview
        self._subtitle_type_index: int | None = None
        # Per-track sync vs video (seconds); positive = later
        self._audio_sync_offset = 0.0
        self._subtitle_sync_offset = 0.0
        self._video_src_size: tuple[int, int] = (0, 0)

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

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        """Monitor gain 0.0–2.0 (0%–200%). Soft-clips on output."""
        self._volume = float(max(0.0, min(2.0, volume)))

    def set_audio_type_index(self, type_index: int) -> None:
        """Which audio stream (0-based among audio) the monitor plays."""
        self._audio_type_index = max(0, int(type_index))

    def set_subtitle_type_index(self, type_index: int | None) -> None:
        """Which subtitle stream (0-based among subs) overlays preview; None = off."""
        if type_index is None:
            self._subtitle_type_index = None
        else:
            self._subtitle_type_index = max(0, int(type_index))

    def set_audio_sync_offset(self, seconds: float) -> None:
        """Shift preview audio vs video (seconds; positive = later)."""
        self._audio_sync_offset = float(seconds)

    def set_subtitle_sync_offset(self, seconds: float) -> None:
        """Shift preview subs vs video (seconds; positive = later)."""
        self._subtitle_sync_offset = float(seconds)

    def open(self, path: Path, duration: float | None = None) -> None:
        self.stop()
        path = Path(path).expanduser().resolve()
        self._path = path
        self._duration = duration
        self._position = 0.0
        self._fps = 24.0
        self._frame_duration = 1.0 / 24.0
        self._video_src_size = (0, 0)
        try:
            with av.open(str(path)) as container:
                if self._duration is None:
                    if container.duration is not None:
                        self._duration = float(container.duration) / av.time_base
                if container.streams.video:
                    video = container.streams.video[0]
                    self._video_src_size = (
                        int(video.width or 0),
                        int(video.height or 0),
                    )
                    rate = video.average_rate or video.base_rate
                    if rate is not None:
                        try:
                            fps = float(rate)
                            if fps > 1:
                                self._fps = fps
                                self._frame_duration = 1.0 / fps
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"Could not open preview: {exc}")
            return
        self._sample_rate = app_settings.get_preview_sample_rate()
        self._monitor_layout, self._channels = app_settings.preview_monitor_layout()
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
        self._configure_monitor_from_source()
        self._start_audio_stream()
        self._thread = threading.Thread(target=self._playback_loop, name="donatello-preview", daemon=True)
        self._thread.start()

    def _configure_monitor_from_source(self) -> None:
        """Set sample rate / layout / channels from Settings (+ file, if Keep channels)."""
        rate = app_settings.get_preview_sample_rate()
        source_layout: str | None = None
        source_channels: int | None = None
        if (
            app_settings.get_preview_downmix() == app_settings.PREVIEW_DOWNMIX_KEEP
            and self._path is not None
        ):
            try:
                with av.open(str(self._path)) as container:
                    audios = list(container.streams.audio)
                    if audios:
                        idx = min(self._audio_type_index, len(audios) - 1)
                        audio = audios[idx]
                        if audio.layout is not None:
                            source_layout = audio.layout.name
                            source_channels = int(audio.layout.nb_channels)
                        else:
                            ctx = audio.codec_context
                            ch = getattr(ctx, "channels", None) or getattr(
                                ctx, "ch_layout", None
                            )
                            if isinstance(ch, int) and ch > 0:
                                source_channels = ch
            except Exception:
                source_layout = None
                source_channels = None
        layout, channels = app_settings.preview_monitor_layout(
            source_layout=source_layout,
            source_channels=source_channels,
        )
        self._sample_rate = rate
        self._monitor_layout = layout
        self._channels = max(1, int(channels))

    def _start_audio_stream(self) -> None:
        self._stop_audio_stream()
        device = app_settings.resolve_preview_audio_device()
        try:
            self._audio_stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=2048,
                latency="high",
                device=device,
            )
            self._audio_stream.start()
            return
        except Exception as exc:  # noqa: BLE001
            # Keep-channels often fails on stereo-only headphones — fall back.
            if self._channels > 2:
                self._emit_error(
                    f"Preview can't open {self._channels} channels on this device "
                    f"({exc}). Falling back to stereo."
                )
                self._channels = 2
                self._monitor_layout = "stereo"
                try:
                    self._audio_stream = sd.OutputStream(
                        samplerate=self._sample_rate,
                        channels=2,
                        dtype="float32",
                        callback=self._audio_callback,
                        blocksize=2048,
                        latency="high",
                        device=device,
                    )
                    self._audio_stream.start()
                    return
                except Exception as exc2:  # noqa: BLE001
                    self._audio_stream = None
                    self._emit_error(f"Audio preview unavailable: {exc2}")
                    return
            self._audio_stream = None
            self._emit_error(f"Audio preview unavailable: {exc}")

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
        self._audio_remainder = None

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:  # noqa: ANN001
        collected = np.zeros((frames, self._channels), dtype=np.float32)
        filled = 0
        if self._audio_remainder is not None and self._audio_remainder.shape[0] > 0:
            chunk = self._audio_remainder
            take = min(frames, chunk.shape[0])
            collected[:take] = chunk[:take]
            filled = take
            self._audio_remainder = chunk[take:] if take < chunk.shape[0] else None
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
                self._audio_remainder = chunk[take:]
                break
        outdata[:] = collected

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
            src_w = int(video.width or self._video_src_size[0] or 0)
            src_h = int(video.height or self._video_src_size[1] or 0)
            if src_w and src_h:
                self._video_src_size = (src_w, src_h)

            sub_stream = self._resolve_subtitle_stream(container)
            actives: list[ActiveSubtitle] = []
            sub_clear_at: float | None = None
            sub_off = float(self._subtitle_sync_offset)

            # Seek early so delayed (+offset) cues are demuxed before the frame,
            # and so bitmap (PGS) decoder state can catch up.
            early = 1.0 if sub_stream is not None else 0.0
            if sub_stream is not None and sub_off > 0:
                early = max(early, sub_off + 0.25)
            seek_t = max(0.0, seconds - early)
            offset = int(seek_t / video.time_base) if video.time_base else 0
            try:
                container.seek(offset, stream=video, any_frame=False, backward=True)
            except av.AVError:
                container.seek(0)

            streams = [video] + ([sub_stream] if sub_stream is not None else [])
            image: Image.Image | None = None
            for packet in container.demux(streams):
                if (
                    sub_stream is not None
                    and packet.stream.index == sub_stream.index
                ):
                    actives, sub_clear_at = self._ingest_subtitle_packet(
                        sub_stream,
                        packet,
                        actives,
                        src_w=src_w,
                        src_h=src_h,
                        time_offset=sub_off,
                        clear_at=sub_clear_at,
                        video_pts=seconds,
                    )
                    continue
                if packet.stream.index != video.index:
                    continue
                try:
                    frames = packet.decode()
                except av.AVError:
                    continue
                for frame in frames:
                    if not isinstance(frame, av.VideoFrame):
                        continue
                    pts = (
                        float(frame.pts * video.time_base)
                        if frame.pts is not None
                        else seconds
                    )
                    if pts + 0.05 < seconds:
                        continue
                    image = self._scale(frame.to_image())
                    if sub_clear_at is not None and seconds >= sub_clear_at:
                        actives = []
                        sub_clear_at = None
                    actives = expire_actives(actives, seconds)
                    return composite_subtitles(
                        image, visible_actives(actives, seconds)
                    )

            if image is None:
                container.seek(0)
                for frame in container.decode(video=0):
                    image = self._scale(frame.to_image())
                    break
            if image is None:
                return None
            if sub_clear_at is not None and seconds >= sub_clear_at:
                actives = []
            actives = expire_actives(actives, seconds)
            return composite_subtitles(image, visible_actives(actives, seconds))

    def _resolve_subtitle_stream(self, container):  # noqa: ANN001
        if self._subtitle_type_index is None:
            return None
        subs = list(container.streams.subtitles)
        if not subs:
            return None
        idx = min(self._subtitle_type_index, len(subs) - 1)
        return subs[idx]

    def _ingest_subtitle_packet(
        self,
        sub_stream,  # noqa: ANN001
        packet,  # noqa: ANN001
        actives: list[ActiveSubtitle],
        *,
        src_w: int,
        src_h: int,
        time_offset: float = 0.0,
        clear_at: float | None = None,
        video_pts: float = 0.0,
    ) -> tuple[list[ActiveSubtitle], float | None]:
        try:
            subset = sub_stream.decode2(packet)
        except Exception:
            return actives, clear_at
        if subset is None:
            return actives, clear_at
        if not subset.rects:
            # PGS clear — honor sync offset so clears stay lined up with cues.
            start, _end = subtitle_set_times(subset)
            return actives, start + float(time_offset)
        new_actives = actives_from_subset(
            subset,
            src_w=src_w,
            src_h=src_h,
            time_offset=time_offset,
        )
        kept = expire_actives(actives, video_pts)
        if not new_actives:
            return kept, clear_at
        return kept + new_actives, clear_at

    def _playback_loop(self) -> None:
        path = self._path
        if path is None:
            return
        start_pos = self._position
        try:
            with av.open(str(path)) as container:
                video = container.streams.video[0] if container.streams.video else None
                audios = list(container.streams.audio)
                audio = None
                if audios:
                    idx = min(self._audio_type_index, len(audios) - 1)
                    audio = audios[idx]
                if video is None:
                    self._emit_error("No video stream to preview.")
                    self._frame_queue.put(None)
                    return

                src_w = int(video.width or self._video_src_size[0] or 0)
                src_h = int(video.height or self._video_src_size[1] or 0)
                if src_w and src_h:
                    self._video_src_size = (src_w, src_h)

                sub_stream = self._resolve_subtitle_stream(container)
                actives: list[ActiveSubtitle] = []
                sub_clear_at: float | None = None
                audio_off = float(self._audio_sync_offset)
                sub_off = float(self._subtitle_sync_offset)

                resampler: AudioResampler | None = None
                if audio is not None:
                    rate = self._sample_rate
                    layout = self._monitor_layout
                    resampler = AudioResampler(
                        format="fltp",
                        layout=layout,
                        rate=rate,
                    )

                seek_base = start_pos
                if audio is not None and audio_off < 0:
                    seek_base = min(seek_base, start_pos + audio_off)
                if sub_stream is not None and sub_off > 0:
                    seek_base = min(seek_base, start_pos - sub_off)
                early = 1.0 if sub_stream is not None else 0.0
                seek_t = max(0.0, seek_base - early)
                offset = int(seek_t / video.time_base) if video.time_base else 0
                try:
                    container.seek(offset, stream=video, any_frame=False, backward=True)
                except av.AVError:
                    container.seek(0)

                # Positive audio offset: delay monitor with leading silence.
                if audio is not None and audio_off > 1e-4:
                    n = int(round(audio_off * self._sample_rate))
                    if n > 0:
                        silence = np.zeros((n, self._channels), dtype=np.float32)
                        chunk = 4096
                        for i in range(0, n, chunk):
                            self._enqueue_monitor_audio_array(silence[i : i + chunk])

                # Audio earlier than video: start enqueue before video display time.
                audio_gate = start_pos + audio_off if audio_off < 0 else start_pos

                wall0 = time.perf_counter()
                streams = [video]
                if audio is not None:
                    streams.append(audio)
                if sub_stream is not None:
                    streams.append(sub_stream)

                for packet in container.demux(streams):
                    if self._stop.is_set():
                        break
                    if (
                        sub_stream is not None
                        and packet.stream.index == sub_stream.index
                    ):
                        actives, sub_clear_at = self._ingest_subtitle_packet(
                            sub_stream,
                            packet,
                            actives,
                            src_w=src_w,
                            src_h=src_h,
                            time_offset=sub_off,
                            clear_at=sub_clear_at,
                            video_pts=self._position,
                        )
                        continue
                    try:
                        frames = packet.decode()
                    except av.AVError:
                        continue
                    for frame in frames:
                        if self._stop.is_set():
                            break
                        if isinstance(frame, av.AudioFrame):
                            if resampler is None:
                                continue
                            pts_a = (
                                float(frame.pts * audio.time_base)
                                if audio is not None and frame.pts is not None
                                else start_pos
                            )
                            if pts_a + 0.02 < audio_gate:
                                continue
                            try:
                                for out in resampler.resample(frame):
                                    self._enqueue_monitor_audio(out)
                            except Exception:
                                continue
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
                        if sub_clear_at is not None and pts >= sub_clear_at:
                            actives = []
                            sub_clear_at = None
                        actives = expire_actives(actives, pts)
                        image = composite_subtitles(
                            image, visible_actives(actives, pts)
                        )
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

                if resampler is not None and not self._stop.is_set():
                    try:
                        for out in resampler.resample(None):
                            self._enqueue_monitor_audio(out)
                    except Exception:
                        pass

        except Exception as exc:  # noqa: BLE001
            self._emit_error(f"Preview playback failed: {exc}")
        finally:
            try:
                self._frame_queue.put_nowait(None)
            except queue.Full:
                pass
            self._playing = False

    def _enqueue_monitor_audio_array(self, arr: np.ndarray) -> None:
        """Queue a pre-built float32 (samples, channels) block."""
        if arr.size == 0:
            return
        arr = np.asarray(arr, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        if arr.shape[1] < self._channels:
            pad = np.zeros(
                (arr.shape[0], self._channels - arr.shape[1]), dtype=np.float32
            )
            arr = np.concatenate([arr, pad], axis=1)
        elif arr.shape[1] > self._channels:
            arr = arr[:, : self._channels]
        if self._volume != 1.0:
            arr = arr * self._volume
            np.clip(arr, -1.0, 1.0, out=arr)
        while not self._stop.is_set():
            try:
                self._audio_q.put(arr, timeout=0.05)
                return
            except queue.Full:
                continue

    def _enqueue_monitor_audio(self, frame: av.AudioFrame) -> None:
        """Queue one float frame (monitor channel count) for PortAudio."""
        try:
            arr = frame.to_ndarray()
        except Exception:
            return
        if arr.size == 0:
            return
        if arr.ndim == 1:
            if self._channels == 1:
                arr = arr.reshape(-1, 1)
            elif arr.shape[0] % self._channels == 0:
                arr = arr.reshape(-1, self._channels)
            else:
                arr = np.column_stack([arr] * self._channels)
        elif arr.shape[0] <= 8 and arr.shape[0] < arr.shape[1]:
            # Planar (channels, samples) → (samples, channels)
            arr = arr.T

        if arr.shape[1] < self._channels:
            pad = np.zeros(
                (arr.shape[0], self._channels - arr.shape[1]), dtype=np.float32
            )
            arr = np.concatenate([arr, pad], axis=1)
        elif arr.shape[1] > self._channels:
            arr = arr[:, : self._channels]

        if np.issubdtype(arr.dtype, np.integer):
            info = np.iinfo(arr.dtype)
            scale = float(max(abs(info.min), info.max)) or 1.0
            arr = arr.astype(np.float32) / scale
        else:
            arr = np.asarray(arr, dtype=np.float32)
        if self._volume != 1.0:
            arr = arr * self._volume
        np.clip(arr, -1.0, 1.0, out=arr)

        # Prefer waiting over dropping — drops caused crackle/roughness.
        while not self._stop.is_set():
            try:
                self._audio_q.put(arr, timeout=0.05)
                return
            except queue.Full:
                continue
