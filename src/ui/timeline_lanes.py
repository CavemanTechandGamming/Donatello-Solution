"""Multi-lane timeline strip — one row per video / audio / subtitle stream."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

import customtkinter as ctk

from src.core.markers import TimelineMarker
from src.core.probe import MediaTrack, format_duration
from src.ui.tooltip import tip

_LANE_COLORS = {
    "video": "#1f6aa5",
    "audio": "#2d8a5e",
    "subtitle": "#8a6a2d",
    "other": "#555555",
}
_LANE_HEIGHT = 26
_HEADER_WIDTH = 56
_MARKER_STRIP_HEIGHT = 22
_MARKER_HIT_PX = 10
_MARKER_COLOR = "#e6c35c"


@dataclass(frozen=True)
class LaneTrack:
    track: MediaTrack
    label: str


class TimelineLanes(ctk.CTkFrame):
    """Scrollable V/A/S lanes with shared playhead, In/Out, and markers."""

    def __init__(
        self,
        master,
        *,
        on_seek: Callable[[float], None] | None = None,
        on_select: Callable[[int], None] | None = None,
        on_edit: Callable[[int], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_seek = on_seek
        self._on_select = on_select
        self._on_edit = on_edit

        self._duration = 0.0
        self._position = 0.0
        self._mark_in: float | None = None
        self._mark_out: float | None = None
        self._markers: list[TimelineMarker] = []
        self._selected_stream: int | None = None
        self._lanes: list[LaneTrack] = []
        self._canvases: list[tk.Canvas] = []
        self._marker_canvas: tk.Canvas | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._ruler = ctk.CTkLabel(
            self,
            text="0:00 —",
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=("gray40", "gray60"),
        )
        self._ruler.grid(row=0, column=0, sticky="ew", pady=(0, 2))

        self._marker_row = ctk.CTkFrame(self, fg_color="transparent")
        self._marker_row.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self._marker_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self._marker_row,
            text="M",
            width=_HEADER_WIDTH,
            anchor="center",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=("gray40", "gray65"),
        ).grid(row=0, column=0, sticky="nsw", padx=(0, 6))

        self._marker_canvas = tk.Canvas(
            self._marker_row,
            height=_MARKER_STRIP_HEIGHT,
            highlightthickness=0,
            bd=0,
            bg="#141414",
            cursor="hand2",
        )
        self._marker_canvas.grid(row=0, column=1, sticky="ew")
        self._marker_canvas.bind(
            "<Configure>", lambda _e: self._draw_marker_strip()
        )
        self._marker_canvas.bind("<Button-1>", self._click_marker_strip)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", height=200)
        self._scroll.grid(row=2, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(1, weight=1)

        self._empty = ctk.CTkLabel(
            self._scroll,
            text="Load a clip to see timeline lanes.",
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        self._empty.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=8)

    def clear(self) -> None:
        self._lanes = []
        self._canvases = []
        self._duration = 0.0
        self._position = 0.0
        self._mark_in = None
        self._mark_out = None
        self._markers = []
        self._selected_stream = None
        for child in self._scroll.winfo_children():
            child.destroy()
        self._empty = ctk.CTkLabel(
            self._scroll,
            text="Load a clip to see timeline lanes.",
            anchor="w",
            text_color=("gray40", "gray60"),
        )
        self._empty.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=8)
        self._ruler.configure(text="0:00 —")
        self._draw_marker_strip()

    def set_tracks(
        self,
        lanes: list[LaneTrack],
        *,
        duration: float,
        selected_stream: int | None = None,
        mark_in: float | None = None,
        mark_out: float | None = None,
        markers: list[TimelineMarker] | None = None,
        position: float = 0.0,
    ) -> None:
        self._lanes = list(lanes)
        self._duration = max(0.0, float(duration))
        self._selected_stream = selected_stream
        self._mark_in = mark_in
        self._mark_out = mark_out
        self._markers = list(markers or [])
        self._position = max(0.0, float(position))
        self._rebuild()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, float(seconds))
        self._redraw_all()

    def set_marks(self, mark_in: float | None, mark_out: float | None) -> None:
        self._mark_in = mark_in
        self._mark_out = mark_out
        self._redraw_all()

    def set_markers(self, markers: list[TimelineMarker]) -> None:
        self._markers = list(markers)
        self._redraw_all()

    def set_selected(self, stream_index: int | None) -> None:
        self._selected_stream = stream_index
        self._rebuild()

    def _marker_x(self, time: float, width: int, *, pad: int = 2) -> float:
        if self._duration <= 0:
            return pad
        return pad + (time / self._duration) * (width - 2 * pad)

    def _hit_marker(self, x: float, width: int) -> TimelineMarker | None:
        if self._duration <= 0 or not self._markers:
            return None
        best: TimelineMarker | None = None
        best_dist = float(_MARKER_HIT_PX) + 0.01
        for mark in self._markers:
            mx = self._marker_x(mark.time, width)
            dist = abs(mx - x)
            if dist < best_dist:
                best_dist = dist
                best = mark
        return best

    def _click_marker_strip(self, event) -> None:
        if self._duration <= 0 or self._on_seek is None or self._marker_canvas is None:
            return
        width = max(1, self._marker_canvas.winfo_width())
        hit = self._hit_marker(event.x, width)
        if hit is not None:
            self._on_seek(hit.time)
            return
        ratio = min(1.0, max(0.0, event.x / float(width)))
        self._on_seek(ratio * self._duration)

    def _click_seek(self, event, canvas: tk.Canvas) -> None:
        if self._duration <= 0 or self._on_seek is None:
            return
        width = max(1, canvas.winfo_width())
        hit = self._hit_marker(event.x, width)
        if hit is not None:
            self._on_seek(hit.time)
            return
        ratio = min(1.0, max(0.0, event.x / float(width)))
        self._on_seek(ratio * self._duration)

    def _draw_marker_strip(self) -> None:
        canvas = self._marker_canvas
        if canvas is None:
            return
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = _MARKER_STRIP_HEIGHT
        pad = 2
        canvas.create_rectangle(0, 0, width, height, fill="#141414", outline="")
        canvas.create_line(pad, height - 1, width - pad, height - 1, fill="#333333")

        if self._duration <= 0:
            return

        for mark in self._markers:
            x = self._marker_x(mark.time, width, pad=pad)
            canvas.create_line(x, 8, x, height - 1, fill=_MARKER_COLOR, width=2)
            canvas.create_polygon(
                x - 6,
                2,
                x + 6,
                2,
                x,
                12,
                fill=_MARKER_COLOR,
                outline="#1a1a1a",
            )
            name = (mark.name or "").strip()
            if name:
                label = name if len(name) <= 14 else name[:13] + "…"
                canvas.create_text(
                    min(width - 4, max(4, x + 8)),
                    height // 2,
                    text=label,
                    fill="#f0e6c0",
                    font=("Segoe UI", 8),
                    anchor="w",
                )

        # Playhead on marker strip
        x = self._marker_x(self._position, width, pad=pad)
        canvas.create_line(x, 0, x, height, fill="#ffffff", width=2)

    def _redraw_all(self) -> None:
        self._draw_marker_strip()
        for canvas in self._canvases:
            self._draw_lane(canvas)

    def _rebuild(self) -> None:
        for child in self._scroll.winfo_children():
            child.destroy()
        self._canvases = []

        if not self._lanes:
            self._empty = ctk.CTkLabel(
                self._scroll,
                text="Load a clip to see timeline lanes.",
                anchor="w",
                text_color=("gray40", "gray60"),
            )
            self._empty.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=8)
            self._ruler.configure(text="0:00 —")
            return

        self._ruler.configure(
            text=f"0:00 — {format_duration(self._duration) if self._duration else '—'}"
        )

        for row, lane in enumerate(self._lanes):
            track = lane.track
            selected = track.stream_index == self._selected_stream

            header = ctk.CTkFrame(
                self._scroll,
                width=_HEADER_WIDTH,
                fg_color=("gray70", "gray28") if selected else ("gray85", "gray20"),
                corner_radius=4,
            )
            header.grid(row=row, column=0, sticky="nsw", padx=(0, 6), pady=3)
            header.grid_propagate(False)

            kind_tag = f"{track.kind[0].upper()}{track.type_index}"
            ctk.CTkLabel(
                header,
                text=kind_tag,
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            ).pack(pady=(6, 2))

            btn_sel = ctk.CTkButton(
                header,
                text="Sel",
                width=44,
                height=22,
                command=lambda s=track.stream_index: self._select(s),
            )
            btn_sel.pack(pady=(0, 2))
            tip(btn_sel, "Select", "Select this stream for Cut/Insert selected.")

            if track.kind in ("video", "audio", "subtitle"):
                edit_tip = (
                    "Change title, language, and volume (0%–200%)."
                    if track.kind == "audio"
                    else "Change title, language, and subtitle flags."
                    if track.kind == "subtitle"
                    else "Change title and language."
                )
                btn_edit = ctk.CTkButton(
                    header,
                    text="Edit",
                    width=44,
                    height=22,
                    command=lambda s=track.stream_index: self._edit(s),
                )
                btn_edit.pack(pady=(0, 6))
                tip(btn_edit, "Edit", edit_tip)
            else:
                ctk.CTkFrame(header, height=6, fg_color="transparent").pack()

            body = ctk.CTkFrame(self._scroll, fg_color="transparent")
            body.grid(row=row, column=1, sticky="ew", pady=3)
            body.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                body,
                text=lane.label,
                anchor="center",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).grid(row=0, column=0, sticky="ew", pady=(0, 2))

            canvas = tk.Canvas(
                body,
                height=_LANE_HEIGHT,
                highlightthickness=0,
                bd=0,
                bg="#1a1a1a",
            )
            canvas.grid(row=1, column=0, sticky="ew")
            canvas.bind("<Configure>", lambda e, c=canvas: self._draw_lane(c))
            canvas.bind("<Button-1>", lambda e, c=canvas: self._click_seek(e, c))
            self._canvases.append(canvas)

        self.after_idle(self._redraw_all)

    def _select(self, stream_index: int) -> None:
        if self._on_select:
            self._on_select(stream_index)

    def _edit(self, stream_index: int) -> None:
        if self._on_edit:
            self._on_edit(stream_index)

    def _draw_lane(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, int(canvas.cget("height") or _LANE_HEIGHT))
        try:
            idx = self._canvases.index(canvas)
        except ValueError:
            return
        if idx >= len(self._lanes):
            return

        lane = self._lanes[idx]
        color = _LANE_COLORS.get(lane.track.kind, _LANE_COLORS["other"])
        selected = lane.track.stream_index == self._selected_stream

        pad = 2
        canvas.create_rectangle(
            pad,
            pad,
            width - pad,
            height - pad,
            fill=color,
            outline="#4a9fd8" if selected else "#333333",
            width=2 if selected else 1,
        )

        if (
            self._duration > 0
            and self._mark_in is not None
            and self._mark_out is not None
            and self._mark_out > self._mark_in
        ):
            x0 = pad + (self._mark_in / self._duration) * (width - 2 * pad)
            x1 = pad + (self._mark_out / self._duration) * (width - 2 * pad)
            canvas.create_rectangle(
                pad, pad, x0, height - pad, fill="#000000", stipple="gray50", outline=""
            )
            canvas.create_rectangle(
                x1, pad, width - pad, height - pad, fill="#000000", stipple="gray50", outline=""
            )
            canvas.create_rectangle(
                x0, pad, x1, height - pad, outline="#e6c35c", width=1
            )

        if self._duration > 0:
            for mark in self._markers:
                x = self._marker_x(mark.time, width, pad=pad)
                canvas.create_line(x, pad, x, height - pad, fill=_MARKER_COLOR, width=2)
                canvas.create_polygon(
                    x - 5,
                    pad,
                    x + 5,
                    pad,
                    x,
                    pad + 8,
                    fill=_MARKER_COLOR,
                    outline="",
                )

        if self._duration > 0:
            x = self._marker_x(self._position, width, pad=pad)
            canvas.create_line(x, 0, x, height, fill="#ffffff", width=2)
