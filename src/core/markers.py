"""Named timeline markers — chapter points and Export-multiple split points."""

from __future__ import annotations

import re
from dataclasses import dataclass

MARKER_KIND_CHAPTER = "chapter"
MARKER_KIND_SPLIT = "split"
MARKER_KINDS = (MARKER_KIND_CHAPTER, MARKER_KIND_SPLIT)


@dataclass
class TimelineMarker:
    """A named point on the active clip (seconds from start)."""

    time: float
    name: str
    kind: str = MARKER_KIND_CHAPTER

    def __post_init__(self) -> None:
        self.time = max(0.0, float(self.time))
        kind = (self.kind or MARKER_KIND_CHAPTER).strip().lower()
        if kind not in MARKER_KINDS:
            kind = MARKER_KIND_CHAPTER
        self.kind = kind
        default = "Split" if self.kind == MARKER_KIND_SPLIT else "Chapter"
        self.name = (self.name or "").strip() or default


@dataclass
class ExportMultipleSegment:
    """One output file from Export multiple."""

    start: float
    end: float
    stem: str  # filename without path or .mkv


def markers_from_list(raw: object) -> list[TimelineMarker]:
    if not isinstance(raw, list):
        return []
    out: list[TimelineMarker] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item.get("time", 0))
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind") or MARKER_KIND_CHAPTER).strip().lower()
        if kind not in MARKER_KINDS:
            kind = MARKER_KIND_CHAPTER
        default = "Split" if kind == MARKER_KIND_SPLIT else "Chapter"
        name = str(item.get("name") or default).strip() or default
        out.append(TimelineMarker(time=t, name=name, kind=kind))
    out.sort(key=lambda m: (m.time, m.kind, m.name.lower()))
    return out


def markers_to_list(markers: list[TimelineMarker]) -> list[dict]:
    ordered = sorted(markers, key=lambda m: (m.time, m.kind, m.name.lower()))
    return [{"time": m.time, "name": m.name, "kind": m.kind} for m in ordered]


def chapter_markers(markers: list[TimelineMarker]) -> list[TimelineMarker]:
    return [m for m in markers if m.kind == MARKER_KIND_CHAPTER]


def split_markers(markers: list[TimelineMarker]) -> list[TimelineMarker]:
    return [m for m in markers if m.kind == MARKER_KIND_SPLIT]


def sanitize_filename_stem(name: str) -> str:
    """Make a marker title safe as an .mkv basename (no extension)."""
    text = (name or "").strip()
    text = text.replace("\n", " ").replace("\r", "")
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    text = re.sub(r"\s+", " ", text).strip(" .")
    if text.lower().endswith(".mkv"):
        text = text[:-4].rstrip(" .")
    return text or "untitled"


def unique_stems(stems: list[str]) -> list[str]:
    """Ensure stems are unique within the batch (suffix _2, _3, …)."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in stems:
        base = sanitize_filename_stem(raw)
        key = base.lower()
        count = seen.get(key, 0) + 1
        seen[key] = count
        out.append(base if count == 1 else f"{base}_{count}")
    return out


def build_export_multiple_segments(
    markers: list[TimelineMarker],
    *,
    duration: float,
    mark_in: float | None = None,
    mark_out: float | None = None,
) -> list[ExportMultipleSegment]:
    """
    Build output ranges for Export multiple.

    Mark In/Out (when set) become virtual boundary splits first (export-time only).
    Named split markers inside that window cut files apart. Out is end-only.
    Without In/Out, user splits alone: discard before first; last → duration.
    """
    dur = max(0.0, float(duration or 0.0))
    if dur <= 0:
        return []

    has_bounds = mark_in is not None or mark_out is not None
    window_start = float(mark_in) if mark_in is not None else 0.0
    window_end = float(mark_out) if mark_out is not None else dur
    window_start = max(0.0, min(window_start, dur))
    window_end = max(0.0, min(window_end, dur))
    if has_bounds and window_end <= window_start + 1e-6:
        return []

    splits = sorted(split_markers(markers), key=lambda m: m.time)

    # time → preferred stem (user split names win)
    named_at: dict[int, str] = {}

    def _ms(t: float) -> int:
        return int(round(float(t) * 1000))

    if has_bounds:
        for mark in splits:
            if mark.time < window_start - 1e-3:
                continue
            if mark.time > window_end + 1e-3:
                continue
            named_at[_ms(mark.time)] = mark.name
        boundary_times = [window_start]
        for mark in splits:
            if mark.time < window_start + 1e-3:
                continue
            if mark.time > window_end - 1e-3:
                continue
            boundary_times.append(mark.time)
        boundary_times.append(window_end)
    else:
        if not splits:
            return []
        for mark in splits:
            named_at[_ms(mark.time)] = mark.name
        boundary_times = [m.time for m in splits]
        boundary_times.append(dur)

    # Collapse near-duplicate boundaries
    unique_times: list[float] = []
    for t in sorted(boundary_times):
        if unique_times and abs(unique_times[-1] - t) < 0.001:
            continue
        unique_times.append(t)

    if len(unique_times) < 2:
        return []

    raw_stems: list[str] = []
    ranges: list[tuple[float, float]] = []
    part_n = 0
    for i in range(len(unique_times) - 1):
        start = unique_times[i]
        end = unique_times[i + 1]
        if end <= start + 1e-6:
            continue
        name = named_at.get(_ms(start))
        if not name:
            part_n += 1
            name = f"Part {part_n}"
        ranges.append((start, end))
        raw_stems.append(name)

    stems = unique_stems(raw_stems)
    return [
        ExportMultipleSegment(start=s, end=e, stem=stem)
        for (s, e), stem in zip(ranges, stems)
    ]


def escape_ffmetadata(value: str) -> str:
    """Escape a value for an FFMETADATA file."""
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\n", " ")
        .replace("\r", "")
    )


def build_chapters_ffmetadata(
    markers: list[TimelineMarker],
    duration: float,
) -> str:
    """
    Build an FFMETADATA1 chapter list from chapter markers only.

    Each marker starts a chapter; the chapter ends at the next marker
    (or at *duration* for the last one). Split markers are ignored.
    """
    markers = chapter_markers(markers)
    if not markers:
        return ";FFMETADATA1\n"

    dur = max(0.0, float(duration or 0.0))
    ordered = sorted(markers, key=lambda m: m.time)
    # Collapse markers that land on the same millisecond
    unique: list[TimelineMarker] = []
    for marker in ordered:
        if unique and abs(unique[-1].time - marker.time) < 0.001:
            unique[-1] = TimelineMarker(
                time=unique[-1].time, name=marker.name, kind=MARKER_KIND_CHAPTER
            )
        else:
            unique.append(
                TimelineMarker(time=marker.time, name=marker.name, kind=MARKER_KIND_CHAPTER)
            )

    lines = [";FFMETADATA1"]
    for i, marker in enumerate(unique):
        start_ms = int(round(marker.time * 1000))
        if i + 1 < len(unique):
            end_ms = int(round(unique[i + 1].time * 1000))
        else:
            end_ms = int(round(dur * 1000)) if dur > 0 else start_ms + 1
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={escape_ffmetadata(marker.name)}")
    lines.append("")
    return "\n".join(lines)
