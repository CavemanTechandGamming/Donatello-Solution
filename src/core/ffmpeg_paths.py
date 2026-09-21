"""
Ensure ffmpeg / ffprobe are available for this process.

Uses the ``static-ffmpeg`` package (installed into the virtualenv) which
downloads platform binaries on first use and prepends them to PATH.
A system-wide FFmpeg install still takes precedence when present.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


class FFmpegBootstrapError(Exception):
    """Raised when bundled or system FFmpeg cannot be made available."""


@dataclass(frozen=True)
class FFmpegCheckResult:
    """Outcome of a first-run / Settings FFmpeg availability check."""

    ok: bool
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    version_line: str | None = None
    error: str | None = None

    def user_message(self) -> str:
        """Plain-language message for a dialog (success or failure)."""
        if self.ok:
            parts = ["FFmpeg is ready."]
            if self.version_line:
                parts.append(self.version_line)
            if self.ffmpeg_path:
                parts.append(f"ffmpeg: {self.ffmpeg_path}")
            if self.ffprobe_path:
                parts.append(f"ffprobe: {self.ffprobe_path}")
            return "\n".join(parts)

        detail = (self.error or "Unknown error.").strip()
        return (
            "Donatello needs FFmpeg (and ffprobe) to open and edit MKVs.\n\n"
            "What to try:\n"
            "1. Stay online and restart Donatello — it can download bundled "
            "FFmpeg on first use (via static-ffmpeg).\n"
            "2. Or install FFmpeg system-wide and make sure both `ffmpeg` and "
            "`ffprobe` are on your PATH.\n"
            "3. Developing from source: "
            "pip install -r requirements/requirements.txt\n\n"
            f"Details:\n{detail}"
        )


@lru_cache(maxsize=1)
def ensure_ffmpeg_on_path() -> None:
    """
    Make ``ffmpeg`` and ``ffprobe`` resolvable via PATH.

    Prefer an existing system install; otherwise fetch/use static-ffmpeg.
    Safe to call multiple times.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    try:
        import static_ffmpeg
    except ImportError as exc:
        raise FFmpegBootstrapError(
            "FFmpeg is not on PATH and the 'static-ffmpeg' package is not installed. "
            "Run: pip install -r requirements/requirements.txt"
        ) from exc

    try:
        # weak=True: keep a real system FFmpeg if already present
        static_ffmpeg.add_paths(weak=True)
    except Exception as exc:  # noqa: BLE001 — surface download / platform errors clearly
        raise FFmpegBootstrapError(
            "Could not download or register bundled FFmpeg binaries. "
            f"Check your network connection and try again.\n\nDetails: {exc}"
        ) from exc

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise FFmpegBootstrapError(
            "Bundled FFmpeg was installed but ffmpeg/ffprobe are still not on PATH."
        )


def ffmpeg_binary() -> str:
    """Return the path/name of the ffmpeg executable."""
    ensure_ffmpeg_on_path()
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegBootstrapError("ffmpeg binary not found after bootstrap.")
    return path


def ffprobe_binary() -> str:
    """Return the path/name of the ffprobe executable."""
    ensure_ffmpeg_on_path()
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegBootstrapError("ffprobe binary not found after bootstrap.")
    return path


def check_ffmpeg() -> FFmpegCheckResult:
    """
    Bootstrap FFmpeg if needed and report whether both binaries are usable.

    Clears the bootstrap cache first so a Settings re-check can pick up a
    newly installed system FFmpeg without restarting Python.
    """
    ensure_ffmpeg_on_path.cache_clear()
    try:
        ensure_ffmpeg_on_path()
    except FFmpegBootstrapError as exc:
        return FFmpegCheckResult(ok=False, error=str(exc))

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if not ffmpeg_path or not ffprobe_path:
        return FFmpegCheckResult(
            ok=False,
            error="ffmpeg/ffprobe still missing after bootstrap.",
        )

    version_line: str | None = None
    try:
        completed = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        first = (completed.stdout or completed.stderr or "").splitlines()
        if first:
            version_line = first[0].strip()
    except Exception as exc:  # noqa: BLE001
        return FFmpegCheckResult(
            ok=False,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            error=f"ffmpeg was found but would not run:\n{exc}",
        )

    return FFmpegCheckResult(
        ok=True,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        version_line=version_line,
    )
