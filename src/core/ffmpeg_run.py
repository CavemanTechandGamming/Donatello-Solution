"""Cancelable FFmpeg subprocess with optional progress parsing."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from src.core.job_progress import JobCancelled, JobProgress
from src.core.logging_setup import get_logger

logger = get_logger("ffmpeg")


class FFmpegRunError(Exception):
    """FFmpeg exited non-zero or could not be started."""


def _inject_progress(cmd: list[str]) -> list[str]:
    """Insert ``-nostats -progress pipe:1`` before the output path."""
    if "-progress" in cmd:
        return list(cmd)
    out = list(cmd)
    if len(out) < 2:
        return out
    out[-1:-1] = ["-nostats", "-progress", "pipe:1"]
    return out


def _parse_out_time_seconds(line: str) -> float | None:
    line = line.strip()
    if line.startswith("out_time_ms="):
        raw = line.split("=", 1)[1].strip()
        try:
            # FFmpeg names this *_ms but the unit is microseconds.
            return int(raw) / 1_000_000.0
        except ValueError:
            return None
    if line.startswith("out_time="):
        raw = line.split("=", 1)[1].strip()
        # HH:MM:SS.microseconds
        try:
            parts = raw.split(":")
            if len(parts) != 3:
                return None
            h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
            return h * 3600 + m * 60 + s
        except ValueError:
            return None
    return None


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            pass


def run_ffmpeg(
    cmd: list[str],
    *,
    label: str,
    progress: JobProgress | None = None,
    duration_hint: float | None = None,
) -> None:
    """
    Run an FFmpeg command. Raises ``JobCancelled`` if *progress* is cancelled,
    ``FFmpegRunError`` on failure.
    """
    if progress is not None:
        progress.check()

    full = _inject_progress(cmd) if progress is not None else list(cmd)
    logger.debug("ffmpeg %s: %s", label, " ".join(full))

    # Prefer stage-mapped ticks. Stream-copy often never emits out_time —
    # caller end_stage() still advances the bar when the step finishes.
    if progress is not None:
        progress.tick(0.0, message=label)

    try:
        proc = subprocess.Popen(
            full,
            stdout=subprocess.PIPE if progress is not None else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        logger.exception("Failed to run ffmpeg (%s)", label)
        raise FFmpegRunError(f"Failed to run ffmpeg ({label}): {exc}") from exc

    stderr_chunks: list[str] = []
    last_frac = 0.0

    def _read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    err_thread = threading.Thread(target=_read_stderr, name=f"ff-err-{label}", daemon=True)
    err_thread.start()

    try:
        if progress is not None and proc.stdout is not None:
            for line in proc.stdout:
                if progress.cancel.is_set():
                    _terminate(proc)
                    raise JobCancelled("Cancelled.")
                t = _parse_out_time_seconds(line)
                if t is not None and duration_hint and duration_hint > 0:
                    frac = min(0.99, t / duration_hint)
                    if frac >= last_frac + 0.01 or frac >= 0.99:
                        last_frac = frac
                        progress.tick(frac, message=label)
                elif line.startswith("progress=end"):
                    progress.tick(1.0, message=label)
        else:
            while proc.poll() is None:
                if progress is not None and progress.cancel.is_set():
                    _terminate(proc)
                    raise JobCancelled("Cancelled.")
                time.sleep(0.05)
    except JobCancelled:
        _terminate(proc)
        err_thread.join(timeout=1.0)
        raise

    code = proc.wait()
    err_thread.join(timeout=2.0)
    detail = "".join(stderr_chunks).strip()

    if progress is not None and progress.cancel.is_set():
        raise JobCancelled("Cancelled.")

    if code != 0:
        logger.error(
            "FFmpeg %s failed (exit %s)\n%s",
            label,
            code,
            detail[-4000:] if detail else "(no output)",
        )
        lines = [ln for ln in detail.splitlines() if ln.strip()]
        useful = "\n".join(lines[-6:]) if lines else f"exit {code}"
        raise FFmpegRunError(f"FFmpeg {label} failed:\n{useful}")

    if progress is not None:
        progress.tick(1.0, message=label)


def safe_unlink(*paths: Path | None) -> None:
    """Best-effort delete of partial outputs after cancel or failure."""
    for path in paths:
        if path is None:
            continue
        try:
            p = Path(path)
            if p.is_file():
                p.unlink()
        except OSError:
            logger.debug("Could not remove %s", path, exc_info=True)
