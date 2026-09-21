"""Undo / redo checkpoints for timeline edits."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.core.export_metadata import TrackEditState
from src.core.markers import TimelineMarker
from src.core.sequence import EditDecision, TimelineSegment


@dataclass
class TimelineCheckpoint:
    """Snapshot of the active clip — EDL, marks, markers, track edits, volumes."""

    media_key: str
    segments: list[TimelineSegment]
    markers: list[TimelineMarker]
    mark_in: float | None
    mark_out: float | None
    playhead: float
    track_edits: dict[int, TrackEditState] = field(default_factory=dict)
    audio_volumes: dict[int, float] = field(default_factory=dict)

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


def copy_track_edits(
    edits: dict[int, TrackEditState],
) -> dict[int, TrackEditState]:
    return {
        int(stream_index): TrackEditState(
            kind=edit.kind,
            stream_index=edit.stream_index,
            type_index=edit.type_index,
            title=edit.title,
            language=edit.language,
            is_default=bool(edit.is_default),
            is_forced=bool(edit.is_forced),
        )
        for stream_index, edit in edits.items()
    }


def copy_audio_volumes(volumes: dict[int, float]) -> dict[int, float]:
    return {
        int(stream_index): float(max(0.0, min(2.0, vol)))
        for stream_index, vol in volumes.items()
    }
