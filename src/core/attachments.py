"""Preserve Matroska attachments (fonts, etc.) through edits and Export."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from src.core.ffmpeg_paths import FFmpegBootstrapError, ensure_ffmpeg_on_path, ffmpeg_binary, ffprobe_binary
from src.core.ffmpeg_run import FFmpegRunError, run_ffmpeg
from src.core.job_progress import JobProgress
from src.core.logging_setup import get_logger

logger = get_logger("attachments")


class AttachmentError(Exception):
    """Raised when attachment merge / probe fails."""


def count_attachments(path: Path) -> int:
    """How many attachment streams are in *path* (0 if none or unreadable)."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return 0
    try:
        ensure_ffmpeg_on_path()
        probe_bin = ffprobe_binary()
    except FFmpegBootstrapError:
        return 0

    cmd = [
        probe_bin,
        "-v",
        "error",
        "-select_streams",
        "t",
        "-show_entries",
        "stream=index",
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
    except OSError:
        return 0
    if completed.returncode != 0:
        return 0
    try:
        info = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return 0
    return len(info.get("streams") or [])


def unique_existing(paths: Iterable[Path]) -> list[Path]:
    """Dedupe resolved paths that exist on disk (order preserved)."""
    seen: set[str] = set()
    out: list[Path] = []
    for raw in paths:
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if not path.is_file():
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def sources_with_attachments(paths: Iterable[Path]) -> list[Path]:
    """Subset of *paths* that actually contain attachment streams."""
    return [p for p in unique_existing(paths) if count_attachments(p) > 0]


def map_attachment_args(input_index: int = 0) -> list[str]:
    """Optional attachment map for selective ffmpeg outputs (``N:t?``)."""
    return ["-map", f"{input_index}:t?"]


def media_map_args(input_index: int = 0, *, attachments: bool = False) -> list[str]:
    """
    Map video/audio/subtitle from input *input_index*.

    When *attachments* is True, also map attachment streams.
    """
    args = [
        "-map",
        f"{input_index}:v?",
        "-map",
        f"{input_index}:a?",
        "-map",
        f"{input_index}:s?",
    ]
    if attachments:
        args.extend(map_attachment_args(input_index))
    return args


def merge_attachments_onto(
    media: Path,
    output: Path,
    attachment_sources: Sequence[Path],
    *,
    progress: JobProgress | None = None,
) -> None:
    """
    Remux *media* streams and copy attachment streams from *attachment_sources*.

    If no source has attachments, copies *media* to *output* (or no-ops when same path).
    """
    media = Path(media).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    sources = sources_with_attachments(attachment_sources)

    if not media.is_file():
        raise AttachmentError(f"Media not found: {media}")

    if not sources:
        if media != output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(media.read_bytes())
        return

    try:
        ff = ffmpeg_binary()
    except FFmpegBootstrapError as exc:
        raise AttachmentError(str(exc)) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    # Write via temp when overwriting media in place.
    dest = output
    tmp: Path | None = None
    if output == media:
        tmp = output.with_suffix(".donatello-att.tmp.mkv")
        dest = tmp

    cmd: list[str] = [ff, "-hide_banner", "-y", "-i", str(media)]
    for src in sources:
        cmd.extend(["-i", str(src)])
    cmd.extend(["-map", "0", "-c", "copy", "-map_chapters", "0"])
    for i in range(1, len(sources) + 1):
        cmd.extend(["-map", f"{i}:t?"])
    cmd.append(str(dest))

    total = sum(count_attachments(s) for s in sources)
    logger.info(
        "Merging attachments: %d source(s), ~%d attachment stream(s) → %s",
        len(sources),
        total,
        output.name,
    )
    if progress is not None:
        progress.status("Copying attachments…", None)

    try:
        run_ffmpeg(cmd, label="attachments", progress=progress)
    except FFmpegRunError as exc:
        if tmp is not None and tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise AttachmentError(str(exc)) from exc

    if tmp is not None:
        tmp.replace(output)
