"""Settings dialog — tabbed Folders / Preview / Keyboard / Audio / Help."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from src.core import settings as app_settings
from src.core.keybindings import (
    ACTIONS,
    chord_to_label,
    clear_binding,
    get_binding,
    reset_all_bindings,
    reset_binding,
    set_binding,
)
from src.core.logging_setup import get_logger, log_path, open_log_file
from src.ui import dialogs
from src.ui.keybinding_capture import capture_key

logger = get_logger("settings_ui")


class SettingsDialog(ctk.CTkToplevel):
    """Modal settings window with layperson-friendly tabs."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Settings — Donatello Solution")
        self.geometry("700x560")
        self.minsize(620, 480)
        self.resizable(True, True)

        self.transient(master)
        self.grab_set()
        self.focus_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        tabs = ctk.CTkTabview(self)
        tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))

        tab_folders = tabs.add("Folders")
        tab_preview = tabs.add("Preview")
        tab_keyboard = tabs.add("Keyboard")
        tab_audio = tabs.add("Audio")
        tab_help = tabs.add("Help")

        self._build_folders_tab(tab_folders)
        self._build_preview_tab(tab_preview)
        self._build_keyboard_tab(tab_keyboard)
        self._build_audio_tab(tab_audio)
        self._build_help_tab(tab_help)

        close = ctk.CTkButton(self, text="Close", width=100, command=self._close)
        close.grid(row=1, column=0, pady=(4, 14))

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_folders_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Where Donatello looks first when you save or export.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 12))

        self._workspace_var = ctk.StringVar(
            value=_display(app_settings.get_default_workspace_dir())
        )
        self._export_var = ctk.StringVar(
            value=_display(app_settings.get_default_export_dir())
        )

        self._add_folder_row(
            tab,
            row=1,
            label="Workspace",
            variable=self._workspace_var,
            on_browse=self._browse_workspace,
            on_clear=self._clear_workspace,
        )
        self._add_folder_row(
            tab,
            row=2,
            label="Export",
            variable=self._export_var,
            on_browse=self._browse_export,
            on_clear=self._clear_export,
        )

        self._warn_multi_var = ctk.BooleanVar(
            value=app_settings.get_warn_export_multiple_discard_before_first()
        )
        ctk.CTkCheckBox(
            tab,
            text="Warn before Export multiple discards media before the first split",
            variable=self._warn_multi_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(12, 8))

        ctk.CTkLabel(
            tab,
            text="Autosave writes a separate recovery slot (never your manual Save file).",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(16, 4))

        ctk.CTkLabel(tab, text="Autosave every", anchor="w", width=120).grid(
            row=5, column=0, sticky="w", padx=(8, 8), pady=8
        )
        self._autosave_var = ctk.StringVar(
            value=str(app_settings.get_autosave_interval_seconds())
        )
        ctk.CTkEntry(tab, textvariable=self._autosave_var, width=100).grid(
            row=5, column=1, sticky="w", padx=4, pady=8
        )
        ctk.CTkLabel(
            tab,
            text="seconds (0 = off; default 60)",
            text_color=("gray40", "gray60"),
            anchor="w",
        ).grid(row=5, column=2, sticky="w", padx=(4, 8))

    def _build_preview_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="How the video preview behaves while you scrub and watch.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 12))

        ctk.CTkLabel(tab, text="Skip amount", anchor="w", width=120).grid(
            row=1, column=0, sticky="w", padx=(8, 8), pady=8
        )
        self._skip_var = ctk.StringVar(value=str(app_settings.get_preview_skip_seconds()))
        ctk.CTkEntry(tab, textvariable=self._skip_var, width=100).grid(
            row=1, column=1, sticky="w", padx=4, pady=8
        )
        ctk.CTkLabel(
            tab,
            text="seconds (<< / >> buttons · ↑ / ↓)",
            text_color=("gray40", "gray60"),
            anchor="w",
        ).grid(row=1, column=2, sticky="w", padx=(4, 8))

        self._hide_timeline_var = ctk.BooleanVar(
            value=app_settings.get_hide_timeline_buttons()
        )
        ctk.CTkCheckBox(
            tab,
            text="Hide timeline button rows (use Tools menu + shortcuts)",
            variable=self._hide_timeline_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(12, 8))

    def _build_keyboard_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Timeline and Tools shortcuts. File menu keys (Ctrl+S, etc.) stay fixed.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 8))

        self._kb_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self._kb_list.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._kb_list.grid_columnconfigure(0, weight=1)
        self._kb_chord_labels: dict[str, ctk.CTkLabel] = {}

        header = ctk.CTkFrame(self._kb_list, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Action", anchor="w", width=160).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(header, text="Shortcut", anchor="w", width=120).grid(
            row=0, column=1, sticky="w", padx=8
        )

        for i, action in enumerate(ACTIONS, start=1):
            row = ctk.CTkFrame(self._kb_list, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=2)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=action.label, anchor="w").grid(
                row=0, column=0, sticky="w"
            )
            chord_lbl = ctk.CTkLabel(
                row,
                text=chord_to_label(get_binding(action.id)),
                anchor="w",
                width=120,
                text_color=("gray30", "gray70"),
            )
            chord_lbl.grid(row=0, column=1, sticky="w", padx=8)
            self._kb_chord_labels[action.id] = chord_lbl
            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.grid(row=0, column=2, sticky="e")
            ctk.CTkButton(
                btns,
                text="Change",
                width=70,
                command=lambda a=action: self._kb_change(a.id, a.label),
            ).pack(side="left", padx=2)
            ctk.CTkButton(
                btns,
                text="Clear",
                width=60,
                fg_color=("gray70", "gray35"),
                hover_color=("gray60", "gray45"),
                command=lambda aid=action.id: self._kb_clear(aid),
            ).pack(side="left", padx=2)
            ctk.CTkButton(
                btns,
                text="Default",
                width=70,
                fg_color=("gray70", "gray35"),
                hover_color=("gray60", "gray45"),
                command=lambda aid=action.id: self._kb_reset_one(aid),
            ).pack(side="left", padx=2)

        foot = ctk.CTkFrame(tab, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 8))
        ctk.CTkButton(
            foot,
            text="Reset all to defaults",
            width=160,
            command=self._kb_reset_all,
        ).pack(side="left")

    def _kb_refresh_labels(self) -> None:
        for action_id, label in self._kb_chord_labels.items():
            label.configure(text=chord_to_label(get_binding(action_id)))

    def _kb_change(self, action_id: str, action_label: str) -> None:
        chord = capture_key(self, action_label=action_label, action_id=action_id)
        if chord is None:
            return
        set_binding(action_id, chord)
        self._kb_refresh_labels()

    def _kb_clear(self, action_id: str) -> None:
        clear_binding(action_id)
        self._kb_refresh_labels()

    def _kb_reset_one(self, action_id: str) -> None:
        reset_binding(action_id)
        self._kb_refresh_labels()

    def _kb_reset_all(self) -> None:
        ok = dialogs.ask_yes_no(
            "Reset shortcuts?",
            "Restore every timeline / Tools shortcut to its default?",
            parent=self,
            ok_text="Reset",
        )
        if not ok:
            return
        reset_all_bindings()
        self._kb_refresh_labels()

    def _build_audio_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Preview sound only — your exported MKV keeps its original audio.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 12))

        ctk.CTkLabel(tab, text="Output", anchor="w", width=120).grid(
            row=1, column=0, sticky="w", padx=(8, 8), pady=8
        )
        self._audio_choices = app_settings.list_preview_audio_outputs()
        saved = app_settings.get_preview_audio_device()
        initial = (
            saved
            if saved and saved in self._audio_choices
            else app_settings.DEFAULT_AUDIO_LABEL
        )
        self._audio_var = ctk.StringVar(value=initial)
        ctk.CTkOptionMenu(
            tab,
            variable=self._audio_var,
            values=self._audio_choices,
            width=360,
            dynamic_resizing=False,
        ).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 8), pady=8)

        ctk.CTkLabel(
            tab,
            text="System default follows Windows. Choose a device to send Donatello alone "
            "(e.g. headphones) without changing the rest of your PC.",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray60"),
            wraplength=500,
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 10))

        ctk.CTkLabel(tab, text="Sample rate", anchor="w", width=120).grid(
            row=3, column=0, sticky="w", padx=(8, 8), pady=8
        )
        self._rate_var = ctk.StringVar(value=str(app_settings.get_preview_sample_rate()))
        ctk.CTkOptionMenu(
            tab,
            variable=self._rate_var,
            values=[str(r) for r in app_settings.PREVIEW_SAMPLE_RATES],
            width=140,
        ).grid(row=3, column=1, sticky="w", padx=4, pady=8)

        ctk.CTkLabel(tab, text="Downmix", anchor="w", width=120).grid(
            row=4, column=0, sticky="w", padx=(8, 8), pady=8
        )
        self._downmix_var = ctk.StringVar(value=app_settings.get_preview_downmix())
        ctk.CTkOptionMenu(
            tab,
            variable=self._downmix_var,
            values=list(app_settings.PREVIEW_DOWNMIX_OPTIONS),
            width=140,
        ).grid(row=4, column=1, sticky="w", padx=4, pady=8)

        ctk.CTkLabel(
            tab,
            text="Stereo / Mono fold multi-channel for headphones. Keep channels leaves "
            "5.1/7.1 alone when your output supports it (falls back to stereo if not). "
            "Export never changes channel layout.",
            anchor="w",
            justify="left",
            text_color=("gray40", "gray60"),
            wraplength=500,
            font=ctk.CTkFont(size=11),
        ).grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))

    def _build_help_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tab,
            text="If something goes wrong, the log is the first place to look.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 12))

        ctk.CTkLabel(
            tab,
            text=str(log_path()),
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            wraplength=560,
        ).grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))

        ctk.CTkButton(
            tab, text="Open log file", width=140, command=self._open_log
        ).grid(row=2, column=0, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(
            tab,
            text="FFmpeg",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=3, column=0, sticky="ew", padx=8, pady=(18, 4))

        self._ffmpeg_status = ctk.CTkLabel(
            tab,
            text="Not checked yet — click Check FFmpeg.",
            anchor="w",
            text_color=("gray40", "gray65"),
            wraplength=560,
            justify="left",
        )
        self._ffmpeg_status.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))

        ctk.CTkButton(
            tab, text="Check FFmpeg", width=140, command=self._check_ffmpeg
        ).grid(row=5, column=0, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(
            tab,
            text="Donatello uses FFmpeg to open, cut, and export MKVs. "
            "A bundled copy downloads on first use when needed.",
            anchor="w",
            text_color=("gray40", "gray60"),
            wraplength=560,
            font=ctk.CTkFont(size=11),
        ).grid(row=6, column=0, sticky="ew", padx=8, pady=(4, 8))

        # Refresh status once when the dialog opens (non-blocking-ish).
        self.after(50, self._refresh_ffmpeg_status_quiet)

    def _refresh_ffmpeg_status_quiet(self) -> None:
        """Update the Help status line without a popup (fast path if already OK)."""
        from src.core.ffmpeg_paths import check_ffmpeg

        result = check_ffmpeg()
        app_settings.set_ffmpeg_check_ok(result.ok)
        if result.ok:
            line = result.version_line or "FFmpeg found"
            path = result.ffmpeg_path or ""
            text = f"OK — {line}"
            if path:
                text = f"{text}\n{path}"
            self._ffmpeg_status.configure(text=text)
        else:
            self._ffmpeg_status.configure(
                text="Not found — click Check FFmpeg for steps to fix it."
            )

    def _check_ffmpeg(self) -> None:
        from src.core.ffmpeg_paths import check_ffmpeg

        result = check_ffmpeg()
        app_settings.set_ffmpeg_check_ok(result.ok)
        if result.ok:
            line = result.version_line or "FFmpeg found"
            path = result.ffmpeg_path or ""
            text = f"OK — {line}"
            if path:
                text = f"{text}\n{path}"
            self._ffmpeg_status.configure(text=text)
            dialogs.show_info("FFmpeg", result.user_message(), parent=self)
        else:
            self._ffmpeg_status.configure(
                text="Not found — see the dialog for what to try."
            )
            dialogs.show_error("FFmpeg not found", result.user_message(), parent=self)

    def _open_log(self) -> None:
        try:
            path = open_log_file()
            logger.info("Opened log file: %s", path)
        except Exception as exc:
            logger.exception("Could not open log file")
            dialogs.show_error(
                "Log file",
                f"Could not open the log file:\n{exc}\n\n{log_path()}",
                parent=self,
            )

    def _close(self) -> None:
        raw = self._skip_var.get().strip()
        try:
            app_settings.set_preview_skip_seconds(raw)
            app_settings.set_preview_sample_rate(self._rate_var.get().strip())
            app_settings.set_preview_downmix(self._downmix_var.get().strip())
            app_settings.set_autosave_interval_seconds(self._autosave_var.get().strip())
        except ValueError as exc:
            dialogs.show_error("Invalid setting", str(exc), parent=self)
            return
        chosen = self._audio_var.get().strip()
        app_settings.set_preview_audio_device(
            None if chosen == app_settings.DEFAULT_AUDIO_LABEL else chosen
        )
        app_settings.set_warn_export_multiple_discard_before_first(
            bool(self._warn_multi_var.get())
        )
        app_settings.set_hide_timeline_buttons(bool(self._hide_timeline_var.get()))
        logger.info(
            "Settings saved: device=%s rate=%s downmix=%s skip=%s warn_multi=%s autosave=%s hide_timeline=%s",
            chosen or app_settings.DEFAULT_AUDIO_LABEL,
            self._rate_var.get(),
            self._downmix_var.get(),
            raw,
            self._warn_multi_var.get(),
            self._autosave_var.get(),
            self._hide_timeline_var.get(),
        )
        self.grab_release()
        self.destroy()

    def _add_folder_row(
        self,
        tab,
        *,
        row: int,
        label: str,
        variable: ctk.StringVar,
        on_browse,
        on_clear,
    ) -> None:
        ctk.CTkLabel(tab, text=label, anchor="w", width=100).grid(
            row=row, column=0, sticky="w", padx=(8, 8), pady=8
        )
        entry = ctk.CTkEntry(tab, textvariable=variable, state="readonly")
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=8)
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=row, column=2, sticky="e", padx=(4, 8), pady=8)
        ctk.CTkButton(btns, text="Browse", width=80, command=on_browse).pack(
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
            dialogs.show_error("Invalid folder", str(exc), parent=self)
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
            dialogs.show_error("Invalid folder", str(exc), parent=self)
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
