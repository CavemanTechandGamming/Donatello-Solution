"""Edit decision list (EDL) — ordered source spans for the timeline sequence.

Segment drag and non-destructive Cut need this model. Today most edits still
bake a whole MKV; the EDL is the working representation for multi-chunk UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TimelineSegment:
    """One contiguous piece of a source file on the sequence timeline."""

    source: Path
    src_in: float
    src_out: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        src_in = max(0.0, float(self.src_in))
        src_out = max(src_in, float(self.src_out))
        object.__setattr__(self, "src_in", src_in)
        object.__setattr__(self, "src_out", src_out)

    @property
    def duration(self) -> float:
        return max(0.0, self.src_out - self.src_in)


@dataclass
class EditDecision:
    """Ordered list of segments that make up the working timeline."""

    segments: list[TimelineSegment] = field(default_factory=list)

    def timeline_duration(self) -> float:
        return sum(seg.duration for seg in self.segments)

    def timeline_spans(
        self,
    ) -> list[tuple[float, float, TimelineSegment]]:
        """Return (timeline_start, timeline_end, segment) for each piece."""
        spans: list[tuple[float, float, TimelineSegment]] = []
        t = 0.0
        for seg in self.segments:
            end = t + seg.duration
            spans.append((t, end, seg))
            t = end
        return spans

    @classmethod
    def single_clip(cls, source: Path, duration: float) -> EditDecision:
        dur = max(0.0, float(duration))
        return cls(segments=[TimelineSegment(source=source, src_in=0.0, src_out=dur)])

    def remove_range(self, start: float, end: float) -> EditDecision:
        """Return a new EDL with [start, end) removed from the timeline (gap closed)."""
        start = max(0.0, float(start))
        end = max(start, float(end))
        if end <= start or not self.segments:
            return EditDecision(segments=list(self.segments))

        kept: list[TimelineSegment] = []
        for tl_start, tl_end, seg in self.timeline_spans():
            if tl_end <= start or tl_start >= end:
                kept.append(seg)
                continue
            # Overlap — keep head and/or tail of this segment
            if tl_start < start:
                head_dur = start - tl_start
                kept.append(
                    TimelineSegment(
                        source=seg.source,
                        src_in=seg.src_in,
                        src_out=seg.src_in + head_dur,
                    )
                )
            if tl_end > end:
                tail_offset = end - tl_start
                kept.append(
                    TimelineSegment(
                        source=seg.source,
                        src_in=seg.src_in + tail_offset,
                        src_out=seg.src_out,
                    )
                )
        return EditDecision(segments=kept)

    def split_at(self, time: float) -> EditDecision | None:
        """
        Razor: split the covering segment at timeline *time*.
        Returns ``None`` if already on a boundary / ends (no change).
        """
        if not self.segments:
            return None
        dur = self.timeline_duration()
        if dur <= 0:
            return None
        time = max(0.0, min(float(time), dur))
        if time <= 1e-3 or time >= dur - 1e-3:
            return None
        for tl_start, tl_end, _seg in self.timeline_spans():
            if abs(time - tl_start) < 1e-3 or abs(time - tl_end) < 1e-3:
                return None
        out: list[TimelineSegment] = []
        did_split = False
        for tl_start, tl_end, seg in self.timeline_spans():
            if tl_start < time < tl_end:
                head_dur = time - tl_start
                out.append(
                    TimelineSegment(
                        source=seg.source,
                        src_in=seg.src_in,
                        src_out=seg.src_in + head_dur,
                    )
                )
                out.append(
                    TimelineSegment(
                        source=seg.source,
                        src_in=seg.src_in + head_dur,
                        src_out=seg.src_out,
                    )
                )
                did_split = True
            else:
                out.append(seg)
        if not did_split:
            return None
        return EditDecision(segments=out)

    def insert_at(self, time: float, piece: TimelineSegment) -> EditDecision:
        """Insert *piece* at timeline *time*, splitting a covering segment if needed."""
        time = max(0.0, float(time))
        if piece.duration <= 0:
            return EditDecision(segments=list(self.segments))
        if not self.segments:
            return EditDecision(segments=[piece])

        out: list[TimelineSegment] = []
        inserted = False
        for tl_start, tl_end, seg in self.timeline_spans():
            if inserted:
                out.append(seg)
                continue
            if time <= tl_start + 1e-9:
                out.append(piece)
                out.append(seg)
                inserted = True
                continue
            if time >= tl_end - 1e-9:
                out.append(seg)
                continue
            # Split covering segment
            head_dur = time - tl_start
            out.append(
                TimelineSegment(
                    source=seg.source,
                    src_in=seg.src_in,
                    src_out=seg.src_in + head_dur,
                )
            )
            out.append(piece)
            out.append(
                TimelineSegment(
                    source=seg.source,
                    src_in=seg.src_in + head_dur,
                    src_out=seg.src_out,
                )
            )
            inserted = True
        if not inserted:
            out.append(piece)
        return EditDecision(segments=out)

    def move_segment(self, from_index: int, to_index: int) -> EditDecision:
        """Reorder a segment; *to_index* is the destination index after removal."""
        if not self.segments:
            return EditDecision(segments=[])
        n = len(self.segments)
        if from_index < 0 or from_index >= n:
            return EditDecision(segments=list(self.segments))
        segs = list(self.segments)
        piece = segs.pop(from_index)
        dest = max(0, min(len(segs), int(to_index)))
        segs.insert(dest, piece)
        return EditDecision(segments=segs)

    def drop_index_at(self, timeline_t: float, *, moving_index: int) -> int:
        """
        Destination index for dropping the segment at *moving_index* so its
        left edge lands near *timeline_t* (gap-closed timeline).
        """
        if not self.segments:
            return 0
        n = len(self.segments)
        moving_index = max(0, min(n - 1, int(moving_index)))
        # Timeline with the moving segment removed
        others = [s for i, s in enumerate(self.segments) if i != moving_index]
        if not others:
            return 0
        t = max(0.0, float(timeline_t))
        cursor = 0.0
        for i, seg in enumerate(others):
            if t < cursor + seg.duration / 2.0:
                return i
            cursor += seg.duration
        return len(others)

    def is_identity(self, source: Path, file_duration: float) -> bool:
        """True when EDL is a single full-file segment (no real edits yet)."""
        if len(self.segments) != 1:
            return False
        seg = self.segments[0]
        try:
            same = seg.source.resolve() == Path(source).resolve()
        except OSError:
            same = str(seg.source) == str(source)
        if not same:
            return False
        dur = max(0.0, float(file_duration))
        return seg.src_in <= 0.02 and abs(seg.src_out - dur) <= 0.05

    def map_timeline_to_source(self, timeline_t: float) -> tuple[Path, float] | None:
        """Map a timeline time to (source path, source time)."""
        if not self.segments:
            return None
        dur = self.timeline_duration()
        if dur <= 0:
            seg = self.segments[0]
            return (seg.source, seg.src_in)
        t = max(0.0, min(float(timeline_t), dur))
        for tl_start, tl_end, seg in self.timeline_spans():
            if t < tl_end or abs(t - dur) < 1e-9 and abs(tl_end - dur) < 1e-9:
                offset = max(0.0, min(t - tl_start, max(0.0, seg.duration - 1e-6)))
                return (seg.source, seg.src_in + offset)
        last = self.segments[-1]
        return (last.source, max(last.src_in, last.src_out - 1e-6))

    def map_source_to_timeline(self, source: Path, source_t: float) -> float | None:
        """Map a source time to timeline time if it falls inside a segment."""
        source_t = float(source_t)
        for tl_start, _tl_end, seg in self.timeline_spans():
            if not _same_source(seg.source, source):
                continue
            if seg.src_in - 1e-4 <= source_t <= seg.src_out + 1e-4:
                return tl_start + max(0.0, source_t - seg.src_in)
        return None

    def resolve_playback(
        self, source: Path, source_t: float
    ) -> tuple[float, float] | None:
        """
        During preview: if *source_t* is in a kept segment, return
        ``(source_t, timeline_t)``. If it fell into a cut gap, snap to the
        next segment ``(src_in, timeline_start)``. Past the end → ``None``.
        """
        mapped = self.map_source_to_timeline(source, source_t)
        if mapped is not None:
            return (float(source_t), mapped)
        for tl_start, _tl_end, seg in self.timeline_spans():
            if not _same_source(seg.source, source):
                continue
            if seg.src_in >= float(source_t) - 1e-4:
                return (seg.src_in, tl_start)
        return None


def _same_source(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def sequences_to_dict(
    sequences: dict[str, EditDecision],
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for media_key, edl in sequences.items():
        out[media_key] = [
            {
                "source": str(seg.source),
                "src_in": float(seg.src_in),
                "src_out": float(seg.src_out),
            }
            for seg in edl.segments
        ]
    return out


def sequences_from_dict(raw: object) -> dict[str, EditDecision]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, EditDecision] = {}
    for media_key, entries in raw.items():
        if not isinstance(entries, list):
            continue
        segments: list[TimelineSegment] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_raw = entry.get("source")
            if not source_raw:
                continue
            try:
                src_in = float(entry.get("src_in", 0))
                src_out = float(entry.get("src_out", 0))
            except (TypeError, ValueError):
                continue
            if src_out <= src_in:
                continue
            segments.append(
                TimelineSegment(
                    source=Path(str(source_raw)),
                    src_in=src_in,
                    src_out=src_out,
                )
            )
        if segments:
            out[str(media_key)] = EditDecision(segments=segments)
    return out
