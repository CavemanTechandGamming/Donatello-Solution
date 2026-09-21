"""Edit-track dialog — title, language by name, Default/Forced (subs), volume (audio), sync offset (A/S)."""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import BooleanVar, StringVar

import customtkinter as ctk

from src.core.languages import code_from_name, language_names, name_from_code

_VOLUME_MAX = 2.0
_OFFSET_MAX = 3600.0


@dataclass(frozen=True)
class TrackEditFields:
    title: str
    language: str
    is_default: bool
    is_forced: bool
    volume: float | None = None  # 0.0–2.0 when editing audio; else None
    sync_offset: float | None = None  # seconds; audio/sub only; + = later


def ask_edit_track(
    master,
    *,
    heading: str,
    title: str,
    language: str,
    is_default: bool,
    is_forced: bool,
    show_disposition: bool,
    show_volume: bool = False,
    volume: float = 1.0,
    show_sync_offset: bool = False,
    sync_offset: float = 0.0,
) -> TrackEditFields | None:
    """Edit title + language; Default/Forced for subs; volume for audio; sync for A/S."""
    dialog = _EditTrackDialog(
        master,
        heading=heading,
        title=title,
        language=language,
        is_default=is_default,
        is_forced=is_forced,
        show_disposition=show_disposition,
        show_volume=show_volume,
        volume=volume,
        show_sync_offset=show_sync_offset,
        sync_offset=sync_offset,
    )
    dialog.wait_window()
    return dialog.result


