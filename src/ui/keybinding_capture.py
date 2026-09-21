"""Capture a single keyboard shortcut for Settings → Keyboard."""

from __future__ import annotations

import customtkinter as ctk

from src.core.keybindings import chord_to_label, event_to_chord, find_conflict
from src.ui import dialogs


class CaptureKeyDialog(ctk.CTkToplevel):
    """Modal: press a key to bind, Esc to cancel."""

    def __init__(self, master, *, action_label: str, action_id: str) -> None:
        super().__init__(master)
        self.title("Set shortcut")
        self.geometry("420x180")
        self.minsize(380, 160)
        self.resizable(False, False)
        self.result: str | None = None  # chord, or None if cancelled
        self._action_id = action_id
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_force()

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=action_label,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))

        ctk.CTkLabel(
            self,
            text="Press a key combination…\nEsc cancels.",
            anchor="w",
            justify="left",
            text_color=("gray30", "gray70"),
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        self._preview = ctk.CTkLabel(
            self,
            text="",
            anchor="center",
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self._preview.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))

        self.bind("<KeyPress>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _on_key(self, event) -> str:
        keysym = str(getattr(event, "keysym", "") or "")
        if keysym == "Escape":
            self._cancel()
            return "break"

        chord = event_to_chord(event)
        if not chord:
            return "break"

        self._preview.configure(text=chord_to_label(chord))
        conflict = find_conflict(chord, excluding=self._action_id)
        if conflict is not None:
            ok = dialogs.ask_yes_no(
                "Shortcut in use",
                f"{chord_to_label(chord)} is already used by “{conflict.label}”.\n\n"
                "Clear that binding and use it here?",
                parent=self,
                ok_text="Replace",
            )
            if not ok:
                self._preview.configure(text="")
                return "break"
            from src.core.keybindings import clear_binding

            clear_binding(conflict.id)

        self.result = chord
        self._finish()
        return "break"

    def _cancel(self) -> None:
        self.result = None
        self._finish()

    def _finish(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def capture_key(parent, *, action_label: str, action_id: str) -> str | None:
    dlg = CaptureKeyDialog(parent, action_label=action_label, action_id=action_id)
    dlg.wait_window()
    return dlg.result
