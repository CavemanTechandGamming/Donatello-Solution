"""Autosave slot — separate from manual workspace Save (game-style)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.core.logging_setup import app_data_dir, get_logger
from src.core.workspace import WorkspaceError, WorkspaceState, save_workspace

logger = get_logger("autosave")

SAVE_KIND_MANUAL = "manual"
SAVE_KIND_AUTOSAVE = "autosave"

_AUTOSAVE_NAME = "autosave.donatello"
_META_NAME = "autosave-meta.json"


def autosave_dir() -> Path:
    path = app_data_dir() / "autosave"
    path.mkdir(parents=True, exist_ok=True)
    return path


def autosave_workspace_path() -> Path:
    """Fixed recovery file — never the user's manual .donatello path."""
    return autosave_dir() / _AUTOSAVE_NAME


def autosave_meta_path() -> Path:
    return autosave_dir() / _META_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_meta() -> dict:
    path = autosave_meta_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_meta(meta: dict) -> None:
    path = autosave_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def write_autosave(
    state: WorkspaceState,
    *,
    linked_workspace: Path | None = None,
) -> Path:
    """
    Write the autosave slot only.

    Never receives or writes the user's manual workspace path as the output.
    """
    out = autosave_workspace_path()
    written = save_workspace(out, state, save_kind=SAVE_KIND_AUTOSAVE)
    meta = {
        "save_kind": SAVE_KIND_AUTOSAVE,
        "label": "Autosave",
        "saved_at": _now_iso(),
        "clean_quit": False,
        "linked_workspace": (
            str(linked_workspace.expanduser()) if linked_workspace else None
        ),
    }
    _write_meta(meta)
    logger.info("Autosave written → %s (linked=%s)", written, meta["linked_workspace"])
    return written


def mark_clean_quit() -> None:
    """Call on intentional Exit after leave-confirm so next launch does not nag."""
    meta = _read_meta()
    if not meta and not autosave_workspace_path().is_file():
        return
    meta["save_kind"] = SAVE_KIND_AUTOSAVE
    meta["label"] = "Autosave"
    meta["clean_quit"] = True
    meta["clean_quit_at"] = _now_iso()
    _write_meta(meta)
    logger.info("Session marked clean quit (autosave kept, no restore prompt)")


def autosave_exists() -> bool:
    return autosave_workspace_path().is_file()


def last_autosave_at() -> str | None:
    raw = _read_meta().get("saved_at")
    return str(raw) if raw else None


def linked_workspace_from_meta() -> Path | None:
    raw = _read_meta().get("linked_workspace")
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path


def should_offer_crash_restore() -> bool:
    """True when an autosave exists and the last session did not exit cleanly."""
    if not autosave_exists():
        return False
    meta = _read_meta()
    if meta.get("clean_quit") is True:
        return False
    return True


def load_autosave_state() -> WorkspaceState:
    from src.core.workspace import load_workspace

    path = autosave_workspace_path()
    if not path.is_file():
        raise WorkspaceError("No autosave found.")
    return load_workspace(path)
