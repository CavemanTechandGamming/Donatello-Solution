"""Themed progress modal for cancelable long jobs."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import customtkinter as ctk

from src.core.job_progress import JobCancelled, JobProgress

T = TypeVar("T")


@dataclass
class JobOutcome:
    """Result of ``run_with_progress``."""

    cancelled: bool
    value: Any = None


class _ProgressDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("420x160")
        self.minsize(380, 140)
        self.resizable(False, False)
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_set()

        self._cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._outcome: tuple[str, object] | None = None  # ok|err|cancel, payload

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))

        self._status = ctk.CTkLabel(
            self,
            text="Starting…",
            anchor="w",
            text_color=("gray30", "gray70"),
        )
        self._status.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))

        self._bar = ctk.CTkProgressBar(self, height=14)
        self._bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        self._bar.set(0)

        ctk.CTkButton(
            self,
            text="Cancel",
            width=100,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=self._request_cancel,
        ).grid(row=3, column=0, pady=(0, 16))

        self.protocol("WM_DELETE_WINDOW", self._request_cancel)
        self.bind("<Escape>", lambda _e: self._request_cancel())
        self.after(50, self._poll)

    def _request_cancel(self) -> None:
        self._cancel.set()
        self._status.configure(text="Cancelling…")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    message, fraction = payload
                    self._status.configure(text=message)
                    if fraction is None:
                        cur = self._bar.get()
                        self._bar.set(0.15 if cur < 0.5 else 0.35)
                    else:
                        self._bar.set(float(fraction))
                elif kind == "done":
                    self._bar.set(1.0)
                    self._outcome = payload
                    self._finish()
                    return
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._poll)

    def _finish(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def start_work(self, work: Callable[[JobProgress], T]) -> None:
        def report(message: str, fraction: float | None) -> None:
            self._queue.put(("status", (message, fraction)))

        def runner() -> None:
            prog = JobProgress(self._cancel, report)
            try:
                result = work(prog)
                self._queue.put(("done", ("ok", result)))
            except JobCancelled:
                self._queue.put(("done", ("cancel", None)))
            except Exception as exc:
                self._queue.put(("done", ("err", exc)))

        threading.Thread(target=runner, name="donatello-job", daemon=True).start()


def run_with_progress(
    parent,
    title: str,
    work: Callable[[JobProgress], T],
) -> JobOutcome:
    """
    Run *work* on a background thread with a cancelable progress dialog.

    ``outcome.cancelled`` is True if the user aborted. Otherwise ``outcome.value``
    holds the return value of *work*. Other exceptions from *work* are re-raised
    after the dialog closes.
    """
    dlg = _ProgressDialog(parent, title)
    dlg.start_work(work)
    dlg.wait_window()
    outcome = dlg._outcome
    if outcome is None:
        return JobOutcome(cancelled=True)
    kind, payload = outcome
    if kind == "cancel":
        return JobOutcome(cancelled=True)
    if kind == "err":
        raise payload  # type: ignore[misc]
    return JobOutcome(cancelled=False, value=payload)
