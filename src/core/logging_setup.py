"""Rotating file logger — lives under APPDATA, not the repo."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False
LOGGER_NAME = "donatello"


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "DonatelloSolution"


def log_path() -> Path:
    return app_data_dir() / "donatello.log"


def setup_logging() -> logging.Logger:
    """
    Configure the app logger once.

    Rotating file: ~512 KiB × 3 backups so it cannot grow forever.
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    app_data_dir().mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path(),
        maxBytes=512 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

    # Mirror warnings+ to stderr for console / run.bat sessions
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(
        logging.Formatter("%(levelname)s: %(message)s")
    )
    logger.addHandler(console)

    _configured = True
    logger.info("Logging started (file=%s)", log_path())
    logger.info("Python %s | platform %s", sys.version.split()[0], sys.platform)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Child logger under ``donatello`` (e.g. ``donatello.export``)."""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def open_log_file() -> Path:
    """Ensure the log exists and open it in the OS default viewer."""
    import subprocess

    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.touch()

    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return path


def install_excepthook() -> None:
    """Log any uncaught exception before the process dies."""
    logger = get_logger("crash")

    def _hook(exc_type, exc, tb) -> None:  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.exception("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
