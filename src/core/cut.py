"""
Cut a time range out of an MKV.

Default: remove the range from **all** streams together (stay in sync) via
stream-copy segment + concat when possible.

Single-stream (Ctrl): shorten only the selected video/audio track (intentional desync).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.core.export_metadata import ExportError, TrackEditState, export_with_track_metadata
from src.core.ffmpeg_paths import FFmpegBootstrapError, ffmpeg_binary
from src.core.logging_setup import get_logger
from src.core.probe import MediaTrack

logger = get_logger("cut")


class CutError(ExportError):
    """Raised when a cut fails."""


@dataclass(frozen=True)
class CutRange:
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < 0:
            raise CutError("Cut times must be non-negative.")
        if self.end <= self.start:
            raise CutError("Out point must be after In point.")


def _ffmpeg() -> str:
    try:
        return ffmpeg_binary()
    except FFmpegBootstrapError as exc:
        raise CutError(str(exc)) from exc


def _run(cmd: list[str], *, label: str) -> None:
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
        raise CutError(f"Failed to run ffmpeg ({label}): {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.error(
            "FFmpeg %s failed (exit %s)\n%s",
            label,
            completed.returncode,
            detail[-4000:] if detail else "(no output)",
        )
        last = detail.splitlines()[-1] if detail else f"exit {completed.returncode}"
        raise CutError(f"FFmpeg {label} failed: {last}")


def _finalize(
    interim: Path,
    output: Path,
    edits: list[TrackEditState] | None,
) -> None:
    if edits:
        export_with_track_metadata(interim, output, edits)
    else:
        output.write_bytes(interim.read_bytes())


def _extract_segment(
    ff: str,
    source: Path,
    dest: Path,
    *,
    start: float | None = None,
    duration: float | None = None,
    label: str = "segment",
) -> None:
    """Stream-copy a segment. ``start`` uses -ss after -i; ``duration`` uses -t."""
    cmd = [ff, "-hide_banner", "-y", "-i", str(source)]
    if start is not None and start > 0.02:
        cmd.extend(["-ss", f"{start:.3f}"])
    if duration is not None:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend(["-map", "0", "-c", "copy", str(dest)])
    _run(cmd, label=label)


def _concat_copy(ff: str, parts: list[Path], dest: Path) -> None:
    if not parts:
        raise CutError("Nothing to concatenate.")
    if len(parts) == 1:
        dest.write_bytes(parts[0].read_bytes())
        return
    list_file = dest.parent / "concat.txt"
    lines = []
    for part in parts:
        escaped = part.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(
        [
            ff,
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-fflags",
            "+genpts",
            str(dest),
        ],
        label="concat",
    )


def cut_out_all_streams(
    source: Path,
    output: Path,
    cut: CutRange,
    *,
    duration: float | None = None,
    edits: list[TrackEditState] | None = None,
) -> None:
    """Remove [start, end) from every stream together (stream-copy when possible)."""
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if not source.is_file():
        raise CutError(f"Source not found: {source}")
    if output.suffix.lower() != ".mkv":
        raise CutError("Cut output must be an .mkv file.")
    if source == output:
        raise CutError("Cut output must be a different path than the source.")

    start, end = cut.start, cut.end
    if duration is not None and end > duration + 0.05:
        end = duration

    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()

    with tempfile.TemporaryDirectory(prefix="donatello-cut-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []

        if start > 0.02:
            part1 = tmp_path / "part1.mkv"
            _extract_segment(
                ff, source, part1, duration=start, label="cut head"
            )
            parts.append(part1)

        keep_tail = duration is None or end < (duration - 0.02)
        if keep_tail:
            part2 = tmp_path / "part2.mkv"
            _extract_segment(
                ff, source, part2, start=end, label="cut tail"
            )
            parts.append(part2)

        if not parts:
            raise CutError("Cut would remove the entire file.")

        interim = tmp_path / "joined.mkv"
        _concat_copy(ff, parts, interim)
        _finalize(interim, output, edits)


def cut_out_single_video(
    source: Path,
    output: Path,
    cut: CutRange,
    track: MediaTrack,
    *,
    duration: float | None = None,
    edits: list[TrackEditState] | None = None,
) -> None:
    """Remove [start, end) from one video track; copy audio + subs (may desync)."""
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if track.kind != "video":
        raise CutError("Single video cut requires a video track.")

    start, end = cut.start, cut.end
    if duration is not None and end > duration + 0.05:
        end = duration

    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    idx = track.type_index
    fc = (
        f"[0:v:{idx}]trim=0:{start:.3f},setpts=PTS-STARTPTS[v0];"
        f"[0:v:{idx}]trim=start={end:.3f},setpts=PTS-STARTPTS[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[vout]"
    )

    with tempfile.TemporaryDirectory(prefix="donatello-cutv-") as tmp:
        interim = Path(tmp) / "single.mkv"
        _run(
            [
                ff,
                "-hide_banner",
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                fc,
                "-map",
                "[vout]",
                "-map",
                "0:a?",
                "-map",
                "0:s?",
                "-c:v",
                "libx265",
                "-pix_fmt",
                "yuv420p10le",
                "-tag:v",
                "hvc1",
                "-c:a",
                "copy",
                "-c:s",
                "copy",
                str(interim),
            ],
            label="single video cut",
        )
        _finalize(interim, output, edits)


def cut_out_single_audio(
    source: Path,
    output: Path,
    cut: CutRange,
    track: MediaTrack,
    audio_tracks: list[MediaTrack],
    *,
    duration: float | None = None,
    edits: list[TrackEditState] | None = None,
) -> None:
    """Remove [start, end) from one audio track; copy video + other audio + subs."""
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if track.kind != "audio":
        raise CutError("Single audio cut requires an audio track.")

    start, end = cut.start, cut.end
    if duration is not None and end > duration + 0.05:
        end = duration

    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    idx = track.type_index
    fc = (
        f"[0:a:{idx}]atrim=0:{start:.3f},asetpts=PTS-STARTPTS[a0];"
        f"[0:a:{idx}]atrim=start={end:.3f},asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[aout]"
    )

    with tempfile.TemporaryDirectory(prefix="donatello-cuta-") as tmp:
        interim = Path(tmp) / "single.mkv"
        cmd = [
            ff,
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            fc,
            "-map",
            "0:v?",
        ]
        for a in sorted(audio_tracks, key=lambda t: t.type_index):
            if a.type_index == idx:
                cmd.extend(["-map", "[aout]"])
            else:
                cmd.extend(["-map", f"0:a:{a.type_index}"])
        cmd.extend(["-map", "0:s?", "-c:v", "copy", "-c:a", "ac3", "-c:s", "copy", str(interim)])
        _run(cmd, label="single audio cut")
        _finalize(interim, output, edits)


def perform_cut(
    source: Path,
    output: Path,
    cut: CutRange,
    *,
    duration: float | None = None,
    edits: list[TrackEditState] | None = None,
    selected_track: MediaTrack | None = None,
    audio_tracks: list[MediaTrack] | None = None,
) -> None:
    """
    Default (selected_track is None): all streams together.
    With selected_track: single-stream cut (video/audio only).
    """
    if selected_track is None:
        cut_out_all_streams(
            source, output, cut, duration=duration, edits=edits
        )
        return
    if selected_track.kind == "video":
        cut_out_single_video(
            source, output, cut, selected_track, duration=duration, edits=edits
        )
        return
    if selected_track.kind == "audio":
        cut_out_single_audio(
            source,
            output,
            cut,
            selected_track,
            audio_tracks or [selected_track],
            duration=duration,
            edits=edits,
        )
        return
    raise CutError(
        "Single-stream subtitle cut is not supported yet. "
        "Use Cut without Ctrl (all streams), or select a video/audio track."
    )


def flatten_edit_decision(
    edl: "EditDecision",
    output: Path,
    *,
    edits: list[TrackEditState] | None = None,
) -> None:
    """Bake an EDL to a single MKV (stream-copy segments + concat)."""
    from src.core.sequence import EditDecision  # local import avoids cycles

    if not isinstance(edl, EditDecision):
        raise CutError("Invalid edit decision.")
    if not edl.segments:
        raise CutError("Edit decision has no segments to flatten.")

    output = Path(output).expanduser().resolve()
    if output.suffix.lower() != ".mkv":
        raise CutError("Flatten output must be an .mkv file.")
    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()

    with tempfile.TemporaryDirectory(prefix="donatello-flatten-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for i, seg in enumerate(edl.segments):
            source = Path(seg.source).expanduser().resolve()
            if not source.is_file():
                raise CutError(f"Segment source not found: {source}")
            if source == output:
                raise CutError("Flatten output must differ from segment sources.")
            part = tmp_path / f"part{i:04d}.mkv"
            _extract_segment(
                ff,
                source,
                part,
                start=seg.src_in if seg.src_in > 0.02 else None,
                duration=seg.duration,
                label=f"flatten {i}",
            )
            parts.append(part)

        interim = tmp_path / "joined.mkv"
        _concat_copy(ff, parts, interim)
        _finalize(interim, output, edits)
