"""Undo / redo checkpoints for timeline EDL edits."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.markers import TimelineMarker
from src.core.sequence import EditDecision, TimelineSegment


@dataclass
class TimelineCheckpoint:
    """Snapshot of the active clip's sequence + marks (enough to undo EDL ops)."""

    media_key: str
    segments: list[TimelineSegment]
    markers: list[TimelineMarker]
    mark_in: float | None
    mark_out: float | None
    playhead: float

    def edl(self) -> EditDecision:
        return EditDecision(segments=list(self.segments))


@dataclass
class UndoStack:
    """Linear undo/redo history (redo cleared on a new edit)."""

    max_depth: int = 50
    _undo: list[TimelineCheckpoint] = field(default_factory=list)
    _redo: list[TimelineCheckpoint] = field(default_factory=list)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, checkpoint: TimelineCheckpoint) -> None:
        self._undo.append(checkpoint)
        if len(self._undo) > self.max_depth:
            self._undo = self._undo[-self.max_depth :]
        self._redo.clear()

    def undo(self, current: TimelineCheckpoint) -> TimelineCheckpoint | None:
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: TimelineCheckpoint) -> TimelineCheckpoint | None:
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()


def copy_edl(edl: EditDecision) -> list[TimelineSegment]:
    return [
        TimelineSegment(source=Path(s.source), src_in=s.src_in, src_out=s.src_out)
        for s in edl.segments
    ]


def copy_markers(markers: list[TimelineMarker]) -> list[TimelineMarker]:
    return [
        TimelineMarker(time=m.time, name=m.name, kind=m.kind) for m in markers
    ]
