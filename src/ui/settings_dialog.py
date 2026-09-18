"""Settings dialog — default folders, preview skip, open log."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.core import settings as app_settings
from src.core.logging_setup import get_logger, log_path, open_log_file

logger = get_logger("settings_ui")


class SettingsDialog(ctk.CTkToplevel):
    """Modal-ish settings window."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Settings — Donatello Solution")
        self.geometry("600x440")
        self.minsize(520, 400)
        self.resizable(True, False)

        self.transient(master)
        self.grab_set()
        self.focus_set()

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Default folders",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(16, 4))

        ctk.CTkLabel(
            self,
            text="Remembered across sessions. Save / Export dialogs start here.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"),
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 10))

        self._workspace_var = ctk.StringVar(
            value=_display(app_settings.get_default_workspace_dir())
        )
        self._export_var = ctk.StringVar(
            value=_display(app_settings.get_default_export_dir())
        )

        self._add_folder_row(
            row=2,
            label="Workspace save",
            variable=self._workspace_var,
            on_browse=self._browse_workspace,
            on_clear=self._clear_workspace,
        )
        self._add_folder_row(
            row=3,
            label="Export",
            variable=self._export_var,
            on_browse=self._browse_export,
            on_clear=self._clear_export,
        )

        ctk.CTkLabel(
            self,
            text="Preview",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(18, 4))

        ctk.CTkLabel(
            self,
            text="Seconds skip buttons (±) on the preview bar.",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray65"),
            anchor="w",
        ).grid(row=5, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 8))

        ctk.CTkLabel(self, text="Skip seconds", anchor="w", width=120).grid(
            row=6, column=0, sticky="w", padx=(16, 8), pady=6
        )
        self._skip_var = ctk.StringVar(value=str(app_settings.get_preview_skip_seconds()))
        skip_entry = ctk.CTkEntry(self, textvariable=self._skip_var, width=100)
        skip_entry.grid(row=6, column=1, sticky="w", padx=4, pady=6)
        ctk.CTkLabel(
            self,
            text="(0.1 – 600)",
            text_color=("gray40", "gray60"),
            anchor="w",
        ).grid(row=6, column=2, sticky="w", padx=(4, 16))

        ctk.CTkLabel(
            self,
            text="Diagnostics",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=7, column=0, columnspan=3, sticky="ew", padx=16, pady=(18, 4))

        ctk.CTkLabel(
            self,
            text=str(log_path()),
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
            wraplength=520,
        ).grid(row=8, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 6))

        ctk.CTkButton(
            self, text="Open log file", width=140, command=self._open_log
        ).grid(row=9, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 8))

        close = ctk.CTkButton(self, text="Close", width=100, command=self._close)
        close.grid(row=10, column=0, columnspan=3, pady=(16, 16))

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _open_log(self) -> None:
        try:
            path = open_log_file()
            logger.info("Opened log file: %s", path)
        except Exception as exc:
            logger.exception("Could not open log file")
            messagebox.showerror(
                "Log file",
                f"Could not open the log file:\n{exc}\n\n{log_path()}",
                parent=self,
            )

    def _close(self) -> None:
        raw = self._skip_var.get().strip()
        try:
            app_settings.set_preview_skip_seconds(raw)
        except ValueError as exc:
            messagebox.showerror("Invalid skip seconds", str(exc), parent=self)
            return
        self.grab_release()
        self.destroy()

    def _add_folder_row(
        self,
        *,
        row: int,
        label: str,
        variable: ctk.StringVar,
        on_browse,
        on_clear,
    ) -> None:
        ctk.CTkLabel(self, text=label, anchor="w", width=120).grid(
            row=row, column=0, sticky="w", padx=(16, 8), pady=6
        )
        entry = ctk.CTkEntry(self, textvariable=variable, state="readonly")
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=6)
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=row, column=2, sticky="e", padx=(4, 16), pady=6)
        ctk.CTkButton(btns, text="Browse…", width=80, command=on_browse).pack(
            side="left", padx=(0, 6)
        )
        ctk.CTkButton(btns, text="Clear", width=60, command=on_clear).pack(side="left")

    def _browse_workspace(self) -> None:
        initial = app_settings.get_default_workspace_dir()
        chosen = filedialog.askdirectory(
            parent=self,
            title="Default workspace save folder",
            initialdir=str(initial) if initial else None,
        )
        if not chosen:
            return
        try:
            path = app_settings.set_default_workspace_dir(chosen)
        except ValueError as exc:
            messagebox.showerror("Invalid folder", str(exc), parent=self)
            return
        self._workspace_var.set(str(path))

    def _browse_export(self) -> None:
        initial = app_settings.get_default_export_dir()
        chosen = filedialog.askdirectory(
            parent=self,
            title="Default export folder",
            initialdir=str(initial) if initial else None,
        )
        if not chosen:
            return
        try:
            path = app_settings.set_default_export_dir(chosen)
        except ValueError as exc:
            messagebox.showerror("Invalid folder", str(exc), parent=self)
            return
        self._export_var.set(str(path))

    def _clear_workspace(self) -> None:
        app_settings.clear_default_workspace_dir()
        self._workspace_var.set("(not set)")

    def _clear_export(self) -> None:
        app_settings.clear_default_export_dir()
        self._export_var.set("(not set)")


def _display(path: Path | None) -> str:
    return str(path) if path else "(not set)"


def open_settings(master) -> None:
    dialog = SettingsDialog(master)
    master.wait_window(dialog)