def _center_on_parent(window, parent) -> None:
    try:
        window.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        ww = window.winfo_width()
        wh = window.winfo_height()
        x = px + max(0, (pw - ww) // 2)
        y = py + max(0, (ph - wh) // 2)
        window.geometry(f"+{x}+{y}")
    except Exception:
        pass


def _format_offset(seconds: float) -> str:
    value = float(seconds)
    if abs(value) < 1e-9:
        return "0"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    if value > 0 and not text.startswith("+"):
        return f"+{text}"
    return text


class _EditTrackDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        *,
        heading: str,
        title: str,
        language: str,
        is_default: bool,
        is_forced: bool,
        show_disposition: bool,
        show_volume: bool,
        volume: float,
        show_sync_offset: bool,
        sync_offset: float,
    ):
        super().__init__(master)
        self.title("Edit track")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result: TrackEditFields | None = None
        self._show_volume = show_volume
        self._show_sync_offset = show_sync_offset

        names = list(language_names())
        known = name_from_code(language)
        extra = None
        if known is None:
            raw = (language or "und").strip() or "und"
            extra = f"Other ({raw})"
            names = [extra, *names]
            selected_name = extra
        else:
            selected_name = known

        self._extra_name = extra
        self._extra_code = (language or "und").strip() or "und"

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=heading,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 12))

        ctk.CTkLabel(self, text="Title", anchor="w").grid(
            row=1, column=0, sticky="w", padx=(20, 12), pady=6
        )
        self._title = ctk.CTkEntry(self, width=280)
        self._title.grid(row=1, column=1, sticky="ew", padx=(0, 20), pady=6)
        self._title.insert(0, title)

        ctk.CTkLabel(self, text="Language", anchor="w").grid(
            row=2, column=0, sticky="w", padx=(20, 12), pady=6
        )
        self._language = ctk.CTkComboBox(self, values=names, width=280, state="readonly")
        self._language.grid(row=2, column=1, sticky="ew", padx=(0, 20), pady=6)
        self._language.set(selected_name)

        row = 3
        self._default = BooleanVar(value=is_default)
        self._forced = BooleanVar(value=is_forced)
        if show_disposition:
            ctk.CTkCheckBox(self, text="Default", variable=self._default).grid(
                row=row, column=1, sticky="w", padx=(0, 20), pady=(8, 4)
            )
            row += 1
            ctk.CTkCheckBox(self, text="Forced", variable=self._forced).grid(
                row=row, column=1, sticky="w", padx=(0, 20), pady=(0, 8)
            )
            row += 1

        self._volume_var: ctk.DoubleVar | None = None
        self._volume_pct: StringVar | None = None
        self._syncing_volume = False
        if show_volume:
            vol = float(max(0.0, min(_VOLUME_MAX, volume)))
            self._volume_var = ctk.DoubleVar(value=vol)
            self._volume_pct = StringVar(value=str(int(round(vol * 100))))

            ctk.CTkLabel(self, text="Volume", anchor="w").grid(
                row=row, column=0, sticky="nw", padx=(20, 12), pady=(10, 6)
            )
            vol_col = ctk.CTkFrame(self, fg_color="transparent")
            vol_col.grid(row=row, column=1, sticky="ew", padx=(0, 20), pady=(10, 6))
            vol_col.grid_columnconfigure(0, weight=1)

            self._volume_slider = ctk.CTkSlider(
                vol_col,
                from_=0,
                to=_VOLUME_MAX,
                number_of_steps=200,
                variable=self._volume_var,
                command=self._on_slider,
            )
            self._volume_slider.grid(row=0, column=0, sticky="ew")

            pct_row = ctk.CTkFrame(vol_col, fg_color="transparent")
            pct_row.grid(row=1, column=0, sticky="w", pady=(6, 0))
            self._volume_entry = ctk.CTkEntry(
                pct_row, width=64, textvariable=self._volume_pct
            )
            self._volume_entry.pack(side="left")
            self._volume_entry.bind("<FocusOut>", self._on_pct_commit)
            self._volume_entry.bind("<Return>", self._on_pct_commit)
            ctk.CTkLabel(
                pct_row,
                text="%  (0–200)",
                anchor="w",
                text_color=("gray40", "gray65"),
            ).pack(side="left", padx=(6, 0))
            row += 1

        self._offset_var: StringVar | None = None
        if show_sync_offset:
            clamped = max(-_OFFSET_MAX, min(_OFFSET_MAX, float(sync_offset)))
            self._offset_var = StringVar(value=_format_offset(clamped))
            ctk.CTkLabel(self, text="Sync", anchor="w").grid(
                row=row, column=0, sticky="nw", padx=(20, 12), pady=(10, 6)
            )
            off_col = ctk.CTkFrame(self, fg_color="transparent")
            off_col.grid(row=row, column=1, sticky="ew", padx=(0, 20), pady=(10, 6))
            entry_row = ctk.CTkFrame(off_col, fg_color="transparent")
            entry_row.pack(anchor="w")
            self._offset_entry = ctk.CTkEntry(
                entry_row, width=96, textvariable=self._offset_var
            )
            self._offset_entry.pack(side="left")
            ctk.CTkLabel(
                entry_row,
                text="sec",
                anchor="w",
                text_color=("gray40", "gray65"),
            ).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(
                off_col,
                text="+ later · − earlier  (vs video)",
                anchor="w",
                text_color=("gray40", "gray65"),
            ).pack(anchor="w", pady=(4, 0))
            row += 1

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", padx=20, pady=(8, 16))
        ctk.CTkButton(
            buttons,
            text="Cancel",
            width=100,
            fg_color=("gray40", "gray30"),
            hover_color=("gray35", "gray25"),
            command=lambda: self._finish(None),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="OK", width=100, command=self._ok).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(None))
        self.bind("<Escape>", lambda _e: self._finish(None))
        self.bind("<Return>", lambda _e: self._ok())

        self.update_idletasks()
        height = 240
        if show_disposition:
            height += 60
        if show_volume:
            height += 80
        if show_sync_offset:
            height += 70
        self.geometry(f"480x{max(height, self.winfo_reqheight())}")
        self.after(10, lambda: _center_on_parent(self, master))
        self.after(50, self._title.focus_set)

    def _on_slider(self, value: str | float) -> None:
        if self._syncing_volume or self._volume_pct is None:
            return
        self._syncing_volume = True
        try:
            vol = float(max(0.0, min(_VOLUME_MAX, float(value))))
            self._volume_pct.set(str(int(round(vol * 100))))
        finally:
            self._syncing_volume = False

    def _on_pct_commit(self, _event=None) -> None:
        if self._syncing_volume or self._volume_var is None or self._volume_pct is None:
            return
        self._syncing_volume = True
        try:
            raw = (self._volume_pct.get() or "").strip().rstrip("%")
            try:
                pct = float(raw)
            except ValueError:
                pct = self._volume_var.get() * 100.0
            vol = max(0.0, min(_VOLUME_MAX, pct / 100.0))
            self._volume_var.set(vol)
            self._volume_pct.set(str(int(round(vol * 100))))
        finally:
            self._syncing_volume = False

    def _parse_offset(self) -> float:
        if self._offset_var is None:
            return 0.0
        raw = (self._offset_var.get() or "").strip().replace("s", "").replace("S", "")
        if not raw or raw in ("+", "-"):
            return 0.0
        try:
            value = float(raw)
        except ValueError:
            return 0.0
        return max(-_OFFSET_MAX, min(_OFFSET_MAX, value))

    def _language_code(self) -> str:
        name = self._language.get()
        if self._extra_name and name == self._extra_name:
            return self._extra_code
        return code_from_name(name)

    def _ok(self) -> None:
        if self._show_volume:
            self._on_pct_commit()
        volume = None
        if self._show_volume and self._volume_var is not None:
            volume = float(max(0.0, min(_VOLUME_MAX, self._volume_var.get())))
        sync_offset = None
        if self._show_sync_offset:
            sync_offset = self._parse_offset()
        self._finish(
            TrackEditFields(
                title=self._title.get(),
                language=self._language_code(),
                is_default=bool(self._default.get()),
                is_forced=bool(self._forced.get()),
                volume=volume,
                sync_offset=sync_offset,
            )
        )

    def _finish(self, value: TrackEditFields | None) -> None:
        self.result = value
        self.grab_release()
        self.destroy()
