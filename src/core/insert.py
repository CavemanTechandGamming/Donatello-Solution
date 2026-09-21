"""
Insert one MKV into another at a timeline point.

Default: all streams together (stream-copy concat) so sync is preserved.
Single-stream: splice only the selected video/audio track (intentional desync).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.core.cut import (
    CutError,
    _concat_copy,
    _extract_segment,
    _ffmpeg,
    _finalize,
    _run,
)
from src.core.export_metadata import TrackEditState
from src.core.job_progress import JobProgress
from src.core.logging_setup import get_logger
from src.core.probe import MediaTrack

logger = get_logger("insert")


def insert_all_streams(
    base: Path,
    insert: Path,
    output: Path,
    *,
    at: float,
    base_duration: float | None = None,
    edits: list[TrackEditState] | None = None,
    progress: JobProgress | None = None,
) -> None:
    """
    Splice *insert* into *base* at *at* seconds (all streams, stream-copy).

    Result = base[0:at] + insert + base[at:end]
    """
    base = Path(base).expanduser().resolve()
    insert = Path(insert).expanduser().resolve()
    output = Path(output).expanduser().resolve()

    if not base.is_file():
        raise CutError(f"Base clip not found: {base}")
    if not insert.is_file():
        raise CutError(f"Insert clip not found: {insert}")
    if output.suffix.lower() != ".mkv":
        raise CutError("Insert output must be an .mkv file.")
    if base == output or insert == output:
        raise CutError("Insert output must be a new path.")
    if at < 0:
        raise CutError("Insert point must be non-negative.")

    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    point = at
    logger.info(
        "Insert all-streams: base=%s insert=%s at=%.3f → %s",
        base.name,
        insert.name,
        point,
        output,
    )
    if progress is not None:
        progress.status("Inserting…", None)

    with tempfile.TemporaryDirectory(prefix="donatello-ins-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        planned = (
            (1 if point > 0.02 else 0)
            + 1
            + (1 if (base_duration is None or point < (base_duration - 0.02)) else 0)
            + 2
        )
        step = 0

        if point > 0.02:
            if progress is not None:
                progress.begin_stage(
                    "Inserting… (head)", step / planned, (step + 1) / planned
                )
            head = tmp_path / "head.mkv"
            _extract_segment(
                ff,
                base,
                head,
                duration=point,
                label="insert head",
                progress=progress,
            )
            parts.append(head)
            if progress is not None:
                progress.end_stage()
            step += 1

        if progress is not None:
            progress.begin_stage(
                "Inserting… (clip)", step / planned, (step + 1) / planned
            )
        mid = tmp_path / "mid.mkv"
        _extract_segment(ff, insert, mid, label="insert clip", progress=progress)
        parts.append(mid)
        if progress is not None:
            progress.end_stage()
        step += 1

        keep_tail = base_duration is None or point < (base_duration - 0.02)
        if keep_tail:
            if progress is not None:
                progress.begin_stage(
                    "Inserting… (tail)", step / planned, (step + 1) / planned
                )
            tail = tmp_path / "tail.mkv"
            _extract_segment(
                ff,
                base,
                tail,
                start=point,
                label="insert tail",
                progress=progress,
            )
            parts.append(tail)
            if progress is not None:
                progress.end_stage()
            step += 1

        if progress is not None:
            progress.begin_stage("Joining…", step / planned, (step + 1) / planned)
        interim = tmp_path / "joined.mkv"
        _concat_copy(ff, parts, interim, progress=progress)
        if progress is not None:
            progress.end_stage()
            step += 1
            progress.begin_stage("Finishing…", step / planned, 1.0)
        _finalize(interim, output, edits, progress=progress)
        if progress is not None:
            progress.end_stage()


def insert_single_video(
    base: Path,
    insert: Path,
    output: Path,
    *,
    at: float,
    base_track: MediaTrack,
    insert_track: MediaTrack,
    base_duration: float | None = None,
    edits: list[TrackEditState] | None = None,
    progress: JobProgress | None = None,
) -> None:
    """
    Insert *insert*'s video into *base*'s video at *at*; copy base audio/subs.

    Intentional desync: audio/sub lengths stay as base-only.
    """
    del base_duration  # unused; kept for API symmetry with insert_all_streams
    if base_track.kind != "video" or insert_track.kind != "video":
        raise CutError("Single-stream video insert needs a video track on both clips.")

    base = Path(base).expanduser().resolve()
    insert = Path(insert).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()

    bv, iv = base_track.type_index, insert_track.type_index
    point = max(0.0, at)

    fc = (
        f"[0:v:{bv}]trim=0:{point:.3f},setpts=PTS-STARTPTS[v0];"
        f"[1:v:{iv}]setpts=PTS-STARTPTS[v1];"
        f"[0:v:{bv}]trim=start={point:.3f},setpts=PTS-STARTPTS[v2];"
        f"[v0][v1][v2]concat=n=3:v=1:a=0[vout]"
    )
    if progress is not None:
        progress.begin_stage("Inserting video…", 0.0, 0.85)

    with tempfile.TemporaryDirectory(prefix="donatello-insv-") as tmp:
        interim = Path(tmp) / "single.mkv"
        _run(
            [
                ff,
                "-hide_banner",
                "-y",
                "-i",
                str(base),
                "-i",
                str(insert),
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
            label="insert video",
            progress=progress,
        )
        if progress is not None:
            progress.end_stage()
            progress.begin_stage("Finishing…", 0.85, 1.0)
        _finalize(interim, output, edits, progress=progress)
        if progress is not None:
            progress.end_stage()


def insert_single_audio(
    base: Path,
    insert: Path,
    output: Path,
    *,
    at: float,
    base_track: MediaTrack,
    insert_track: MediaTrack,
    base_audio_tracks: list[MediaTrack],
    edits: list[TrackEditState] | None = None,
    progress: JobProgress | None = None,
) -> None:
    """Insert insert's audio into one base audio track at *at*; copy video/other audio/subs."""
    if base_track.kind != "audio" or insert_track.kind != "audio":
        raise CutError("Single-stream audio insert needs an audio track on both clips.")

    base = Path(base).expanduser().resolve()
    insert = Path(insert).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()

    ba, ia = base_track.type_index, insert_track.type_index
    point = max(0.0, at)
    fc = (
        f"[0:a:{ba}]atrim=0:{point:.3f},asetpts=PTS-STARTPTS[a0];"
        f"[1:a:{ia}]asetpts=PTS-STARTPTS[a1];"
        f"[0:a:{ba}]atrim=start={point:.3f},asetpts=PTS-STARTPTS[a2];"
        f"[a0][a1][a2]concat=n=3:v=0:a=1[aout]"
    )
    if progress is not None:
        progress.begin_stage("Inserting audio…", 0.0, 0.85)

    with tempfile.TemporaryDirectory(prefix="donatello-insa-") as tmp:
        interim = Path(tmp) / "single.mkv"
        cmd = [
            ff,
            "-hide_banner",
            "-y",
            "-i",
            str(base),
            "-i",
            str(insert),
            "-filter_complex",
            fc,
            "-map",
            "0:v?",
        ]
        for a in sorted(base_audio_tracks, key=lambda t: t.type_index):
            if a.type_index == ba:
                cmd.extend(["-map", "[aout]"])
            else:
                cmd.extend(["-map", f"0:a:{a.type_index}"])
        cmd.extend(
            ["-map", "0:s?", "-c:v", "copy", "-c:a", "ac3", "-c:s", "copy", str(interim)]
        )
        _run(cmd, label="insert audio", progress=progress)
        if progress is not None:
            progress.end_stage()
            progress.begin_stage("Finishing…", 0.85, 1.0)
        _finalize(interim, output, edits, progress=progress)
        if progress is not None:
            progress.end_stage()


