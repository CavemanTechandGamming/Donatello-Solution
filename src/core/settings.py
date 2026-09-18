"""
Persisted user settings — default workspace save / export folders, last import dir.

Stored under the user's app data directory — not in the repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "DonatelloSolution" / "settings.json"


def load_settings() -> dict[str, Any]:
    path = _settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _existing_dir(raw: object) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path if path.is_dir() else None


def _plain_dir(raw: object) -> Path | None:
    """Return a path even if it does not exist yet (for filedialog initialdir)."""
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    text = str(path).strip()
    if not text or text in (".", ""):
        return None
    return path


def get_default_workspace_dir() -> Path | None:
    return _existing_dir(load_settings().get("default_workspace_dir"))


def get_default_export_dir() -> Path | None:
    return _existing_dir(load_settings().get("default_export_dir"))


def set_default_workspace_dir(directory: Path | str) -> Path:
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    settings = load_settings()
    settings["default_workspace_dir"] = str(directory)
    save_settings(settings)
    return directory


def set_default_export_dir(directory: Path | str) -> Path:
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    settings = load_settings()
    settings["default_export_dir"] = str(directory)
    save_settings(settings)
    return directory


def clear_default_workspace_dir() -> None:
    settings = load_settings()
    settings.pop("default_workspace_dir", None)
    save_settings(settings)


def clear_default_export_dir() -> None:
    settings = load_settings()
    settings.pop("default_export_dir", None)
    save_settings(settings)


def get_last_import_dir() -> Path | None:
    return _plain_dir(load_settings().get("last_import_dir"))


def set_last_import_dir(directory: Path | str) -> None:
    directory = Path(directory).expanduser()
    if not str(directory).strip() or str(directory) in (".", ""):
        return
    settings = load_settings()
    settings["last_import_dir"] = str(directory)
    save_settings(settings)


def workspace_dialog_initialdir() -> str | None:
    """Folder Save/Open workspace dialogs should start in."""
    path = get_default_workspace_dir() or get_last_import_dir()
    return str(path) if path else None


def export_dialog_initialdir() -> str | None:
    """Folder Export dialogs should start in."""
    path = get_default_export_dir() or get_last_import_dir()
    return str(path) if path else None


def import_dialog_initialdir() -> str | None:
    path = get_last_import_dir() or get_default_workspace_dir()
    return str(path) if path else None


DEFAULT_PREVIEW_SKIP_SECONDS = 5.0


def get_preview_skip_seconds() -> float:
    raw = load_settings().get("preview_skip_seconds", DEFAULT_PREVIEW_SKIP_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_SKIP_SECONDS
    if value < 0.1:
        return 0.1
    if value > 600:
        return 600.0
    return value


def set_preview_skip_seconds(seconds: float | int | str) -> float:
    value = float(seconds)
    if value < 0.1:
        raise ValueError("Skip seconds must be at least 0.1")
    if value > 600:
        raise ValueError("Skip seconds must be at most 600")
    settings = load_settings()
    settings["preview_skip_seconds"] = value
    save_settings(settings)
    return value
