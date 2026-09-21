"""Cancelable long-job progress handle (FFmpeg Export / bake paths)."""

from __future__ import annotations

import threading
from collections.abc import Callable


class JobCancelled(Exception):
    """User aborted a long job; callers must clean temps / partial outputs."""


class JobProgress:
    """
    Thread-safe cancel + stage-based progress for the UI.

    Call ``begin_stage(message, start, end)`` for each discrete step
    (fractions are 0..1 within the *current* scope). Nested work inherits the
    stage as its scope. ``tick(local)`` fills within the open stage; stream-copy
    FFmpeg often never reports time, so ``end_stage()`` snaps the bar forward
    when the step finishes.
    """

    def __init__(
        self,
        cancel: threading.Event,
        report: Callable[[str, float | None], None],
    ) -> None:
        self.cancel = cancel
        self._report = report
        self._scope_lo = 0.0
        self._scope_hi = 1.0
        self._lo = 0.0
        self._hi = 1.0
        self._message = ""
        # (scope_lo, scope_hi, lo, hi, message)
        self._stack: list[tuple[float, float, float, float, str]] = []

    def check(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled("Cancelled.")

    def begin_stage(self, message: str, start: float = 0.0, end: float = 1.0) -> None:
        """Open a stage occupying [start, end] of the current scope."""
        self.check()
        start = max(0.0, min(1.0, float(start)))
        end = max(start, min(1.0, float(end)))
        span = self._scope_hi - self._scope_lo
        new_lo = self._scope_lo + span * start
        new_hi = self._scope_lo + span * end
        self._stack.append(
            (self._scope_lo, self._scope_hi, self._lo, self._hi, self._message)
        )
        self._scope_lo = self._lo = new_lo
        self._scope_hi = self._hi = new_hi
        self._message = message
        self._report(message, new_lo)

    def end_stage(self, message: str | None = None) -> None:
        """Snap to the end of the open stage and restore the parent scope."""
        self.check()
        msg = self._message if message is None else message
        self._report(msg, self._hi)
        if self._stack:
            (
                self._scope_lo,
                self._scope_hi,
                self._lo,
                self._hi,
                self._message,
            ) = self._stack.pop()

    def tick(self, local: float, *, message: str | None = None) -> None:
        """Fill within the open stage (*local* is 0..1)."""
        self.check()
        local = max(0.0, min(1.0, float(local)))
        frac = self._lo + (self._hi - self._lo) * local
        msg = self._message if message is None else message
        if message is not None:
            self._message = message
        self._report(msg, frac)

    def status(self, message: str, fraction: float | None = None) -> None:
        """Absolute overall fraction (0..1), or message-only when *fraction* is None."""
        self.check()
        if fraction is None:
            self._report(message, self._lo)
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        self._message = message
        self._report(message, fraction)

    def complete(self, message: str = "Done") -> None:
        """Force the bar to 100% (call when the whole job succeeds)."""
        self.check()
        self._report(message, 1.0)
