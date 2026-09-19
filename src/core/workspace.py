"""Workspace (.donatello) save / load — project state, not Export."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.core.export_metadata import TrackEditState
from src.core.logging_setup import get_logger
from src.core.markers import TimelineMarker, markers_from_list, markers_to_list

logger = get_logger("workspace")

WORKSPACE_VERSION = 1
APP_NAME = "Donatello Solution"


class WorkspaceError(Exception):
    """Raised when a workspace file cannot be read or written."""


@dataclass
class WorkspaceState:
    """In-memory snapshot of a Donatello workspace."""

    media: list[Path] = field(default_factory=list)
    active: Path | None = None
    selected: Path | None = None
    selected_stream_index: int | None = None
    mark_in: float | None = None
    mark_out: float | None = None
    playhead: float | None = None
    # media path key → stream_index → edit
    track_edits: dict[str, dict[int, TrackEditState]] = field(default_factory=dict)
    # media path key → named markers (chapter points)
    markers: dict[str, list[TimelineMarker]] = field(default_factory=dict)
    # media path key → audio stream_index → preview volume 0..2 (100% = 1.0)
    audio_volumes: dict[str, dict[int, float]] = field(default_factory=dict)


def _path_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _edit_from_dict(raw: object) -> TrackEditState | None:
    if not isinstance(raw, dict):
        return None
    try:
        kind = str(raw.get("kind") or "")
        stream_index = int(raw["stream_index"])
        type_index = int(raw.get("type_index", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if kind not in ("video", "audio", "subtitle"):
        return None
    return TrackEditState(
        kind=kind,
        stream_index=stream_index,
        type_index=type_index,
        title=str(raw.get("title") or ""),
        language=str(raw.get("language") or "und").strip() or "und",
        is_default=bool(raw.get("is_default", False)),
        is_forced=bool(raw.get("is_forced", False)),
    )


def state_to_dict(state: WorkspaceState) -> dict:
    track_edits: dict[str, list[dict]] = {}
    for media_key, by_stream in state.track_edits.items():
        track_edits[media_key] = [asdict(e) for e in by_stream.values()]

    markers: dict[str, list[dict]] = {}
    for media_key, marks in state.markers.items():
        markers[media_key] = markers_to_list(marks)

    audio_volumes: dict[str, dict[str, float]] = {}
    for media_key, by_stream in state.audio_volumes.items():
        audio_volumes[media_key] = {
            str(stream_index): float(max(0.0, min(2.0, vol)))
            for stream_index, vol in by_stream.items()
        }

    return {
        "version": WORKSPACE_VERSION,
        "app": APP_NAME,
        "media": [str(p) for p in state.media],
        "active": str(state.active) if state.active else None,
        "selected": str(state.selected) if state.selected else None,
        "selected_stream_index": state.selected_stream_index,
        "mark_in": state.mark_in,
        "mark_out": state.mark_out,
        "playhead": state.playhead,
        "track_edits": track_edits,
        "markers": markers,
        "audio_volumes": audio_volumes,
    }


def state_from_dict(data: object) -> WorkspaceState:
    if not isinstance(data, dict):
        raise WorkspaceError("Workspace file is not a JSON object.")

    version = data.get("version", 1)
    try:
        version_i = int(version)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError(f"Invalid workspace version: {version!r}") from exc
    if version_i > WORKSPACE_VERSION:
        raise WorkspaceError(
            f"Workspace version {version_i} is newer than this app "
            f"(supports up to {WORKSPACE_VERSION})."
        )

    media_raw = data.get("media") or []
    if not isinstance(media_raw, list):
        raise WorkspaceError("'media' must be a list of paths.")

    media: list[Path] = []
    seen: set[str] = set()
    for item in media_raw:
        if not item:
            continue
        path = Path(str(item)).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        media.append(path)

    active_raw = data.get("active")
    selected_raw = data.get("selected")
    active = Path(str(active_raw)).expanduser() if active_raw else None
    selected = Path(str(selected_raw)).expanduser() if selected_raw else None

    edits_out: dict[str, dict[int, TrackEditState]] = {}
    edits_raw = data.get("track_edits") or {}
    if isinstance(edits_raw, dict):
        for media_key, entries in edits_raw.items():
            if not isinstance(entries, list):
                continue
            by_stream: dict[int, TrackEditState] = {}
            for entry in entries:
                edit = _edit_from_dict(entry)
                if edit is not None:
                    by_stream[edit.stream_index] = edit
            if by_stream:
                # Prefer resolved keys when the file still exists
                try:
                    key = _path_key(Path(str(media_key)))
                except OSError:
                    key = str(media_key)
                edits_out[key] = by_stream
                if str(media_key) != key:
                    edits_out[str(media_key)] = by_stream

    markers_out: dict[str, list[TimelineMarker]] = {}
    markers_raw = data.get("markers") or {}
    if isinstance(markers_raw, dict):
        for media_key, entries in markers_raw.items():
            marks = markers_from_list(entries)
            if not marks:
                continue
            try:
                key = _path_key(Path(str(media_key)))
            except OSError:
                key = str(media_key)
            markers_out[key] = marks
            if str(media_key) != key:
                markers_out[str(media_key)] = marks

    volumes_out: dict[str, dict[int, float]] = {}
    volumes_raw = data.get("audio_volumes") or {}
    if isinstance(volumes_raw, dict):
        for media_key, by_stream in volumes_raw.items():
            if not isinstance(by_stream, dict):
                continue
            parsed: dict[int, float] = {}
            for stream_key, vol in by_stream.items():
                try:
                    stream_index = int(stream_key)
                    value = float(vol)
                except (TypeError, ValueError):
                    continue
                parsed[stream_index] = max(0.0, min(2.0, value))
            if not parsed:
                continue
            try:
                key = _path_key(Path(str(media_key)))
            except OSError:
                key = str(media_key)
            volumes_out[key] = parsed
            if str(media_key) != key:
                volumes_out[str(media_key)] = parsed

    return WorkspaceState(
        media=media,
        active=active,
        selected=selected,
        selected_stream_index=_optional_int(data.get("selected_stream_index")),
        mark_in=_optional_float(data.get("mark_in")),
        mark_out=_optional_float(data.get("mark_out")),
        playhead=_optional_float(data.get("playhead")),
        track_edits=edits_out,
        markers=markers_out,
        audio_volumes=volumes_out,
    )


def save_workspace(path: Path, state: WorkspaceState) -> Path:
    path = Path(path).expanduser()
    if path.suffix.lower() not in (".donatello", ".json"):
        path = path.with_suffix(".donatello")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state_to_dict(state)
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.exception("Failed to write workspace %s", path)
        raise WorkspaceError(f"Could not write workspace: {exc}") from exc
    logger.info(
        "Saved workspace %s (%d media, active=%s)",
        path,
        len(state.media),
        state.active.name if state.active else None,
    )
    return path


def load_workspace(path: Path) -> WorkspaceState:
    path = Path(path).expanduser()
    if not path.is_file():
        raise WorkspaceError(f"Workspace not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except OSError as exc:
        logger.exception("Failed to read workspace %s", path)
        raise WorkspaceError(f"Could not read workspace: {exc}") from exc
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in workspace %s: %s", path, exc)
        raise WorkspaceError(f"Workspace is not valid JSON: {exc}") from exc

    state = state_from_dict(data)
    logger.info(
        "Loaded workspace %s (%d media, active=%s)",
        path,
        len(state.media),
        state.active.name if state.active else None,
    )
    return state
