"""Simple dialog to view / rename / delete / seek named markers."""

from __future__ import annotations

import customtkinter as ctk

from src.core.markers import TimelineMarker
from src.core.probe import format_duration
from src.ui import dialogs


class MarkersDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        markers: list[TimelineMarker],
        *,
        on_seek=None,
        on_change=None,
    ) -> None:
        super().__init__(master)
        self.title("Markers")
        self.geometry("420x360")
        self.minsize(360, 280)
        self.transient(master)
        self.grab_set()
        self.focus_set()

        self._markers = list(markers)
        self._on_seek = on_seek
        self._on_change = on_change

        ctk.CTkLabel(
            self,
            text="Markers → chapters on Export.",
            anchor="w",
            text_color=("gray40", "gray65"),
        ).pack(fill="x", padx=14, pady=(12, 6))

        self._list = ctk.CTkScrollableFrame(self, label_text="Markers")
        self._list.pack(fill="both", expand=True, padx=14, pady=6)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(4, 14))
        ctk.CTkButton(btn_row, text="Close", width=90, command=self._close).pack(
            side="right"
        )

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._rebuild()

    def _rebuild(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()
        ordered = sorted(self._markers, key=lambda m: (m.time, m.name.lower()))
        self._markers = ordered
        if not ordered:
            ctk.CTkLabel(
                self._list,
                text="No markers yet. Add one at the playhead.",
                anchor="w",
                text_color=("gray40", "gray60"),
            ).pack(fill="x", padx=6, pady=8)
            return
        for idx, marker in enumerate(ordered):
            row = ctk.CTkFrame(self._list, fg_color=("gray85", "gray22"))
            row.pack(fill="x", padx=2, pady=3)
            ctk.CTkLabel(
                row,
                text=f"{format_duration(marker.time)}  —  {marker.name}",
                anchor="w",
            ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            ctk.CTkButton(
                row, text="Go", width=44, command=lambda i=idx: self._seek(i)
            ).pack(side="right", padx=2, pady=4)
            ctk.CTkButton(
                row, text="Rename", width=70, command=lambda i=idx: self._rename(i)
            ).pack(side="right", padx=2, pady=4)
            ctk.CTkButton(
                row, text="Del", width=44, command=lambda i=idx: self._delete(i)
            ).pack(side="right", padx=2, pady=4)

    def _seek(self, index: int) -> None:
        if self._on_seek and 0 <= index < len(self._markers):
            self._on_seek(self._markers[index].time)

    def _rename(self, index: int) -> None:
        if not (0 <= index < len(self._markers)):
            return
        current = self._markers[index]
        name = dialogs.ask_string(
            "Rename marker",
            "Chapter name:",
            initialvalue=current.name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            dialogs.show_warning("Rename marker", "Name cannot be empty.", parent=self)
            return
        self._markers[index] = TimelineMarker(time=current.time, name=name)
        self._notify()
        self._rebuild()

    def _delete(self, index: int) -> None:
        if not (0 <= index < len(self._markers)):
            return
        del self._markers[index]
        self._notify()
        self._rebuild()

    def _notify(self) -> None:
        if self._on_change:
            self._on_change(list(self._markers))

    def _close(self) -> None:
        self.grab_release()
        self.destroy()


def open_markers_dialog(master, markers: list[TimelineMarker], *, on_seek, on_change) -> None:
    dialog = MarkersDialog(master, markers, on_seek=on_seek, on_change=on_change)
    master.wait_window(dialog)
