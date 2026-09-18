"""Named timeline markers (chapter points on Export)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimelineMarker:
    """A named point on the active clip (seconds from start)."""

    time: float
    name: str

    def __post_init__(self) -> None:
        self.time = max(0.0, float(self.time))
        self.name = (self.name or "").strip() or "Chapter"


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
        name = str(item.get("name") or "Chapter").strip() or "Chapter"
        out.append(TimelineMarker(time=t, name=name))
    out.sort(key=lambda m: (m.time, m.name.lower()))
    return out


def markers_to_list(markers: list[TimelineMarker]) -> list[dict]:
    ordered = sorted(markers, key=lambda m: (m.time, m.name.lower()))
    return [{"time": m.time, "name": m.name} for m in ordered]


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
    Build an FFMETADATA1 chapter list from markers.

    Each marker starts a chapter; the chapter ends at the next marker
    (or at *duration* for the last one).
    """
    if not markers:
        return ";FFMETADATA1\n"

    dur = max(0.0, float(duration or 0.0))
    ordered = sorted(markers, key=lambda m: m.time)
    # Collapse markers that land on the same millisecond
    unique: list[TimelineMarker] = []
    for marker in ordered:
        if unique and abs(unique[-1].time - marker.time) < 0.001:
            unique[-1] = TimelineMarker(time=unique[-1].time, name=marker.name)
        else:
            unique.append(marker)

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
