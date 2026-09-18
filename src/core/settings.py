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


DEFAULT_AUDIO_LABEL = "System default"


def list_preview_audio_outputs() -> list[str]:
    """Human-readable output device names (plus System default)."""
    try:
        import sounddevice as sd
    except ImportError:
        return [DEFAULT_AUDIO_LABEL]

    names = [DEFAULT_AUDIO_LABEL]
    seen: set[str] = set()
    try:
        devices = sd.query_devices()
    except Exception:
        return names
    for device in devices:
        try:
            if int(device.get("max_output_channels") or 0) <= 0:
                continue
            name = str(device.get("name") or "").strip()
        except (TypeError, ValueError):
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def get_preview_audio_device() -> str | None:
    """Saved output device name, or None for the OS default."""
    raw = load_settings().get("preview_audio_device")
    if raw is None or raw == "" or raw == DEFAULT_AUDIO_LABEL:
        return None
    return str(raw)


def set_preview_audio_device(name: str | None) -> str | None:
    settings = load_settings()
    if not name or name == DEFAULT_AUDIO_LABEL:
        settings.pop("preview_audio_device", None)
        save_settings(settings)
        return None
    settings["preview_audio_device"] = str(name)
    save_settings(settings)
    return str(name)


def resolve_preview_audio_device() -> int | str | None:
    """
    Value for sounddevice OutputStream ``device=``.

    None → host default (follows the OS default when it changes).
    Otherwise a device index matching the saved name (Donatello-only pin).
    """
    name = get_preview_audio_device()
    if not name:
        return None
    try:
        import sounddevice as sd
    except ImportError:
        return None
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    for index, device in enumerate(devices):
        try:
            if int(device.get("max_output_channels") or 0) <= 0:
                continue
            if str(device.get("name") or "").strip() == name:
                return index
        except (TypeError, ValueError):
            continue
    return None


PREVIEW_SAMPLE_RATES = (44100, 48000, 96000)
DEFAULT_PREVIEW_SAMPLE_RATE = 48000

PREVIEW_DOWNMIX_STEREO = "Stereo"
PREVIEW_DOWNMIX_MONO = "Mono"
PREVIEW_DOWNMIX_KEEP = "Keep channels"
PREVIEW_DOWNMIX_OPTIONS = (
    PREVIEW_DOWNMIX_STEREO,
    PREVIEW_DOWNMIX_MONO,
    PREVIEW_DOWNMIX_KEEP,
)
DEFAULT_PREVIEW_DOWNMIX = PREVIEW_DOWNMIX_STEREO


def get_preview_sample_rate() -> int:
    raw = load_settings().get("preview_sample_rate", DEFAULT_PREVIEW_SAMPLE_RATE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PREVIEW_SAMPLE_RATE
    if value in PREVIEW_SAMPLE_RATES:
        return value
    return DEFAULT_PREVIEW_SAMPLE_RATE


def set_preview_sample_rate(rate: int | str) -> int:
    value = int(rate)
    if value not in PREVIEW_SAMPLE_RATES:
        raise ValueError(f"Sample rate must be one of {', '.join(map(str, PREVIEW_SAMPLE_RATES))}")
    settings = load_settings()
    settings["preview_sample_rate"] = value
    save_settings(settings)
    return value


def get_preview_downmix() -> str:
    raw = load_settings().get("preview_downmix", DEFAULT_PREVIEW_DOWNMIX)
    text = str(raw or DEFAULT_PREVIEW_DOWNMIX).strip()
    if text in PREVIEW_DOWNMIX_OPTIONS:
        return text
    lowered = text.lower()
    if lowered == "mono":
        return PREVIEW_DOWNMIX_MONO
    if lowered in ("keep", "keep channels", "off", "none", "original"):
        return PREVIEW_DOWNMIX_KEEP
    return DEFAULT_PREVIEW_DOWNMIX


def set_preview_downmix(mode: str) -> str:
    text = str(mode or "").strip()
    if text not in PREVIEW_DOWNMIX_OPTIONS:
        raise ValueError("Downmix must be Stereo, Mono, or Keep channels")
    settings = load_settings()
    settings["preview_downmix"] = text
    save_settings(settings)
    return text


def preview_monitor_layout(
    *,
    source_layout: str | None = None,
    source_channels: int | None = None,
) -> tuple[str, int]:
    """Return (PyAV layout name, channel count) for the preview monitor."""
    mode = get_preview_downmix()
    if mode == PREVIEW_DOWNMIX_MONO:
        return "mono", 1
    if mode == PREVIEW_DOWNMIX_KEEP:
        if source_layout and source_channels and source_channels > 0:
            return str(source_layout), int(source_channels)
        if source_channels and source_channels > 0:
            guessed = {
                1: "mono",
                2: "stereo",
                6: "5.1",
                8: "7.1",
            }.get(int(source_channels), "stereo")
            return guessed, int(source_channels)
        return "stereo", 2
    return "stereo", 2
