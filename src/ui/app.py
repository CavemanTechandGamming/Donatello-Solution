"""Dark-mode shell: project panel, preview, multi-lane timeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

from src import __version__
from src.core import settings as app_settings
from src.core.cut import CutError, CutRange, perform_cut
from src.core.insert import perform_insert
from src.core.export_metadata import (
    ExportError,
    TrackEditState,
    edits_from_tracks,
    export_with_track_metadata,
)
from src.core.logging_setup import get_logger, log_path
from src.core.markers import TimelineMarker
from src.core.preview import PreviewFrame, PreviewPlayer
from src.core.probe import MediaTrack, ProbeError, ProbeResult, format_duration, probe_mkv
from src.core.workspace import (
    WorkspaceError,
    WorkspaceState,
    load_workspace,
    save_workspace,
)
from src.ui.about_dialog import open_about
from src.ui import dialogs
from src.ui.markers_dialog import open_markers_dialog
from src.ui.menubar import ThemedMenuBar
from src.ui.settings_dialog import open_settings as show_settings_dialog
from src.ui.timeline_lanes import LaneTrack, TimelineLanes
from src.ui.tooltip import tip
from src.ui.track_edit_dialog import ask_edit_track

from PIL import Image

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

logger = get_logger("ui")


def _with_log_hint(message: str) -> str:
    return f"{message}\n\nDetails: {log_path()}"


def _show_error(title: str, message: str, *, parent=None, exc: BaseException | None = None) -> None:
    if exc is not None:
        logger.exception("%s: %s", title, message)
    else:
        logger.error("%s: %s", title, message)
    dialogs.show_error(title, _with_log_hint(message), parent=parent)


@dataclass
class ProjectItem:
    """One MKV imported into the current workspace bin."""

    path: Path
    duration_label: str
    track_summary: str  # short e.g. "1V 2A 3S"


def _track_summary(result: ProbeResult) -> str:
    counts = {"video": 0, "audio": 0, "subtitle": 0}
    for track in result.tracks:
        if track.kind in counts:
            counts[track.kind] += 1
    parts: list[str] = []
    if counts["video"]:
        parts.append(f"{counts['video']}V")
    if counts["audio"]:
        parts.append(f"{counts['audio']}A")
    if counts["subtitle"]:
        parts.append(f"{counts['subtitle']}S")
    return " ".join(parts) or "—"


def _path_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _format_skip_label(seconds: float) -> str:
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    text = f"{seconds:.1f}".rstrip("0").rstrip(".")
    return f"{text}s"


def _work_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / "DonatelloSolution" / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


class DonatelloApp(ctk.CTk, TkinterDnD.DnDWrapper):
    """Main window — project bin + preview + track edit + timeline shell (#1–#5)."""

    def __init__(self) -> None:
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.title(f"Donatello Solution {__version__}")
        self.geometry("1280x820")
        self.minsize(960, 640)

        self._result: ProbeResult | None = None
        self._items: list[ProjectItem] = []
        self._item_buttons: dict[str, ctk.CTkButton] = {}
        self._selected_path: Path | None = None
        self._active_path: Path | None = None
        self._edits: dict[str, dict[int, TrackEditState]] = {}
        self._markers: dict[str, list[TimelineMarker]] = {}
        self._audio_volumes: dict[str, dict[int, float]] = {}
        self._mark_in: float | None = None
        self._mark_out: float | None = None
        self._selected_stream_index: int | None = None
        self._workspace_path: Path | None = None

        self._preview_image: ctk.CTkImage | None = None
        self._last_pil: Image.Image | None = None
        self._scrub_job: str | None = None
        self._updating_scrub = False
        self._player = PreviewPlayer(
            on_frame=self._on_preview_frame,
            on_position=self._on_preview_position,
            on_ended=self._on_preview_ended,
            on_error=self._on_preview_error,
            max_width=1280,
        )

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_menu()
        self._build_layout()
        self._wire_drag_and_drop()
        self._show_empty_state()
        self._poll_preview()

    # ── menu ────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        # Native tk.Menu ignores dark theme on Windows — use themed bar instead.
        self._menubar = ThemedMenuBar(self)
        self._menubar.grid(row=0, column=0, columnspan=2, sticky="ew")

        self._menubar.add_menu(
            "File",
            [
                ("Import MKV", self.import_mkv_dialog),
                ("Open MKV on timeline", self.open_mkv_on_timeline),
                ("---", None),
                ("Open Workspace", self.open_workspace_dialog),
                ("Save Workspace", self.save_workspace),
                ("Save Workspace As", self.save_workspace_as_dialog),
                ("Export MKV", self.export_dialog),
                ("---", None),
                ("Exit", self.destroy),
            ],
        )
        self._menubar.add_menu(
            "Edit",
            [
                ("Settings", self.open_settings),
                ("---", None),
                ("Clear marks", self._clear_marks),
                ("Add marker", self._add_marker_here),
                ("Markers", self._manage_markers),
            ],
        )
        self._menubar.add_menu(
            "Help",
            [
                ("Open log file", self._open_log_from_menu),
                ("---", None),
                ("About Donatello", self._show_about),
            ],
            last=True,
        )

        self.bind_all("<Control-i>", lambda _e: self.import_mkv_dialog())
        self.bind_all("<Control-o>", lambda _e: self.open_mkv_on_timeline())
        self.bind_all("<Control-Shift-O>", lambda _e: self.open_workspace_dialog())
        self.bind_all("<Control-s>", lambda _e: self.save_workspace())
        self.bind_all("<Control-e>", lambda _e: self.export_dialog())
        self.bind_all("<Control-comma>", lambda _e: self.open_settings())

    def _open_log_from_menu(self) -> None:
        from src.core.logging_setup import open_log_file

        try:
            open_log_file()
        except Exception as exc:
            _show_error("Log file", str(exc), parent=self, exc=exc)

    def _show_about(self) -> None:
        open_about(self)

    # ── layout ──────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=260)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        project = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray90", "gray18"), width=260)
        project.grid(row=1, column=0, sticky="nsew")
        project.grid_propagate(False)
        project.grid_columnconfigure(0, weight=1)
        project.grid_rowconfigure(2, weight=1)

        proj_top = ctk.CTkFrame(project, fg_color="transparent")
        proj_top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            proj_top,
            text="Project",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        btn_import = ctk.CTkButton(
            proj_top, text="Import", width=90, command=self.import_mkv_dialog
        )
        btn_import.pack(side="right")
        tip(btn_import, "Import", "Add MKV files to the project panel.")

        self._project_list = ctk.CTkScrollableFrame(project, fg_color="transparent")
        self._project_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._project_list.grid_columnconfigure(0, weight=1)

        tracks_frame = ctk.CTkFrame(self, corner_radius=0)
        tracks_frame.grid(row=1, column=1, sticky="nsew")
        tracks_frame.grid_columnconfigure(0, weight=1)
        tracks_frame.grid_rowconfigure(0, weight=1)

        # Preview (right column)
        preview = ctk.CTkFrame(tracks_frame, fg_color=("gray88", "gray16"), corner_radius=0)
        preview.grid(row=0, column=0, sticky="nsew")
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_rowconfigure(1, weight=1)
        self._preview_frame = preview

        ctk.CTkLabel(
            preview,
            text="Preview",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))

        self._preview_label = ctk.CTkLabel(
            preview,
            text="",
            fg_color=("gray80", "gray12"),
            corner_radius=6,
        )
        self._preview_label.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self._preview_label.bind("<Configure>", self._on_preview_resize)
        self._resize_job: str | None = None

        transport = ctk.CTkFrame(preview, fg_color="transparent")
        transport.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 2))

        self._btn_skip_back = ctk.CTkButton(
            transport, text="<< 5s", width=64, command=lambda: self._step_seconds(-1)
        )
        self._btn_skip_back.pack(side="left", padx=(0, 4))
        self._tip_skip_back = tip(
            self._btn_skip_back, "Skip back", "Jump backward by the skip amount."
        )

        self._btn_frame_back = ctk.CTkButton(
            transport, text="‹ 1f", width=52, command=lambda: self._step_frames(-1)
        )
        self._btn_frame_back.pack(side="left", padx=2)
        tip(self._btn_frame_back, "Previous frame", "Step one frame backward.")

        self._play_btn = ctk.CTkButton(
            transport, text="Play", width=70, command=self._toggle_play
        )
        self._play_btn.pack(side="left", padx=6)
        tip(self._play_btn, "Play / Pause", "Start or pause preview playback.")

        self._btn_stop = ctk.CTkButton(
            transport, text="Stop", width=60, command=self._stop_preview
        )
        self._btn_stop.pack(side="left", padx=2)
        tip(self._btn_stop, "Stop", "Stop preview and return to the start.")

        self._btn_frame_fwd = ctk.CTkButton(
            transport, text="1f ›", width=52, command=lambda: self._step_frames(1)
        )
        self._btn_frame_fwd.pack(side="left", padx=2)
        tip(self._btn_frame_fwd, "Next frame", "Step one frame forward.")

        self._btn_skip_fwd = ctk.CTkButton(
            transport, text="5s >>", width=64, command=lambda: self._step_seconds(1)
        )
        self._btn_skip_fwd.pack(side="left", padx=(4, 4))
        self._tip_skip_fwd = tip(
            self._btn_skip_fwd, "Skip forward", "Jump forward by the skip amount."
        )

        btn_prev_mark = ctk.CTkButton(
            transport, text="‹ M", width=44, command=lambda: self._goto_marker(-1)
        )
        btn_prev_mark.pack(side="left", padx=(8, 2))
        tip(btn_prev_mark, "Previous marker", "Jump to the previous named marker.")

        btn_next_mark = ctk.CTkButton(
            transport, text="M ›", width=44, command=lambda: self._goto_marker(1)
        )
        btn_next_mark.pack(side="left", padx=2)
        tip(btn_next_mark, "Next marker", "Jump to the next named marker.")

        self._time_label = ctk.CTkLabel(
            transport,
            text="0:00 / —",
            width=110,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._time_label.pack(side="left", padx=(8, 0))

        scrub_row = ctk.CTkFrame(preview, fg_color="transparent")
        scrub_row.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 8))
        scrub_row.grid_columnconfigure(0, weight=1)
        self._scrub = ctk.CTkSlider(
            scrub_row, from_=0, to=1, number_of_steps=1000, command=self._on_scrub
        )
        self._scrub.grid(row=0, column=0, sticky="ew")
        self._scrub.set(0)
        self._scrub.configure(state="disabled")

        self._refresh_skip_button_labels()

        timeline = ctk.CTkFrame(self, fg_color=("gray80", "gray14"), corner_radius=0, height=360)
        timeline.grid(row=2, column=0, columnspan=2, sticky="ew")
        timeline.grid_columnconfigure(0, weight=1)
        timeline.grid_rowconfigure(1, weight=1)
        timeline.grid_propagate(False)

        ctk.CTkLabel(
            timeline,
            text="Timeline",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 2))

        self._lanes = TimelineLanes(
            timeline,
            on_seek=self._seek_preview,
            on_select=self._select_stream,
            on_edit=self._edit_track,
        )
        self._lanes.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 6))

        cut_row = ctk.CTkFrame(timeline, fg_color="transparent")
        cut_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))

        btn_in = ctk.CTkButton(cut_row, text="Mark In", width=78, command=self._mark_in_here)
        btn_in.pack(side="left", padx=(0, 4))
        tip(btn_in, "Mark In", "Set the start of the range at the playhead.")

        btn_out = ctk.CTkButton(
            cut_row, text="Mark Out", width=86, command=self._mark_out_here
        )
        btn_out.pack(side="left", padx=4)
        tip(btn_out, "Mark Out", "Set the end of the range at the playhead.")

        btn_clear = ctk.CTkButton(
            cut_row, text="Clear", width=64, command=self._clear_marks
        )
        btn_clear.pack(side="left", padx=4)
        tip(btn_clear, "Clear marks", "Clear Mark In and Mark Out.")

        btn_add_marker = ctk.CTkButton(
            cut_row, text="Add marker", width=100, command=self._add_marker_here
        )
        btn_add_marker.pack(side="left", padx=(12, 4))
        tip(btn_add_marker, "Add marker", "Named chapter point at the playhead.")

        btn_markers = ctk.CTkButton(
            cut_row, text="Markers", width=90, command=self._manage_markers
        )
        btn_markers.pack(side="left", padx=4)
        tip(btn_markers, "Markers", "List, rename, delete, or jump to markers.")

        btn_cut = ctk.CTkButton(
            cut_row, text="Cut", width=64, command=lambda: self._do_cut(False)
        )
        btn_cut.pack(side="left", padx=(12, 4))
        tip(btn_cut, "Cut", "Remove the In→Out range from all streams.")

        btn_cut_sel = ctk.CTkButton(
            cut_row,
            text="Cut selected",
            width=110,
            command=lambda: self._do_cut(True),
        )
        btn_cut_sel.pack(side="left", padx=4)
        tip(btn_cut_sel, "Cut selected", "Remove In→Out on the selected video/audio only.")

        ins_row = ctk.CTkFrame(timeline, fg_color="transparent")
        ins_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))

        btn_ins = ctk.CTkButton(
            ins_row, text="Insert", width=86, command=lambda: self._do_insert(False)
        )
        btn_ins.pack(side="left", padx=(0, 4))
        tip(btn_ins, "Insert", "Splice another MKV in at In (or the playhead).")

        btn_ins_sel = ctk.CTkButton(
            ins_row,
            text="Insert selected",
            width=130,
            command=lambda: self._do_insert(True),
        )
        btn_ins_sel.pack(side="left", padx=4)
        tip(
            btn_ins_sel,
            "Insert selected",
            "Insert one matching video/audio stream only.",
        )

        self._marks_label = ctk.CTkLabel(
            ins_row,
            text="In — · Out —",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
        )
        self._marks_label.pack(side="left", padx=(12, 0))

        self._drop_targets = (project, self._project_list, tracks_frame, timeline)

    # ── drag & drop ─────────────────────────────────────────────────────

    def _wire_drag_and_drop(self) -> None:
        for zone in self._drop_targets:
            zone.drop_target_register(DND_FILES)
            zone.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event) -> None:
        paths = self._parse_drop_data(event.data)
        mkvs = [p for p in paths if p.suffix.lower() == ".mkv"]
        skipped = [p for p in paths if p.suffix.lower() != ".mkv"]
        if skipped and not mkvs:
            dialogs.show_warning(
                "MKV only",
                "Donatello imports .mkv files only.",
                parent=self,
            )
            return
        if mkvs:
            self.import_paths(mkvs, load_first_if_empty=True)

    def _parse_drop_data(self, data: str) -> list[Path]:
        try:
            raw = self.tk.splitlist(data)
        except Exception:
            raw = [data.strip()]
        return [Path(p) for p in raw if p]

    # ── project panel ───────────────────────────────────────────────────

    def _refresh_project_list(self) -> None:
        for child in self._project_list.winfo_children():
            child.destroy()
        self._item_buttons.clear()

        if not self._items:
            empty = ctk.CTkLabel(
                self._project_list,
                text="(empty)",
                text_color=("gray50", "gray55"),
                anchor="w",
            )
            empty.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
            return

        for row, item in enumerate(self._items):
            key = _path_key(item.path)
            is_active = (
                self._active_path is not None and _path_key(item.path) == _path_key(self._active_path)
            )
            is_selected = (
                self._selected_path is not None
                and _path_key(item.path) == _path_key(self._selected_path)
            )
            fg = ("gray75", "gray25") if is_selected or is_active else ("gray85", "gray20")
            label = f"{item.path.name}\n{item.duration_label}  ·  {item.track_summary}"
            if is_active:
                label = "● " + label

            btn = ctk.CTkButton(
                self._project_list,
                text=label,
                anchor="w",
                height=52,
                fg_color=fg,
                hover_color=("gray70", "gray30"),
                text_color=("gray10", "gray90"),
                font=ctk.CTkFont(size=12),
                command=lambda p=item.path: self.load_mkv(p),
            )
            btn.grid(row=row, column=0, sticky="ew", padx=2, pady=2)
            tip(btn, item.path.name, "Load this clip on the timeline.")
            self._item_buttons[key] = btn

    def import_mkv_dialog(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Import MKV",
            initialdir=app_settings.import_dialog_initialdir(),
            filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")],
        )
        if not paths:
            return
        first = Path(paths[0])
        app_settings.set_last_import_dir(first.parent)
        self.import_paths([Path(p) for p in paths], load_first_if_empty=True)

    def open_mkv_on_timeline(self) -> None:
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Open MKV on timeline",
            initialdir=app_settings.import_dialog_initialdir(),
            filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        app_settings.set_last_import_dir(path.parent)
        self.import_paths([path], load_first_if_empty=False)
        self.load_mkv(path)

    def open_settings(self) -> None:
        # Pause so the next Play picks up a newly chosen audio device.
        if self._player.playing:
            self._player.pause()
            self._play_btn.configure(text="Play")
        show_settings_dialog(self)
        self._refresh_skip_button_labels()

    def _refresh_skip_button_labels(self) -> None:
        skip = app_settings.get_preview_skip_seconds()
        label = _format_skip_label(skip)
        if hasattr(self, "_btn_skip_back"):
            self._btn_skip_back.configure(text=f"<< {label}")
            self._btn_skip_fwd.configure(text=f"{label} >>")
        if hasattr(self, "_tip_skip_back"):
            self._tip_skip_back.set_text(
                f"Skip back\nJump backward by {label} (Settings)."
            )
            self._tip_skip_fwd.set_text(
                f"Skip forward\nJump forward by {label} (Settings)."
            )

    def save_workspace(self) -> None:
        if self._workspace_path is None:
            self.save_workspace_as_dialog()
            return
        self._write_workspace(self._workspace_path)

    def save_workspace_as_dialog(self) -> None:
        path_str = filedialog.asksaveasfilename(
            parent=self,
            title="Save Workspace As",
            initialdir=app_settings.workspace_dialog_initialdir(),
            defaultextension=".donatello",
            filetypes=[
                ("Donatello workspace", "*.donatello"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path_str:
            return
        self._write_workspace(Path(path_str))

    def _capture_workspace_state(self) -> WorkspaceState:
        return WorkspaceState(
            media=[item.path for item in self._items],
            active=self._active_path,
            selected=self._selected_path,
            selected_stream_index=self._selected_stream_index,
            mark_in=self._mark_in,
            mark_out=self._mark_out,
            playhead=self._player.position if self._active_path is not None else None,
            track_edits={k: dict(v) for k, v in self._edits.items()},
            markers={k: list(v) for k, v in self._markers.items()},
            audio_volumes={k: dict(v) for k, v in self._audio_volumes.items()},
        )

    def _write_workspace(self, path: Path) -> None:
        try:
            written = save_workspace(path, self._capture_workspace_state())
        except WorkspaceError as exc:
            _show_error("Save failed", str(exc), parent=self, exc=exc)
            return
        self._workspace_path = written
        self._refresh_window_title()
        dialogs.show_info(
            "Workspace saved",
            f"Saved workspace to:\n{written}",
            parent=self,
        )

    def open_workspace_dialog(self) -> None:
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Open Workspace",
            initialdir=app_settings.workspace_dialog_initialdir(),
            filetypes=[
                ("Donatello workspace", "*.donatello"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not path_str:
            return
        self._open_workspace(Path(path_str))

    def _refresh_window_title(self) -> None:
        parts = [f"Donatello Solution {__version__}"]
        if self._workspace_path is not None:
            parts.append(self._workspace_path.name)
        if self._active_path is not None:
            parts.append(self._active_path.name)
        self.title(" — ".join(parts))

    def _reset_workspace_ui(self) -> None:
        self._result = None
        self._items.clear()
        self._item_buttons.clear()
        self._selected_path = None
        self._active_path = None
        self._edits.clear()
        self._markers.clear()
        self._audio_volumes.clear()
        self._show_empty_state()
        self._refresh_project_list()
        self._refresh_window_title()

    def _merge_saved_edits(
        self, media_key: str, result: ProbeResult
    ) -> dict[int, TrackEditState]:
        seeded = edits_from_tracks(result.tracks)
        saved = self._edits.get(media_key) or {}
        # Also try unresolved / alternate keys from the file
        if not saved:
            for alt_key, by_stream in list(self._edits.items()):
                try:
                    if _path_key(Path(alt_key)) == media_key:
                        saved = by_stream
                        break
                except OSError:
                    continue
        for stream_index, prior in saved.items():
            track = next(
                (t for t in result.tracks if t.stream_index == stream_index),
                None,
            )
            if track is None or prior.kind != track.kind:
                continue
            base = seeded[stream_index]
            base.title = prior.title
            base.language = prior.language
            if track.kind == "subtitle":
                base.is_default = prior.is_default
                base.is_forced = prior.is_forced
        self._edits[media_key] = seeded
        return seeded

    def _open_workspace(self, path: Path) -> None:
        try:
            state = load_workspace(path)
        except WorkspaceError as exc:
            _show_error("Open workspace failed", str(exc), parent=self, exc=exc)
            return

        self._reset_workspace_ui()
        self._workspace_path = path.expanduser()
        self._edits = {k: dict(v) for k, v in state.track_edits.items()}
        self._markers = {k: list(v) for k, v in state.markers.items()}
        self._audio_volumes = {
            k: dict(v) for k, v in state.audio_volumes.items()
        }

        missing: list[str] = []
        probe_errors: list[str] = []
        for media_path in state.media:
            if not media_path.is_file():
                missing.append(str(media_path))
                continue
            try:
                result = probe_mkv(media_path)
            except ProbeError as exc:
                probe_errors.append(f"{media_path.name}: {exc}")
                continue
            item = ProjectItem(
                path=result.path,
                duration_label=format_duration(result.duration_seconds),
                track_summary=_track_summary(result),
            )
            self._items.append(item)
            self._merge_saved_edits(_path_key(result.path), result)

        self._refresh_project_list()

        # Resolve active / selected against loaded items
        by_key = {_path_key(i.path): i.path for i in self._items}

        def _resolve(candidate: Path | None) -> Path | None:
            if candidate is None:
                return None
            try:
                key = _path_key(candidate)
            except OSError:
                key = str(candidate.expanduser())
            return by_key.get(key)

        active = _resolve(state.active)
        selected = _resolve(state.selected) or active
        if active is None and self._items:
            active = self._items[0].path
            selected = selected or active

        if selected is not None:
            self._selected_path = selected

        if active is not None:
            self.load_mkv(
                active,
                clear_marks=False,
                restore_stream=state.selected_stream_index,
            )
            self._mark_in = state.mark_in
            self._mark_out = state.mark_out
            self._update_marks_label()
            if state.playhead is not None:
                try:
                    self._player.show_frame_at(max(0.0, float(state.playhead)))
                except Exception:
                    logger.exception("Could not restore playhead to %.3f", state.playhead)

        self._refresh_window_title()

        notes: list[str] = []
        if missing:
            notes.append(
                "Missing media (not found on disk):\n" + "\n".join(missing[:8])
                + ("\n…" if len(missing) > 8 else "")
            )
        if probe_errors:
            notes.append(
                "Could not probe:\n" + "\n".join(probe_errors[:8])
                + ("\n…" if len(probe_errors) > 8 else "")
            )
        if notes:
            dialogs.show_warning(
                "Workspace opened with problems",
                "\n\n".join(notes),
                parent=self,
            )
        else:
            dialogs.show_info(
                "Workspace opened",
                f"Restored {len(self._items)} media item(s) from:\n{path}",
                parent=self,
            )

    def export_dialog(self) -> None:
        if self._active_path is None or self._result is None:
            dialogs.show_warning(
                "Nothing to export",
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return

        # Release preview handles so Windows can read/write the media file.
        self._player.pause()
        self._play_btn.configure(text="Play")

        path_str = filedialog.asksaveasfilename(
            parent=self,
            title="Export MKV",
            initialdir=app_settings.export_dialog_initialdir(),
            defaultextension=".mkv",
            filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")],
        )
        if not path_str:
            return

        out = Path(path_str)
        if out.suffix.lower() != ".mkv":
            out = out.with_suffix(".mkv")

        # Refresh edits against the file currently on the timeline
        try:
            current = probe_mkv(self._active_path)
        except ProbeError as exc:
            _show_error("Export failed", str(exc), parent=self, exc=exc)
            return

        key = _path_key(self._active_path)
        saved = self._edits.get(key) or {}
        edits: list = []
        for track in current.tracks:
            if track.kind not in ("video", "audio", "subtitle"):
                continue
            base = TrackEditState.from_track(track)
            prior = saved.get(track.stream_index)
            if prior is not None and prior.kind == track.kind:
                base.title = prior.title
                base.language = prior.language
                if track.kind == "subtitle":
                    base.is_default = prior.is_default
                    base.is_forced = prior.is_forced
            edits.append(base)

        logger.info("Export requested: %s → %s", self._active_path.name, out)
        chapter_markers = list(self._markers.get(key) or [])
        duration = current.duration_seconds or 0.0

        range_start: float | None = None
        range_end: float | None = None
        if self._mark_in is not None or self._mark_out is not None:
            range_start = float(self._mark_in) if self._mark_in is not None else 0.0
            range_end = (
                float(self._mark_out)
                if self._mark_out is not None
                else float(duration)
            )
            if range_end <= range_start:
                dialogs.show_warning(
                    "Export range",
                    "Mark Out must be after Mark In.\n\n"
                    "Clear marks to export the full clip, or fix In/Out.",
                    parent=self,
                )
                return

        try:
            if out.resolve() == Path(self._active_path).resolve():
                # Overwrite via temp then replace
                tmp = out.with_suffix(".donatello-export.tmp.mkv")
                export_with_track_metadata(
                    self._active_path,
                    tmp,
                    edits,
                    markers=chapter_markers,
                    duration=duration,
                    range_start=range_start,
                    range_end=range_end,
                )
                tmp.replace(out)
            else:
                export_with_track_metadata(
                    self._active_path,
                    out,
                    edits,
                    markers=chapter_markers,
                    duration=duration,
                    range_start=range_start,
                    range_end=range_end,
                )
        except ExportError as exc:
            _show_error("Export failed", str(exc), parent=self, exc=exc)
            return
        except OSError as exc:
            _show_error("Export failed", str(exc), parent=self, exc=exc)
            return

        notes: list[str] = []
        if range_start is not None and range_end is not None:
            notes.append(
                f"Trimmed to In–Out: {format_duration(range_start)}–"
                f"{format_duration(range_end)} "
                f"({format_duration(range_end - range_start)})."
            )
        else:
            notes.append("Full clip (no In/Out set).")
        if chapter_markers:
            notes.append(f"Chapters: {len(chapter_markers)} named marker(s).")
        else:
            notes.append("No named markers — no chapters written.")
        dialogs.show_info(
            "Export complete",
            "\n".join(notes) + f"\n\n{out}",
            parent=self,
        )
        # Reload preview from exported/active path
        self.load_mkv(Path(self._active_path))

    def import_paths(self, paths: list[Path], *, load_first_if_empty: bool) -> None:
        added: list[Path] = []
        errors: list[str] = []
        existing = {_path_key(i.path) for i in self._items}

        for path in paths:
            try:
                resolved = _path_key(path) if path.exists() else ""
            except OSError:
                resolved = ""
            if resolved and resolved in existing:
                continue
            try:
                result = probe_mkv(path)
            except ProbeError as exc:
                logger.error("Import failed for %s: %s", path, exc)
                errors.append(f"{path.name}: {exc}")
                continue

            item = ProjectItem(
                path=result.path,
                duration_label=format_duration(result.duration_seconds),
                track_summary=_track_summary(result),
            )
            self._items.append(item)
            existing.add(_path_key(result.path))
            added.append(result.path)
            logger.info("Imported %s (%s)", result.path.name, item.track_summary)
            # Seed edit state if new
            media_key = _path_key(result.path)
            if media_key not in self._edits:
                self._edits[media_key] = edits_from_tracks(result.tracks)

        self._refresh_project_list()

        if errors:
            _show_error(
                "Import problems",
                "\n".join(errors[:8]) + ("\n…" if len(errors) > 8 else ""),
                parent=self,
            )

        if not added:
            return

        self._selected_path = added[0]
        self._refresh_project_list()

        if load_first_if_empty and self._active_path is None:
            self.load_mkv(added[0])

    # ── timeline / streams ──────────────────────────────────────────────

    def _show_empty_state(self) -> None:
        self._lanes.clear()
        self._clear_marks()
        self._selected_stream_index = None
        self._reset_preview_ui()
        self._refresh_window_title()

    def _reset_preview_ui(self) -> None:
        self._player.close()
        self._preview_image = None
        self._last_pil = None
        self._preview_label.configure(image=None, text="")
        self._play_btn.configure(text="Play")
        self._time_label.configure(text="0:00 / —")
        self._updating_scrub = True
        self._scrub.set(0)
        self._scrub.configure(state="disabled", to=1)
        self._updating_scrub = False

    def _open_preview(self, result: ProbeResult) -> None:
        duration = result.duration_seconds or 0.0
        self._player.open(result.path, duration if duration > 0 else None)
        self._scrub.configure(state="normal", to=max(duration, 0.1))
        self._updating_scrub = True
        self._scrub.set(0)
        self._updating_scrub = False
        self._play_btn.configure(text="Play")
        self._time_label.configure(
            text=f"0:00 / {format_duration(result.duration_seconds)}"
        )
        self._refresh_skip_button_labels()

    def _toggle_play(self) -> None:
        if self._active_path is None:
            return
        if self._player.playing:
            self._player.pause()
            self._play_btn.configure(text="Play")
        else:
            self._player.play()
            self._play_btn.configure(text="Pause")

    def _stop_preview(self) -> None:
        self._player.pause()
        self._play_btn.configure(text="Play")
        if self._active_path is not None:
            self._player.show_frame_at(0.0)

    def _step_frames(self, direction: int) -> None:
        if self._active_path is None:
            return
        self._play_btn.configure(text="Play")
        self._player.step_frames(direction)

    def _step_seconds(self, direction: int) -> None:
        if self._active_path is None:
            return
        skip = app_settings.get_preview_skip_seconds()
        self._play_btn.configure(text="Play")
        self._player.step_seconds(direction * skip)

    def _goto_marker(self, direction: int) -> None:
        """Jump to previous (-1) or next (+1) named marker relative to playhead."""
        if self._active_path is None:
            return
        markers = sorted(self._active_marker_list(), key=lambda m: (m.time, m.name.lower()))
        if not markers:
            return
        t = float(self._player.position)
        eps = 0.05
        if direction < 0:
            candidates = [m for m in markers if m.time < t - eps]
            if not candidates:
                return
            target = candidates[-1]
        else:
            candidates = [m for m in markers if m.time > t + eps]
            if not candidates:
                return
            target = candidates[0]
        self._play_btn.configure(text="Play")
        self._seek_preview(float(target.time))

    def _on_scrub(self, value: str | float) -> None:
        if self._updating_scrub or self._active_path is None:
            return
        seconds = float(value)
        if self._scrub_job is not None:
            try:
                self.after_cancel(self._scrub_job)
            except Exception:
                pass
        self._scrub_job = self.after(60, lambda s=seconds: self._seek_preview(s))

    def _seek_preview(self, seconds: float) -> None:
        self._scrub_job = None
        if self._player.playing:
            self._player.pause()
            self._play_btn.configure(text="Play")
        self._player.show_frame_at(seconds)

    def _preview_box_size(self) -> tuple[int, int]:
        self._preview_label.update_idletasks()
        w = max(self._preview_label.winfo_width() - 8, 160)
        h = max(self._preview_label.winfo_height() - 8, 90)
        return w, h

    def _fit_preview_image(self, img: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
        if img.mode != "RGB":
            img = img.convert("RGB")
        box_w, box_h = self._preview_box_size()
        src_w, src_h = img.size
        # Aspect-fit inside the preview pane (letterbox — never crop)
        scale = min(box_w / float(src_w), box_h / float(src_h))
        size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
        if size != img.size:
            fitted = img.resize(size, Image.Resampling.BILINEAR)
        else:
            fitted = img
        return fitted, size

    def _show_pil_preview(self, img: Image.Image) -> None:
        self._last_pil = img.copy() if img is not None else None
        if self._last_pil is None:
            return
        fitted, size = self._fit_preview_image(self._last_pil)
        self._preview_image = ctk.CTkImage(
            light_image=fitted, dark_image=fitted, size=size
        )
        self._preview_label.configure(image=self._preview_image, text="")

    def _on_preview_resize(self, _event=None) -> None:
        if self._last_pil is None:
            return
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(50, self._reflow_preview)

    def _reflow_preview(self) -> None:
        self._resize_job = None
        if self._last_pil is not None:
            self._show_pil_preview(self._last_pil)

    def _on_preview_frame(self, frame: PreviewFrame) -> None:
        self._show_pil_preview(frame.image)

    def _on_preview_position(self, seconds: float) -> None:
        duration = self._player.duration
        self._time_label.configure(
            text=f"{format_duration(seconds)} / {format_duration(duration)}"
        )
        if duration and duration > 0:
            self._updating_scrub = True
            self._scrub.set(min(seconds, duration))
            self._updating_scrub = False
        if hasattr(self, "_lanes"):
            self._lanes.set_position(seconds)

    def _on_preview_ended(self) -> None:
        self._play_btn.configure(text="Play")

    def _on_preview_error(self, message: str) -> None:
        self.after(0, lambda m=message: dialogs.show_warning("Preview", m, parent=self))

    def _poll_preview(self) -> None:
        self._player.poll()
        self.after(33, self._poll_preview)

    def _on_close(self) -> None:
        self._player.close()
        self.destroy()

    def _ensure_edits(self, result: ProbeResult) -> dict[int, TrackEditState]:
        key = _path_key(result.path)
        if key not in self._edits:
            self._edits[key] = edits_from_tracks(result.tracks)
        return self._edits[key]

    def load_mkv(
        self,
        path: Path,
        *,
        clear_marks: bool = True,
        restore_stream: int | None = None,
    ) -> None:
        try:
            result = probe_mkv(path)
        except ProbeError as exc:
            _show_error("Could not open MKV", str(exc), parent=self, exc=exc)
            return

        logger.info(
            "Loaded timeline: %s (%s, %s)",
            result.path.name,
            format_duration(result.duration_seconds),
            _track_summary(result),
        )
        resolved = _path_key(result.path)
        if resolved not in {_path_key(i.path) for i in self._items}:
            self._items.append(
                ProjectItem(
                    path=result.path,
                    duration_label=format_duration(result.duration_seconds),
                    track_summary=_track_summary(result),
                )
            )

        self._result = result
        self._active_path = result.path
        self._selected_path = result.path
        if restore_stream is not None and any(
            t.stream_index == restore_stream for t in result.tracks
        ):
            self._selected_stream_index = restore_stream
        else:
            self._selected_stream_index = None
        self._ensure_edits(result)
        self._seed_markers_from_probe(result)
        self._apply_result(result)
        self._refresh_project_list()
        self._open_preview(result)
        if clear_marks:
            self._clear_marks()
        else:
            self._update_marks_label()
        self._refresh_window_title()

    def _seed_markers_from_probe(self, result: ProbeResult) -> None:
        """Import MKV chapters as markers when this clip has none in the workspace yet."""
        key = _path_key(result.path)
        existing = self._markers.get(key)
        if existing:
            return
        if not result.chapters:
            self._markers.setdefault(key, [])
            return
        self._markers[key] = [
            TimelineMarker(time=m.time, name=m.name) for m in result.chapters
        ]
        logger.info(
            "Loaded %d chapter marker(s) from %s",
            len(self._markers[key]),
            result.path.name,
        )

    def _apply_result(self, result: ProbeResult) -> None:
        self._ensure_edits(result)
        self._refresh_timeline_lanes()
        self._sync_preview_monitor_audio()

    def _audio_volume_map(self) -> dict[int, float]:
        if self._active_path is None:
            return {}
        key = _path_key(self._active_path)
        return self._audio_volumes.setdefault(key, {})

    def _get_audio_volume(self, stream_index: int) -> float:
        return float(self._audio_volume_map().get(stream_index, 1.0))

    def _set_audio_volume(self, stream_index: int, volume: float) -> None:
        value = float(max(0.0, min(2.0, volume)))
        self._audio_volume_map()[stream_index] = value
        if self._result is None:
            return
        track = next(
            (t for t in self._result.tracks if t.stream_index == stream_index),
            None,
        )
        if track is None or track.kind != "audio":
            return
        # Live monitor follows selected audio (or first audio).
        selected = None
        if self._selected_stream_index is not None:
            selected = next(
                (
                    t
                    for t in self._result.tracks
                    if t.stream_index == self._selected_stream_index
                ),
                None,
            )
        monitor = selected if selected and selected.kind == "audio" else next(
            (t for t in self._result.tracks if t.kind == "audio"),
            None,
        )
        if monitor is not None and monitor.stream_index == stream_index:
            self._player.set_volume(value)

    def _sync_preview_monitor_audio(self) -> None:
        if self._result is None:
            self._player.set_audio_type_index(0)
            self._player.set_volume(1.0)
            return
        audios = [t for t in self._result.tracks if t.kind == "audio"]
        if not audios:
            self._player.set_audio_type_index(0)
            self._player.set_volume(1.0)
            return
        chosen = audios[0]
        if self._selected_stream_index is not None:
            for track in audios:
                if track.stream_index == self._selected_stream_index:
                    chosen = track
                    break
        self._player.set_audio_type_index(chosen.type_index)
        self._player.set_volume(self._get_audio_volume(chosen.stream_index))

    def _refresh_timeline_lanes(self) -> None:
        if not hasattr(self, "_lanes"):
            return
        if self._result is None:
            self._lanes.clear()
            return
        edits = self._ensure_edits(self._result)
        track_by_index = {t.stream_index: t for t in self._result.tracks}
        lanes: list[LaneTrack] = []
        for kind in ("video", "audio", "subtitle"):
            for track in self._result.tracks:
                if track.kind != kind:
                    continue
                edit = edits.get(track.stream_index) or TrackEditState.from_track(track)
                # Prefer edited track title; fall back to stream label / file name
                title = (edit.title or "").strip()
                if not title:
                    if track.kind == "video" and self._active_path is not None:
                        title = self._active_path.name
                    else:
                        title = edit.display_label(track_by_index.get(track.stream_index))
                if len(title) > 72:
                    title = title[:71] + "…"
                lanes.append(
                    LaneTrack(
                        track=track,
                        label=title,
                    )
                )
        duration = float(self._result.duration_seconds or 0.0)
        self._lanes.set_tracks(
            lanes,
            duration=duration,
            selected_stream=self._selected_stream_index,
            mark_in=self._mark_in,
            mark_out=self._mark_out,
            markers=list(self._active_marker_list()) if self._active_path else [],
            position=float(self._player.position),
        )

    def _select_stream(self, stream_index: int) -> None:
        self._selected_stream_index = stream_index
        self._update_marks_label()
        if hasattr(self, "_lanes"):
            self._lanes.set_selected(stream_index)
        self._sync_preview_monitor_audio()

    def _mark_in_here(self) -> None:
        if self._active_path is None:
            return
        self._mark_in = self._player.position
        self._update_marks_label()

    def _mark_out_here(self) -> None:
        if self._active_path is None:
            return
        self._mark_out = self._player.position
        self._update_marks_label()

    def _clear_marks(self) -> None:
        self._mark_in = None
        self._mark_out = None
        self._update_marks_label()

    def _active_marker_list(self) -> list[TimelineMarker]:
        if self._active_path is None:
            return []
        key = _path_key(self._active_path)
        return self._markers.setdefault(key, [])

    def _add_marker_here(self) -> None:
        if self._active_path is None:
            dialogs.show_warning(
                "Add marker",
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return
        t = float(self._player.position)
        default_name = f"Chapter {len(self._active_marker_list()) + 1}"
        name = dialogs.ask_string(
            "Add marker",
            f"Name for marker at {format_duration(t)}:",
            initialvalue=default_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip() or default_name
        markers = self._active_marker_list()
        markers.append(TimelineMarker(time=t, name=name))
        markers.sort(key=lambda m: (m.time, m.name.lower()))
        logger.info("Added marker %r @ %.3f on %s", name, t, self._active_path.name)
        self._update_marks_label()

    def _manage_markers(self) -> None:
        if self._active_path is None:
            dialogs.show_warning(
                "Markers",
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return
        key = _path_key(self._active_path)

        def on_seek(seconds: float) -> None:
            try:
                self._player.show_frame_at(max(0.0, float(seconds)))
            except Exception:
                logger.exception("Seek to marker failed")

        def on_change(markers: list[TimelineMarker]) -> None:
            self._markers[key] = list(markers)
            self._update_marks_label()

        open_markers_dialog(
            self,
            list(self._markers.get(key) or []),
            on_seek=on_seek,
            on_change=on_change,
        )

    def _update_marks_label(self) -> None:
        if not hasattr(self, "_marks_label"):
            return
        inn = format_duration(self._mark_in) if self._mark_in is not None else "—"
        out = format_duration(self._mark_out) if self._mark_out is not None else "—"
        sel = "none"
        if self._result is not None and self._selected_stream_index is not None:
            track = next(
                (t for t in self._result.tracks if t.stream_index == self._selected_stream_index),
                None,
            )
            if track:
                sel = f"{track.kind[0].upper()}{track.type_index}"
        n_markers = len(self._active_marker_list()) if self._active_path else 0
        marker_bit = f" · {n_markers}m" if n_markers else ""
        sel_bit = f" · {sel}" if sel != "none" else ""
        self._marks_label.configure(text=f"In {inn} · Out {out}{marker_bit}{sel_bit}")
        if hasattr(self, "_lanes"):
            self._lanes.set_marks(self._mark_in, self._mark_out)
            if self._active_path is not None:
                self._lanes.set_markers(list(self._active_marker_list()))

    def _do_cut(self, single_stream: bool) -> None:
        if self._active_path is None or self._result is None:
            dialogs.show_warning("Cut", "Load a clip onto the timeline first.", parent=self)
            return
        if self._mark_in is None or self._mark_out is None:
            dialogs.show_warning(
                "Cut",
                "Set Mark In and Mark Out from the playhead first.",
                parent=self,
            )
            return
        try:
            cut = CutRange(start=self._mark_in, end=self._mark_out)
        except CutError as exc:
            _show_error("Cut", str(exc), parent=self, exc=exc)
            return

        selected: MediaTrack | None = None
        if single_stream:
            if self._selected_stream_index is None:
                dialogs.show_warning(
                    "Cut selected",
                    "Select a video or audio stream in the Streams list first.",
                    parent=self,
                )
                return
            selected = next(
                (
                    t
                    for t in self._result.tracks
                    if t.stream_index == self._selected_stream_index
                ),
                None,
            )
            if selected is None:
                _show_error("Cut selected", "Selected stream not found.", parent=self)
                return

        stem = self._active_path.stem
        out_path = _work_dir() / f"{stem}_cut_{int(cut.start * 1000)}_{int(cut.end * 1000)}.mkv"
        key = _path_key(self._active_path)
        edits_map = self._edits.get(key) or edits_from_tracks(self._result.tracks)
        edits = list(edits_map.values())
        audio_tracks = [t for t in self._result.tracks if t.kind == "audio"]

        mode = "selected track" if single_stream else "all streams"
        logger.info(
            "Cut %s: %s %.3f–%.3f → %s",
            mode,
            self._active_path.name,
            cut.start,
            cut.end,
            out_path.name,
        )
        try:
            perform_cut(
                self._active_path,
                out_path,
                cut,
                duration=self._result.duration_seconds,
                edits=edits,
                selected_track=selected,
                audio_tracks=audio_tracks,
            )
        except CutError as exc:
            _show_error("Cut failed", str(exc), parent=self, exc=exc)
            return

        dialogs.show_info(
            "Cut complete",
            f"Removed {format_duration(cut.start)}–{format_duration(cut.end)} ({mode}).\n\n"
            f"Saved:\n{out_path}",
            parent=self,
        )
        self.import_paths([out_path], load_first_if_empty=False)
        self.load_mkv(out_path)

    def _insert_point(self) -> float:
        if self._mark_in is not None:
            return self._mark_in
        return self._player.position

    def _pick_insert_source(self) -> Path | None:
        """Choose another project MKV, or browse."""
        active_key = _path_key(self._active_path) if self._active_path else None
        others = [i for i in self._items if _path_key(i.path) != active_key]

        if len(others) == 1:
            return others[0].path

        if others:
            chosen: list[Path | None] = [None]
            dialog = ctk.CTkToplevel(self)
            dialog.title("Insert clip")
            dialog.geometry("420x320")
            dialog.transient(self)
            dialog.grab_set()
            ctk.CTkLabel(
                dialog,
                text="Choose a Project clip to insert (or Browse)",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(16, 8))
            list_frame = ctk.CTkScrollableFrame(dialog)
            list_frame.pack(fill="both", expand=True, padx=16, pady=8)

            def pick(p: Path) -> None:
                chosen[0] = p
                dialog.destroy()

            for item in others:
                ctk.CTkButton(
                    list_frame,
                    text=f"{item.path.name}\n{item.duration_label}  ·  {item.track_summary}",
                    anchor="w",
                    height=48,
                    command=lambda p=item.path: pick(p),
                ).pack(fill="x", pady=2)

            def browse() -> None:
                path_str = filedialog.askopenfilename(
                    parent=dialog,
                    title="Insert MKV",
                    initialdir=app_settings.import_dialog_initialdir(),
                    filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")],
                )
                if path_str:
                    chosen[0] = Path(path_str)
                    dialog.destroy()

            btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_row.pack(fill="x", padx=16, pady=(0, 16))
            ctk.CTkButton(btn_row, text="Browse", width=100, command=browse).pack(
                side="left"
            )
            ctk.CTkButton(btn_row, text="Cancel", width=100, command=dialog.destroy).pack(
                side="right"
            )
            self.wait_window(dialog)
            return chosen[0]

        path_str = filedialog.askopenfilename(
            parent=self,
            title="Insert MKV",
            initialdir=app_settings.import_dialog_initialdir(),
            filetypes=[("Matroska video", "*.mkv"), ("All files", "*.*")],
        )
        return Path(path_str) if path_str else None

    def _do_insert(self, single_stream: bool) -> None:
        if self._active_path is None or self._result is None:
            dialogs.show_warning("Insert", "Load a base clip onto the timeline first.", parent=self)
            return

        insert_path = self._pick_insert_source()
        if insert_path is None:
            return
        if _path_key(insert_path) == _path_key(self._active_path):
            dialogs.show_warning(
                "Insert",
                "Pick a different clip than the one already on the timeline.",
                parent=self,
            )
            return

        at = self._insert_point()
        selected_base = None
        selected_ins = None
        if single_stream:
            if self._selected_stream_index is None:
                dialogs.show_warning(
                    "Insert selected",
                    "Select a video or audio stream on the base clip first.",
                    parent=self,
                )
                return
            selected_base = next(
                (
                    t
                    for t in self._result.tracks
                    if t.stream_index == self._selected_stream_index
                ),
                None,
            )
            if selected_base is None or selected_base.kind not in ("video", "audio"):
                dialogs.show_warning(
                    "Insert selected",
                    "Select a video or audio stream (not subtitle).",
                    parent=self,
                )
                return
            try:
                ins_probe = probe_mkv(insert_path)
            except ProbeError as exc:
                _show_error("Insert", str(exc), parent=self, exc=exc)
                return
            selected_ins = next(
                (t for t in ins_probe.tracks if t.kind == selected_base.kind),
                None,
            )
            if selected_ins is None:
                _show_error(
                    "Insert selected",
                    f"Insert clip has no {selected_base.kind} stream.",
                    parent=self,
                )
                return

        # Ensure insert clip is in the project bin
        self.import_paths([insert_path], load_first_if_empty=False)

        stem = self._active_path.stem
        out_path = _work_dir() / f"{stem}_ins_{int(at * 1000)}.mkv"
        key = _path_key(self._active_path)
        edits_map = self._edits.get(key) or edits_from_tracks(self._result.tracks)
        edits = list(edits_map.values())
        audio_tracks = [t for t in self._result.tracks if t.kind == "audio"]

        mode = "selected track" if single_stream else "all streams"
        logger.info(
            "Insert %s: base=%s insert=%s at=%.3f → %s",
            mode,
            self._active_path.name,
            insert_path.name,
            at,
            out_path.name,
        )
        try:
            perform_insert(
                self._active_path,
                insert_path,
                out_path,
                at=at,
                base_duration=self._result.duration_seconds,
                edits=edits,
                selected_base_track=selected_base,
                selected_insert_track=selected_ins,
                base_audio_tracks=audio_tracks,
            )
        except CutError as exc:
            _show_error("Insert failed", str(exc), parent=self, exc=exc)
            return

        dialogs.show_info(
            "Insert complete",
            f"Inserted at {format_duration(at)} ({mode}).\n\nSaved:\n{out_path}",
            parent=self,
        )
        self.import_paths([out_path], load_first_if_empty=False)
        self.load_mkv(out_path)

    def _edit_track(self, stream_index: int) -> None:
        if self._result is None or self._active_path is None:
            return
        edits = self._ensure_edits(self._result)
        edit = edits.get(stream_index)
        track = next((t for t in self._result.tracks if t.stream_index == stream_index), None)
        if edit is None or track is None:
            return

        heading = f"{edit.kind.capitalize()} stream {edit.type_index}  ({track.codec})"
        result = ask_edit_track(
            self,
            heading=heading,
            title=edit.title,
            language=edit.language,
            is_default=edit.is_default,
            is_forced=edit.is_forced,
            show_disposition=(edit.kind == "subtitle"),
            show_volume=(edit.kind == "audio"),
            volume=self._get_audio_volume(stream_index),
        )
        if result is None:
            return

        edit.title = result.title
        edit.language = result.language
        if edit.kind == "subtitle":
            edit.is_default = result.is_default
            edit.is_forced = result.is_forced
        if edit.kind == "audio" and result.volume is not None:
            self._set_audio_volume(stream_index, result.volume)
        self._refresh_timeline_lanes()


def run() -> None:
    app = DonatelloApp()
    app.mainloop()
