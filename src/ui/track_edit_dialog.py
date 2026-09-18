"""Edit-track dialog — title, language by name, Default/Forced (subs)."""

from __future__ import annotations

from dataclasses import dataclass
from tkinter import BooleanVar

import customtkinter as ctk

from src.core.languages import code_from_name, language_names, name_from_code


@dataclass(frozen=True)
class TrackEditFields:
    title: str
    language: str
    is_default: bool
    is_forced: bool


def ask_edit_track(
    master,
    *,
    heading: str,
    title: str,
    language: str,
    is_default: bool,
    is_forced: bool,
    show_disposition: bool,
) -> TrackEditFields | None:
    """Edit title + language; Default/Forced when *show_disposition* (subtitles)."""
    dialog = _EditTrackDialog(
        master,
        heading=heading,
        title=title,
        language=language,
        is_default=is_default,
        is_forced=is_forced,
        show_disposition=show_disposition,
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
    ):
        super().__init__(master)
        self.title("Edit track")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result: TrackEditFields | None = None

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
                row=row, column=1, sticky="w", padx=(0, 20), pady=(0, 12)
            )
            row += 1
        else:
            row += 1

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", padx=20, pady=(0, 16))
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
        height = 300 if show_disposition else 240
        self.geometry(f"480x{max(height, self.winfo_reqheight())}")
        self.after(10, lambda: _center_on_parent(self, master))
        self.after(50, self._title.focus_set)

    def _language_code(self) -> str:
        name = self._language.get()
        if self._extra_name and name == self._extra_name:
            return self._extra_code
        return code_from_name(name)

    def _ok(self) -> None:
        self._finish(
            TrackEditFields(
                title=self._title.get(),
                language=self._language_code(),
                is_default=bool(self._default.get()),
                is_forced=bool(self._forced.get()),
            )
        )

    def _finish(self, value: TrackEditFields | None) -> None:
        self.result = value
        self.grab_release()
        self.destroy()
