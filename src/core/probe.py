"""
Probe MKV files with ffprobe — duration, video/audio/subtitle tracks, layouts.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.ffmpeg_paths import FFmpegBootstrapError, ensure_ffmpeg_on_path, ffprobe_binary
from src.core.logging_setup import get_logger
from src.core.markers import TimelineMarker

logger = get_logger("probe")


class ProbeError(Exception):
    """Raised when a file cannot be probed or is not a usable MKV."""


_SUBTITLE_CODEC_HINTS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "mov_text",
    "webvtt",
    "hdmv_pgs_subtitle",
    "pgssub",
    "dvd_subtitle",
    "dvdsub",
    "dvb_subtitle",
    "dvb_teletext",
    "xsub",
    "eia_608",
    "cc_dec",
    "timed_id3",
    "arib_caption",
}

# Locked layouts we care about preserving (plus honest labels for others).
_CHANNEL_COUNT_LAYOUT = {
    1: "mono",
    2: "stereo",
    3: "2.1",
    6: "5.1",
    8: "7.1",
}


@dataclass(frozen=True)
class MediaTrack:
    """One stream in an MKV (video, audio, or subtitle)."""

    kind: str  # "video" | "audio" | "subtitle" | other
    stream_index: int
    type_index: int  # index among streams of the same kind (0-based)
    codec: str
    language: str
    title: str
    # Audio-only
    channels: int | None = None
    channel_layout: str | None = None
    # Video-only
    width: int | None = None
    height: int | None = None
    bit_depth: int | None = None
    # Subtitle disposition
    is_default: bool = False
    is_forced: bool = False

    def display_label(self) -> str:
        """Human-readable one-line label for the track list."""
        lang = (self.language or "und").upper()
        parts = [f"[{self.kind[0].upper()}{self.type_index}]", lang, self.codec]

        if self.kind == "video":
            if self.width and self.height:
                parts.append(f"{self.width}x{self.height}")
            if self.bit_depth:
                parts.append(f"{self.bit_depth}-bit")
        elif self.kind == "audio":
            layout = self.channel_layout or (
                _CHANNEL_COUNT_LAYOUT.get(self.channels or 0)
                if self.channels
                else None
            )
            if layout:
                parts.append(layout)
            elif self.channels:
                parts.append(f"{self.channels}ch")
        elif self.kind == "subtitle":
            flags: list[str] = []
            if self.is_default:
                flags.append("default")
            if self.is_forced:
                flags.append("forced")
            if flags:
                parts.append(f"({', '.join(flags)})")

        if self.title:
            parts.append(f"— {self.title}")
        return "  ".join(parts)


@dataclass(frozen=True)
class ProbeResult:
    """Full MKV probe outcome for the UI (one ffprobe call)."""

    path: Path
    duration_seconds: float | None
    tracks: list[MediaTrack]
    format_name: str
    chapters: list[TimelineMarker] = field(default_factory=list)
    attachment_count: int = 0


def format_duration(seconds: float | None) -> str:
    """Format seconds as ``H:MM:SS`` (or ``—`` if unknown)."""
    if seconds is None or seconds < 0:
        return "—"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _is_subtitle_stream(stream: dict[str, Any]) -> bool:
    codec_type = (stream.get("codec_type") or "").lower()
    if codec_type == "subtitle":
        return True
    codec_name = (stream.get("codec_name") or "").lower()
    return codec_name in _SUBTITLE_CODEC_HINTS


def _tag(tags: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = tags.get(key)
        if value:
            return str(value)
    return ""


def _normalize_channel_layout(raw: str | None, channels: int | None) -> str | None:
    """Map ffprobe layout / channel count to stereo · 2.1 · 5.1 · 7.1 when possible."""
    if raw:
        name = raw.strip().lower().replace(" ", "")
        aliases = {
            "stereo": "stereo",
            "mono": "mono",
            "2.1": "2.1",
            "3.0": "2.1",
            "5.1": "5.1",
            "5.1(side)": "5.1",
            "7.1": "7.1",
            "hexagonal": "5.1",
            "octagonal": "7.1",
        }
        if name in aliases:
            return aliases[name]
        # Common descriptive forms
        if "7.1" in name:
            return "7.1"
        if "5.1" in name:
            return "5.1"
        if name in ("2.1", "lowfrequency"):
            return "2.1"
        if name == "stereo":
            return "stereo"

    if channels in _CHANNEL_COUNT_LAYOUT:
        return _CHANNEL_COUNT_LAYOUT[channels]
    return raw.strip() if raw else None


def _bit_depth(stream: dict[str, Any]) -> int | None:
    raw = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    if raw is None or raw == "N/A":
        # pix_fmt hints: yuv420p10le → 10
        pix = (stream.get("pix_fmt") or "").lower()
        for depth in (12, 10, 8):
            if f"p{depth}" in pix or f"{depth}le" in pix or f"{depth}be" in pix:
                return depth
        return None
    try:
        value = int(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _duration_from_info(info: dict[str, Any]) -> float | None:
    format_block = info.get("format") or {}
    raw = format_block.get("duration")
    if raw is not None and raw != "N/A":
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    best: float | None = None
    for stream in info.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        raw = stream.get("duration")
        if raw is None or raw == "N/A":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and (best is None or value > best):
            best = value
    return best


def _assert_mkv(path: Path, info: dict[str, Any]) -> None:
    if path.suffix.lower() != ".mkv":
        raise ProbeError(
            f"'{path.name}' is not an MKV. Donatello opens .mkv files only."
        )

    format_name = (info.get("format") or {}).get("format_name") or ""
    # ffprobe reports matroska,webm for MKV
    lowered = format_name.lower()
    if "matroska" not in lowered and "mkv" not in lowered:
        raise ProbeError(
            f"'{path.name}' does not look like a Matroska/MKV container "
            f"(format: {format_name or 'unknown'})."
        )


def _probe_json(path: Path) -> dict[str, Any]:
    """
    Run ffprobe with a large probesize so late-multiplexed subtitle streams
    (common in big rips) are not missed by default probe limits.
    """
    try:
        ensure_ffmpeg_on_path()
        probe_bin = ffprobe_binary()
    except FFmpegBootstrapError as exc:
        raise ProbeError(str(exc)) from exc

    cmd = [
        probe_bin,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-probesize",
        "200M",
        "-analyzeduration",
        "200M",
        "-of",
        "json",
        str(path),
    ]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise ProbeError(
            "ffprobe was not found. Install FFmpeg on PATH or: "
            "pip install -r requirements/requirements.txt"
        ) from exc
    except OSError as exc:
        raise ProbeError(f"Failed to run ffprobe: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or (
            f"exit {completed.returncode}"
        )
        logger.error("ffprobe failed for %s: %s", path.name, detail[-2000:])
        raise ProbeError(f"Failed to probe '{path.name}': {detail}")

    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for '{path.name}'.") from exc


def _tracks_from_info(info: dict[str, Any]) -> list[MediaTrack]:
    tracks: list[MediaTrack] = []
    type_counters: dict[str, int] = {"video": 0, "audio": 0, "subtitle": 0}

    for stream in info.get("streams") or []:
        codec_type = (stream.get("codec_type") or "").lower()
        if codec_type == "attachment":
            continue  # counted on ProbeResult.attachment_count
        if _is_subtitle_stream(stream):
            kind = "subtitle"
        elif codec_type in ("video", "audio"):
            kind = codec_type
        else:
            kind = codec_type or "other"

        type_index = type_counters.get(kind, 0)
        if kind in type_counters:
            type_counters[kind] = type_index + 1

        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = _tag(tags, "language", "LANGUAGE") or "und"
        title = _tag(tags, "title", "TITLE")

        channels: int | None = None
        channel_layout: str | None = None
        width: int | None = None
        height: int | None = None
        bit_depth: int | None = None

        if kind == "audio":
            raw_ch = stream.get("channels")
            try:
                channels = int(raw_ch) if raw_ch is not None else None
            except (TypeError, ValueError):
                channels = None
            channel_layout = _normalize_channel_layout(
                stream.get("channel_layout"),
                channels,
            )
        elif kind == "video":
            try:
                width = int(stream["width"]) if stream.get("width") else None
            except (TypeError, ValueError, KeyError):
                width = None
            try:
                height = int(stream["height"]) if stream.get("height") else None
            except (TypeError, ValueError, KeyError):
                height = None
            bit_depth = _bit_depth(stream)

        tracks.append(
            MediaTrack(
                kind=kind,
                stream_index=int(stream.get("index", type_index)),
                type_index=type_index,
                codec=stream.get("codec_name") or stream.get("codec_type") or "unknown",
                language=language,
                title=title,
                channels=channels,
                channel_layout=channel_layout,
                width=width,
                height=height,
                bit_depth=bit_depth,
                is_default=bool(disposition.get("default")),
                is_forced=bool(disposition.get("forced")),
            )
        )

    return tracks


def _chapters_from_info(info: dict[str, Any]) -> list[TimelineMarker]:
    """MKV chapter start times → timeline markers (title from chapter tags)."""
    out: list[TimelineMarker] = []
    raw_chapters = info.get("chapters") or []
    if not isinstance(raw_chapters, list):
        return []

    for index, chapter in enumerate(raw_chapters):
        if not isinstance(chapter, dict):
            continue
        start_raw = chapter.get("start_time")
        if start_raw is None or start_raw == "N/A":
            start_raw = chapter.get("start")
            time_base = chapter.get("time_base") or "1/1000000000"
            try:
                start_ticks = float(start_raw)
                if "/" in str(time_base):
                    num_s, den_s = str(time_base).split("/", 1)
                    t = start_ticks * (float(num_s) / float(den_s))
                else:
                    t = start_ticks
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        else:
            try:
                t = float(start_raw)
            except (TypeError, ValueError):
                continue

        tags = chapter.get("tags") or {}
        title = ""
        if isinstance(tags, dict):
            title = str(tags.get("title") or tags.get("TITLE") or "").strip()
        if not title:
            title = f"Chapter {index + 1}"
        out.append(TimelineMarker(time=t, name=title))

    out.sort(key=lambda m: (m.time, m.name.lower()))
    # Drop exact duplicate times (keep last name)
    unique: list[TimelineMarker] = []
    for marker in out:
        if unique and abs(unique[-1].time - marker.time) < 0.001:
            unique[-1] = marker
        else:
            unique.append(marker)
    return unique


def probe_mkv(path: Path | str) -> ProbeResult:
    """
    Probe an MKV once: duration, all tracks, audio layouts, chapters.

    Raises ProbeError if the file is missing, not MKV, has no video, or fails.
    """
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ProbeError(f"File not found: {path}")

    info = _probe_json(path)
    _assert_mkv(path, info)

    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        raise ProbeError(
            f"'{path.name}' does not contain a video stream."
        )

    format_name = str((info.get("format") or {}).get("format_name") or "matroska")
    chapters = _chapters_from_info(info)
    attachment_count = sum(
        1
        for s in streams
        if (s.get("codec_type") or "").lower() == "attachment"
    )
    if chapters:
        logger.info(
            "Probed %d chapter(s) in %s",
            len(chapters),
            path.name,
        )
    if attachment_count:
        logger.info(
            "Probed %d attachment(s) in %s",
            attachment_count,
            path.name,
        )
    return ProbeResult(
        path=path,
        duration_seconds=_duration_from_info(info),
        tracks=_tracks_from_info(info),
        format_name=format_name,
        chapters=chapters,
        attachment_count=attachment_count,
    )