def perform_insert(
    base: Path,
    insert: Path,
    output: Path,
    *,
    at: float,
    base_duration: float | None = None,
    edits: list[TrackEditState] | None = None,
    selected_base_track: MediaTrack | None = None,
    selected_insert_track: MediaTrack | None = None,
    base_audio_tracks: list[MediaTrack] | None = None,
    progress: JobProgress | None = None,
) -> None:
    """Default: all streams. With selected tracks: single-stream video/audio insert."""
    if selected_base_track is None:
        insert_all_streams(
            base,
            insert,
            output,
            at=at,
            base_duration=base_duration,
            edits=edits,
            progress=progress,
        )
        return

    if selected_base_track.kind == "video":
        ins = selected_insert_track
        if ins is None or ins.kind != "video":
            raise CutError(
                "Select a video stream on the insert clip (or use Insert for all streams)."
            )
        insert_single_video(
            base,
            insert,
            output,
            at=at,
            base_track=selected_base_track,
            insert_track=ins,
            base_duration=base_duration,
            edits=edits,
            progress=progress,
        )
        return

    if selected_base_track.kind == "audio":
        ins = selected_insert_track
        if ins is None or ins.kind != "audio":
            raise CutError(
                "Select an audio stream on the insert clip (or use Insert for all streams)."
            )
        insert_single_audio(
            base,
            insert,
            output,
            at=at,
            base_track=selected_base_track,
            insert_track=ins,
            base_audio_tracks=base_audio_tracks or [selected_base_track],
            edits=edits,
            progress=progress,
        )
        return

    raise CutError(
        "Single-stream subtitle insert is not supported. Use Insert (all streams)."
    )
