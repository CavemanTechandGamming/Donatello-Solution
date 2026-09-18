"""Themed About window (matches dark CustomTkinter chrome)."""

from __future__ import annotations

import customtkinter as ctk

from src import __version__


class AboutDialog(ctk.CTkToplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("About Donatello Solution")
        self.geometry("400x200")
        self.minsize(360, 180)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.focus_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Donatello Solution {__version__}",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))

        ctk.CTkLabel(
            self,
            text="Timeline editor for MKV files.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=1, column=0, sticky="new", padx=20, pady=(0, 12))

        ctk.CTkButton(self, text="Close", width=100, command=self._close).grid(
            row=2, column=0, pady=(4, 18)
        )

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        self.grab_release()
        self.destroy()


def open_about(master) -> None:
    AboutDialog(master)
