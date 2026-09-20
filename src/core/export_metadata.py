"""Track metadata edits and remux-with-copy Export (title / language / flags / chapters)."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.ffmpeg_paths import FFmpegBootstrapError, ffmpeg_binary
from src.core.logging_setup import get_logger
from src.core.markers import (
    MARKER_KIND_CHAPTER,
    TimelineMarker,
    build_chapters_ffmetadata,
    chapter_markers,
)
from src.core.probe import MediaTrack

logger = get_logger("export")


class ExportError(Exception):
    """Raised when metadata remux / export fails."""


@dataclass
class TrackEditState:
    """Editable metadata for one stream (overlays probe defaults)."""

    kind: str
    stream_index: int
    type_index: int
    title: str
    language: str
    is_default: bool = False
    is_forced: bool = False

    @classmethod
    def from_track(cls, track: MediaTrack) -> TrackEditState:
        return cls(
            kind=track.kind,
            stream_index=track.stream_index,
            type_index=track.type_index,
            title=track.title or "",
            language=(track.language or "und").strip() or "und",
            is_default=bool(track.is_default),
            is_forced=bool(track.is_forced),
        )

    def display_label(self, track: MediaTrack | None = None) -> str:
        """One-line label using edited fields + probe tech details when available."""
        lang = (self.language or "und").upper()
        codec = track.codec if track else "?"
        parts = [f"[{self.kind[0].upper()}{self.type_index}]", lang, codec]

        if track and self.kind == "video":
            if track.width and track.height:
                parts.append(f"{track.width}x{track.height}")
            if track.bit_depth:
                parts.append(f"{track.bit_depth}-bit")
        elif track and self.kind == "audio":
            if track.channel_layout:
                parts.append(track.channel_layout)
            elif track.channels:
                parts.append(f"{track.channels}ch")
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


def edits_from_tracks(tracks: list[MediaTrack]) -> dict[int, TrackEditState]:
    """Map stream_index → edit state seeded from probe."""
    return {t.stream_index: TrackEditState.from_track(t) for t in tracks}


def build_metadata_remux_command(
    source: Path,
    output: Path,
    edits: list[TrackEditState],
    *,
    chapters_file: Path | None = None,
) -> list[str]:
    """
    Stream-copy remux writing title/language (and sub Default/Forced) onto streams.

    Optional *chapters_file* is an FFMETADATA1 sidecar mapped as chapters.
    """
    try:
        ffmpeg_bin = ffmpeg_binary()
    except FFmpegBootstrapError as exc:
        raise ExportError(str(exc)) from exc

    cmd: list[str] = [
        ffmpeg_bin,
        "-hide_banner",
        "-y",
        "-i",
        str(source),
    ]
    if chapters_file is not None:
        cmd.extend(["-i", str(chapters_file)])

    cmd.extend(["-map", "0", "-c", "copy"])
    if chapters_file is not None:
        # Drop source chapters; take ours from the metadata input.
        cmd.extend(["-map_chapters", "1"])
    else:
        cmd.extend(["-map_chapters", "0"])

    for edit in edits:
        if edit.kind not in ("video", "audio", "subtitle"):
            continue
        letter = {"video": "v", "audio": "a", "subtitle": "s"}[edit.kind]
        out_n = edit.type_index
        title = edit.title.replace("\n", " ").replace("\r", "").strip()
        language = (edit.language or "").strip() or "und"
        if title:
            cmd.extend([f"-metadata:s:{letter}:{out_n}", f"title={title}"])
        cmd.extend([f"-metadata:s:{letter}:{out_n}", f"language={language}"])
        if edit.kind == "subtitle":
            flags: list[str] = []
            if edit.is_default:
                flags.append("default")
            if edit.is_forced:
                flags.append("forced")
            # FFmpeg wants -disposition:s:N (not -disposition:s:s:N)
            cmd.extend(
                [
                    f"-disposition:{letter}:{out_n}",
                    "+".join(flags) if flags else "0",
                ]
            )

    cmd.append(str(output))
    return cmd


def _ffmpeg() -> str:
    try:
        return ffmpeg_binary()
    except FFmpegBootstrapError as exc:
        raise ExportError(str(exc)) from exc


def _run_ffmpeg(cmd: list[str], *, label: str) -> None:
    logger.debug("ffmpeg %s: %s", label, " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        logger.exception("Failed to run ffmpeg (%s)", label)
        raise ExportError(f"Failed to run ffmpeg ({label}): {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.error(
            "FFmpeg %s failed (exit %s)\n%s",
            label,
            completed.returncode,
            detail[-4000:] if detail else "(no output)",
        )
        lines = [ln for ln in detail.splitlines() if ln.strip()]
        useful = "\n".join(lines[-6:]) if lines else f"exit {completed.returncode}"
        raise ExportError(f"FFmpeg {label} failed:\n{useful}")


def _extract_copy_segment(
    source: Path,
    dest: Path,
    *,
    start: float,
    duration: float,
) -> None:
    """Stream-copy [start, start+duration) from *source* into *dest*."""
    ff = _ffmpeg()
    cmd = [ff, "-hide_banner", "-y", "-i", str(source)]
    if start > 0.02:
        cmd.extend(["-ss", f"{start:.3f}"])
    cmd.extend(["-t", f"{duration:.3f}", "-map", "0", "-c", "copy", str(dest)])
    _run_ffmpeg(cmd, label="export trim")


def markers_for_export_range(
    markers: list[TimelineMarker],
    *,
    start: float,
    end: float,
) -> list[TimelineMarker]:
    """Keep chapter markers inside [start, end] and shift times so export starts at 0."""
    out: list[TimelineMarker] = []
    for marker in chapter_markers(markers):
        if marker.time < start - 0.001:
            continue
        if marker.time > end + 0.001:
            continue
        out.append(
            TimelineMarker(
                time=max(0.0, marker.time - start),
                name=marker.name,
                kind=MARKER_KIND_CHAPTER,
            )
        )
    return out


def export_with_track_metadata(
    source: Path,
    output: Path,
    edits: list[TrackEditState],
    *,
    markers: list[TimelineMarker] | None = None,
    duration: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> None:
    """
    Remux *source* to *output* with track metadata applied (stream copy).

    When *range_start* / *range_end* are set (NLE In/Out), only that span is
    written — keep between In and Out (opposite of Cut, which removes it).

    When *markers* are provided, they are written as MKV chapters (shifted
    into the exported range when trimming).
    """
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if not source.is_file():
        raise ExportError(f"Source not found: {source}")
    if output.suffix.lower() != ".mkv":
        raise ExportError("Export output must be an .mkv file.")
    if source == output:
        raise ExportError("Export path must be different from the source file.")

    output.parent.mkdir(parents=True, exist_ok=True)

    src_duration = float(duration) if duration is not None else 0.0
    trim = range_start is not None or range_end is not None
    start = float(range_start) if range_start is not None else 0.0
    end = float(range_end) if range_end is not None else src_duration

    if trim:
        if start < 0 or (range_end is not None and end < 0):
            raise ExportError("Export In/Out times must be non-negative.")
        if src_duration > 0:
            end = min(end, src_duration) if range_end is not None else src_duration
            start = min(start, max(0.0, src_duration - 0.05))
        if end <= start:
            raise ExportError("Mark Out must be after Mark In for ranged Export.")
        span = end - start
        chapter_markers = markers_for_export_range(
            list(markers or []), start=start, end=end
        )
        out_duration = span
        logger.info(
            "Export trim In=%.3f Out=%.3f (%.3fs) %s → %s",
            start,
            end,
            span,
            source.name,
            output,
        )
        with tempfile.TemporaryDirectory(prefix="donatello-export-") as tmp:
            interim = Path(tmp) / "range.mkv"
            _extract_copy_segment(source, interim, start=start, duration=span)
            _remux_with_metadata(
                interim,
                output,
                edits,
                markers=chapter_markers,
                duration=out_duration,
            )
        return

    _remux_with_metadata(
        source,
        output,
        edits,
        markers=chapter_markers(list(markers or [])),
        duration=src_duration if src_duration > 0 else None,
    )


def _remux_with_metadata(
    source: Path,
    output: Path,
    edits: list[TrackEditState],
    *,
    markers: list[TimelineMarker],
    duration: float | None,
) -> None:
    chapter_markers = list(markers)
    use_chapters = bool(chapter_markers)

    def _run(chapters_file: Path | None) -> None:
        cmd = build_metadata_remux_command(
            source, output, edits, chapters_file=chapters_file
        )
        logger.info(
            "Export %s → %s (%d track edit(s), %d chapter(s))",
            source.name,
            output,
            len(edits),
            len(chapter_markers) if chapters_file else 0,
        )
        logger.debug("ffmpeg export: %s", " ".join(cmd))
        _run_ffmpeg(cmd, label="export remux")
        logger.info("Export complete: %s", output)

    if not use_chapters:
        _run(None)
        return

    dur = float(duration) if duration is not None else 0.0
    if dur <= 0 and chapter_markers:
        dur = max(m.time for m in chapter_markers) + 1.0

    with tempfile.TemporaryDirectory(prefix="donatello-chapters-") as tmp:
        meta_path = Path(tmp) / "chapters.ffmeta"
        meta_path.write_text(
            build_chapters_ffmetadata(chapter_markers, dur),
            encoding="utf-8",
        )
        _run(meta_path)
