"""Dark-mode shell: project panel, preview, multi-lane timeline."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, filedialog

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

from src import __version__
from src.core import settings as app_settings
from src.core.cut import CutError, CutRange, flatten_edit_decision, perform_cut
from src.core.insert import perform_insert
from src.core.export_metadata import (
    ExportError,
    TrackEditState,
    edits_from_tracks,
    export_with_track_metadata,
)
from src.core.ffmpeg_run import safe_unlink
from src.core.logging_setup import get_logger, log_path
from src.core.markers import (
    MARKER_KIND_CHAPTER,
    MARKER_KIND_SPLIT,
    TimelineMarker,
    build_export_multiple_segments,
    chapter_markers,
)
from src.core.preview import PreviewFrame, PreviewPlayer
from src.core.probe import MediaTrack, ProbeError, ProbeResult, format_duration, probe_mkv
from src.core.sequence import EditDecision, TimelineSegment
from src.core.undo import (
    TimelineCheckpoint,
    UndoStack,
    copy_audio_volumes,
    copy_edl,
    copy_markers,
    copy_track_edits,
    copy_track_offsets,
)
from src.core.autosave import (
    autosave_exists,
    autosave_workspace_path,
    linked_workspace_from_meta,
    load_autosave_state,
    mark_clean_quit,
    should_offer_crash_restore,
    write_autosave,
)
from src.core.workspace import (
    WorkspaceError,
    WorkspaceState,
    load_workspace,
    save_workspace,
    state_to_dict,
)
from src.ui.about_dialog import open_about
from src.ui import dialogs
from src.ui.markers_dialog import open_markers_dialog
from src.ui.menubar import NativeMenuBar
from src.ui.progress_dialog import run_with_progress
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
        self._track_offsets: dict[str, dict[int, float]] = {}
        self._preview_audio_stream: dict[str, int] = {}
        self._preview_subtitle_stream: dict[str, int] = {}
        self._sequences: dict[str, EditDecision] = {}
        self._undo_stack = UndoStack()
        self._preview_media_path: Path | None = None
        self._bin_drag_path: Path | None = None
        self._bin_drag_moved = False
        self._bin_drag_origin: tuple[int, int] | None = None
        self._mark_in: float | None = None
        self._mark_out: float | None = None
        self._selected_stream_index: int | None = None
        self._monitor_audio_stream_index: int | None = None
        self._visible_subtitle_stream_index: int | None = None
        self._workspace_path: Path | None = None
        self._saved_fingerprint: str = ""
        self._autosave_job: str | None = None
        self._hide_timeline_buttons_var = BooleanVar(
            value=app_settings.get_hide_timeline_buttons()
        )
        self._cut_row = None
        self._ins_row = None

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
        self._bound_key_sequences: list[str] = []
        self._build_menu()
        self._bind_fixed_shortcuts()
        self._apply_user_keybindings()
        self._build_layout()
        self._wire_drag_and_drop()
        self._show_empty_state()
        self._remember_clean_workspace()
        self._reschedule_autosave()
        self.after(250, self._maybe_offer_autosave_restore)
        self.after(400, self._maybe_check_ffmpeg_on_startup)
        self._poll_preview()

    # ── menu ────────────────────────────────────────────────────────────

    def _accel(self, action_id: str) -> str | None:
        from src.core.keybindings import chord_to_label, get_binding

        chord = get_binding(action_id)
        if not chord:
            return None
        label = chord_to_label(chord)
        return None if label == "(none)" else label

    def _build_menu(self) -> None:
        # Native tk.Menu: system colors on Windows, but correct hover/open/dismiss.
        import tkinter as tk

        try:
            self.configure(menu=tk.Menu(self))
        except Exception:
            pass
        self._menubar = NativeMenuBar(self)

        self._menubar.add_menu(
            "File",
            [
                ("Import MKV", self.import_mkv_dialog),
                ("Open MKV on timeline", self.open_mkv_on_timeline),
                ("---", None),
                ("Open Workspace", self.open_workspace_dialog),
                ("Save Workspace", self.save_workspace),
                ("Save Workspace As", self.save_workspace_as_dialog),
                ("Restore Autosave…", self.restore_autosave_dialog),
                ("Export MKV", self.export_dialog),
                ("Export multiple…", self.export_multiple_dialog),
                ("---", None),
                ("Exit", self._on_close),
            ],
        )
        self._menubar.add_menu(
            "Edit",
            [
                ("Undo", self._undo_edit, "Ctrl+Z"),
                ("Redo", self._redo_edit, "Ctrl+Y"),
                ("---", None),
                ("Settings", self.open_settings, "Ctrl+,"),
                ("---", None),
                ("Clear marks", self._clear_marks, self._accel("clear_marks")),
                ("Add marker", self._add_marker_here, self._accel("add_marker")),
                ("Add split", self._add_split_here, self._accel("add_split")),
                ("Markers", self._manage_markers, self._accel("markers")),
            ],
        )
        self._menubar.add_menu(
            "Tools",
            [
                ("Mark In", self._mark_in_here, self._accel("mark_in")),
                ("Mark Out", self._mark_out_here, self._accel("mark_out")),
                ("Clear marks", self._clear_marks, self._accel("clear_marks")),
                ("---", None),
                ("Add marker", self._add_marker_here, self._accel("add_marker")),
                ("Add split", self._add_split_here, self._accel("add_split")),
                ("Markers", self._manage_markers, self._accel("markers")),
                (
                    "Previous marker",
                    lambda: self._goto_marker(-1),
                    self._accel("prev_marker"),
                ),
                (
                    "Next marker",
                    lambda: self._goto_marker(1),
                    self._accel("next_marker"),
                ),
                ("---", None),
                ("Razor", self._do_razor, self._accel("razor")),
                ("Delete", lambda: self._do_delete(False), self._accel("delete")),
                (
                    "Delete selected",
                    lambda: self._do_delete(True),
                    self._accel("delete_selected"),
                ),
                ("Insert", lambda: self._do_insert(False), self._accel("insert")),
                (
                    "Insert selected",
                    lambda: self._do_insert(True),
                    self._accel("insert_selected"),
                ),
                ("---", None),
                ("Play / Pause", self._toggle_play, self._accel("play_pause")),
                ("Stop", self._stop_preview, self._accel("stop")),
                (
                    "Previous frame",
                    lambda: self._step_frames(-1),
                    self._accel("prev_frame"),
                ),
                (
                    "Next frame",
                    lambda: self._step_frames(1),
                    self._accel("next_frame"),
                ),
                (
                    "Skip back",
                    lambda: self._step_seconds(-1),
                    self._accel("skip_back"),
                ),
                (
                    "Skip forward",
                    lambda: self._step_seconds(1),
                    self._accel("skip_forward"),
                ),
                ("---", None),
                (
                    "Prelog",
                    lambda: self._drop_named_chapter("Prelog"),
                    self._accel("drop_prelog"),
                ),
                (
                    "Intro",
                    lambda: self._drop_named_chapter("Intro"),
                    self._accel("drop_intro"),
                ),
                (
                    "Episode",
                    lambda: self._drop_named_chapter("Episode"),
                    self._accel("drop_episode"),
                ),
                (
                    "Credits",
                    lambda: self._drop_named_chapter("Credits"),
                    self._accel("drop_credits"),
                ),
                (
                    "Epilog",
                    lambda: self._drop_named_chapter("Epilog"),
                    self._accel("drop_epilog"),
                ),
                ("---", None),
                (
                    "Hide timeline buttons",
                    self._on_hide_timeline_buttons_toggle,
                    None,
                    "check",
                    self._hide_timeline_buttons_var,
                ),
            ],
        )
        self._menubar.add_menu(
            "Help",
            [
                ("Check FFmpeg…", self._check_ffmpeg_from_menu),
                ("Open log file", self._open_log_from_menu),
                ("---", None),
                ("About Donatello", self._show_about),
            ],
            last=True,
        )

    def _bind_fixed_shortcuts(self) -> None:
        """File / Edit chords that are not user-customizable."""
        self.bind_all("<Control-i>", lambda _e: self.import_mkv_dialog())
        self.bind_all("<Control-o>", lambda _e: self.open_mkv_on_timeline())
        self.bind_all("<Control-Shift-O>", lambda _e: self.open_workspace_dialog())
        self.bind_all("<Control-s>", lambda _e: self.save_workspace())
        self.bind_all("<Control-e>", lambda _e: self.export_dialog())
        self.bind_all("<Control-comma>", lambda _e: self.open_settings())
        self.bind_all("<Control-z>", lambda _e: self._undo_edit())
        self.bind_all("<Control-y>", lambda _e: self._redo_edit())
        self.bind_all("<Control-Shift-Z>", lambda _e: self._redo_edit())

    def _user_action_callbacks(self) -> dict:
        return {
            "mark_in": self._mark_in_here,
            "mark_out": self._mark_out_here,
            "clear_marks": self._clear_marks,
            "add_marker": self._add_marker_here,
            "add_split": self._add_split_here,
            "markers": self._manage_markers,
            "prev_marker": lambda: self._goto_marker(-1),
            "next_marker": lambda: self._goto_marker(1),
            "razor": self._do_razor,
            "delete": lambda: self._do_delete(False),
            "delete_selected": lambda: self._do_delete(True),
            "insert": lambda: self._do_insert(False),
            "insert_selected": lambda: self._do_insert(True),
            "play_pause": self._toggle_play,
            "stop": self._stop_preview,
            "prev_frame": lambda: self._step_frames(-1),
            "next_frame": lambda: self._step_frames(1),
            "skip_back": lambda: self._step_seconds(-1),
            "skip_forward": lambda: self._step_seconds(1),
            "drop_prelog": lambda: self._drop_named_chapter("Prelog"),
            "drop_intro": lambda: self._drop_named_chapter("Intro"),
            "drop_episode": lambda: self._drop_named_chapter("Episode"),
            "drop_credits": lambda: self._drop_named_chapter("Credits"),
            "drop_epilog": lambda: self._drop_named_chapter("Epilog"),
        }

    def _apply_user_keybindings(self) -> None:
        from src.core.keybindings import (
            chord_modifier_flags,
            chord_to_sequence,
            get_effective_bindings,
        )

        for seq in self._bound_key_sequences:
            try:
                self.unbind_all(seq)
            except Exception:
                pass
        self._bound_key_sequences = []

        callbacks = self._user_action_callbacks()
        for action_id, chord in get_effective_bindings().items():
            if not chord:
                continue
            callback = callbacks.get(action_id)
            if callback is None:
                continue
            flags = chord_modifier_flags(chord)
            handler = self._hotkey(callback, **flags)
            sequences = [chord_to_sequence(chord)]
            # Numpad digits mirror Key-1…Key-5
            if chord.startswith("Key-") and len(chord) == 5 and chord[-1].isdigit():
                sequences.append(f"<KP_{chord[-1]}>")
            for seq in sequences:
                if not seq:
                    continue
                self.bind_all(seq, handler)
                self._bound_key_sequences.append(seq)

    def reload_keybindings(self) -> None:
        """Re-apply user shortcuts and refresh menu accelerator labels."""
        self._apply_user_keybindings()
        self._build_menu()

    def _focus_is_typing(self) -> bool:
        """True when a text field has focus — bare hotkeys must not fire."""
        try:
            widget = self.focus_get()
        except Exception:
            return False
        if widget is None:
            return False
        name = type(widget).__name__.lower()
        if "entry" in name or "text" in name or "spinbox" in name:
            return True
        try:
            cls = widget.winfo_class()
        except Exception:
            return False
        return cls in ("Entry", "Text", "TEntry", "TCombobox", "Spinbox")

    def _hotkey(
        self,
        callback,
        *,
        require_shift: bool = False,
        forbid_shift: bool = False,
        require_control: bool = False,
        forbid_control: bool = False,
    ):
        def handler(event=None):
            if self._focus_is_typing():
                return None
            state = int(getattr(event, "state", 0) or 0) if event is not None else 0
            shift = bool(state & 0x0001)
            control = bool(state & 0x0004)
            if require_shift and not shift:
                return None
            if forbid_shift and shift:
                return None
            if require_control and not control:
                return None
            if forbid_control and control:
                return None
            callback()
            return "break"

        return handler

    def _open_log_from_menu(self) -> None:
        from src.core.logging_setup import open_log_file

        try:
            open_log_file()
        except Exception as exc:
            _show_error("Log file", str(exc), parent=self, exc=exc)

    def _check_ffmpeg_from_menu(self) -> None:
        from src.core.ffmpeg_paths import check_ffmpeg

        result = check_ffmpeg()
        app_settings.set_ffmpeg_check_ok(result.ok)
        if result.ok:
            dialogs.show_info("FFmpeg", result.user_message(), parent=self)
        else:
            dialogs.show_error("FFmpeg not found", result.user_message(), parent=self)

    def _maybe_check_ffmpeg_on_startup(self) -> None:
        """Quiet when FFmpeg works; warn once with fix steps when it does not."""
        from src.core.ffmpeg_paths import check_ffmpeg

        result = check_ffmpeg()
        if result.ok:
            app_settings.set_ffmpeg_check_ok(True)
            logger.info(
                "FFmpeg OK on startup: %s",
                result.version_line or result.ffmpeg_path,
            )
            return
        app_settings.set_ffmpeg_check_ok(False)
        logger.error("FFmpeg missing on startup: %s", result.error)
        dialogs.show_error("FFmpeg not found", result.user_message(), parent=self)

    def _show_about(self) -> None:
        open_about(self)

    # ── layout ──────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=260)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        project = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray90", "gray18"), width=260)
        project.grid(row=0, column=0, sticky="nsew")
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
        tracks_frame.grid(row=0, column=1, sticky="nsew")
        tracks_frame.grid_columnconfigure(0, weight=1)
        tracks_frame.grid_rowconfigure(0, weight=1)

        # Preview (right column)
        preview = ctk.CTkFrame(tracks_frame, fg_color=("gray88", "gray16"), corner_radius=0)
        preview.grid(row=0, column=0, sticky="nsew")
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_rowconfigure(2, weight=1)
        self._preview_frame = preview

        ctk.CTkLabel(
            preview,
            text="Preview",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))

        track_pick = ctk.CTkFrame(preview, fg_color="transparent")
        track_pick.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
        track_pick.grid_columnconfigure(1, weight=1)
        track_pick.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(track_pick, text="Audio", width=56, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self._preview_audio_menu = ctk.CTkOptionMenu(
            track_pick,
            values=["(no audio)"],
            command=self._on_preview_audio_menu,
            width=200,
            state="disabled",
        )
        self._preview_audio_menu.grid(row=0, column=1, sticky="ew", padx=(0, 16))

        ctk.CTkLabel(track_pick, text="Subs", width=44, anchor="w").grid(
            row=0, column=2, sticky="w", padx=(0, 6)
        )
        self._preview_sub_menu = ctk.CTkOptionMenu(
            track_pick,
            values=["Off"],
            command=self._on_preview_subtitle_menu,
            width=200,
            state="disabled",
        )
        self._preview_sub_menu.grid(row=0, column=3, sticky="ew")
        self._preview_audio_menu_map: dict[str, int] = {}
        self._preview_sub_menu_map: dict[str, int] = {}
        self._updating_preview_menus = False

        self._preview_label = ctk.CTkLabel(
            preview,
            text="",
            fg_color=("gray80", "gray12"),
            corner_radius=6,
        )
        self._preview_label.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        self._preview_label.bind("<Configure>", self._on_preview_resize)
        self._resize_job: str | None = None

        transport = ctk.CTkFrame(preview, fg_color="transparent")
        transport.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 2))

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
        tip(self._btn_frame_back, "Previous frame", "Step one frame backward.\nShortcut: ←")

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
        tip(self._btn_frame_fwd, "Next frame", "Step one frame forward.\nShortcut: →")

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
        scrub_row.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        scrub_row.grid_columnconfigure(0, weight=1)
        self._scrub = ctk.CTkSlider(
            scrub_row, from_=0, to=1, number_of_steps=1000, command=self._on_scrub
        )
        self._scrub.grid(row=0, column=0, sticky="ew")
        self._scrub.set(0)
        self._scrub.configure(state="disabled")

        self._refresh_skip_button_labels()

        timeline = ctk.CTkFrame(self, fg_color=("gray80", "gray14"), corner_radius=0, height=360)
        timeline.grid(row=1, column=0, columnspan=2, sticky="ew")
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
            on_segment_reorder=self._on_segment_reorder,
            on_media_drop=self._on_lane_media_drop,
        )
        self._lanes.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 6))

        cut_row = ctk.CTkFrame(timeline, fg_color="transparent")
        cut_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
        self._cut_row = cut_row

        btn_in = ctk.CTkButton(cut_row, text="Mark In", width=78, command=self._mark_in_here)
        btn_in.pack(side="left", padx=(0, 4))
        tip(
            btn_in,
            "Mark In",
            "Start of the export work area (and Delete range). "
            "Frames before In are not exported.\nShortcut: [",
        )

        btn_out = ctk.CTkButton(
            cut_row, text="Mark Out", width=86, command=self._mark_out_here
        )
        btn_out.pack(side="left", padx=4)
        tip(
            btn_out,
            "Mark Out",
            "End of the export work area (and Delete range). "
            "Frames after Out are not exported.\nShortcut: ]",
        )

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

        btn_add_split = ctk.CTkButton(
            cut_row, text="Add split", width=90, command=self._add_split_here
        )
        btn_add_split.pack(side="left", padx=4)
        tip(
            btn_add_split,
            "Add split",
            "Split point for Export multiple — name becomes the output filename.",
        )

        btn_markers = ctk.CTkButton(
            cut_row, text="Markers", width=90, command=self._manage_markers
        )
        btn_markers.pack(side="left", padx=4)
        tip(btn_markers, "Markers", "List, rename, delete, or jump to markers.")

        btn_razor = ctk.CTkButton(
            cut_row, text="Razor", width=70, command=self._do_razor
        )
        btn_razor.pack(side="left", padx=(12, 4))
        tip(
            btn_razor,
            "Razor",
            "Split the timeline at the playhead. Nothing is deleted — "
            "drag segments to rearrange.",
        )

        btn_del = ctk.CTkButton(
            cut_row, text="Delete", width=70, command=lambda: self._do_delete(False)
        )
        btn_del.pack(side="left", padx=4)
        tip(
            btn_del,
            "Delete",
            "Remove the In→Out range from the timeline (closes the gap). "
            "Confirms first. In/Out still define the Export work area.",
        )

        btn_del_sel = ctk.CTkButton(
            cut_row,
            text="Delete selected",
            width=120,
            command=lambda: self._do_delete(True),
        )
        btn_del_sel.pack(side="left", padx=4)
        tip(
            btn_del_sel,
            "Delete selected",
            "Remove In→Out on the selected video/audio only (bakes a new file).",
        )

        ins_row = ctk.CTkFrame(timeline, fg_color="transparent")
        ins_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        self._ins_row = ins_row

        btn_ins = ctk.CTkButton(
            ins_row, text="Insert", width=86, command=lambda: self._do_insert(False)
        )
        btn_ins.pack(side="left", padx=(0, 4))
        tip(btn_ins, "Insert", "Splice another MKV into the timeline at In (or playhead).")

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
            "Insert one matching video/audio stream only (bakes a new file).",
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
        self._apply_timeline_chrome_visibility()

    def _on_hide_timeline_buttons_toggle(self) -> None:
        hidden = bool(self._hide_timeline_buttons_var.get())
        app_settings.set_hide_timeline_buttons(hidden)
        self._apply_timeline_chrome_visibility()

    def _apply_timeline_chrome_visibility(self) -> None:
        """Show or hide Mark/Edit button rows under the lanes."""
        hidden = bool(self._hide_timeline_buttons_var.get())
        for row in (self._cut_row, self._ins_row):
            if row is None:
                continue
            if hidden:
                row.grid_remove()
            else:
                row.grid()

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
        if not mkvs:
            return

        on_timeline = self._drop_is_on_timeline(event)
        if on_timeline and self._active_path is not None and self._result is not None:
            self.import_paths(mkvs, load_first_if_empty=False)
            at = self._insert_point()
            inserted = 0
            for path in mkvs:
                if not self._insert_clip_into_edl(path, at, quiet=True):
                    break
                inserted += 1
                try:
                    dur = probe_mkv(path).duration_seconds or 0.0
                except ProbeError:
                    dur = 0.0
                at += max(0.0, float(dur))
            self._sync_scrubber_duration()
            self._refresh_timeline_lanes()
            if inserted:
                dialogs.show_info(
                    "Inserted on timeline",
                    f"Added {inserted} clip(s) into the sequence.",
                    parent=self,
                )
            return

        self.import_paths(mkvs, load_first_if_empty=True)

    def _drop_is_on_timeline(self, event) -> bool:
        """True when the OS drop landed on the timeline / lane strip."""
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = getattr(event, "widget", None)
        cur = w
        for _ in range(20):
            if cur is None:
                break
            if cur is getattr(self, "_lanes", None):
                return True
            cur = getattr(cur, "master", None)
        return False

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
                command=lambda: None,
            )
            btn.grid(row=row, column=0, sticky="ew", padx=2, pady=2)
            tip(
                btn,
                item.path.name,
                "Click to load. Drag onto the timeline to insert at drop point.",
            )
            btn.bind(
                "<ButtonPress-1>",
                lambda e, p=item.path: self._bin_drag_start(e, p),
                add="+",
            )
            btn.bind("<B1-Motion>", self._bin_drag_motion, add="+")
            btn.bind(
                "<ButtonRelease-1>",
                lambda e, p=item.path: self._bin_drag_release(e, p),
                add="+",
            )
            self._item_buttons[key] = btn

    def _bin_drag_start(self, event, path: Path) -> None:
        self._bin_drag_path = path
        self._bin_drag_moved = False
        self._bin_drag_origin = (int(event.x_root), int(event.y_root))

    def _bin_drag_motion(self, event) -> None:
        if self._bin_drag_path is None or self._bin_drag_origin is None:
            return
        dx = abs(int(event.x_root) - self._bin_drag_origin[0])
        dy = abs(int(event.y_root) - self._bin_drag_origin[1])
        if dx > 8 or dy > 8:
            self._bin_drag_moved = True
            try:
                self.configure(cursor="fleur")
            except Exception:
                pass

    def _bin_drag_release(self, event, path: Path) -> None:
        moved = self._bin_drag_moved
        drag_path = self._bin_drag_path or path
        # If lane release already consumed the drop, drag was cleared
        if self._bin_drag_path is None and moved:
            try:
                self.configure(cursor="")
            except Exception:
                pass
            return
        self._clear_bin_drag()
        if not moved:
            self.load_mkv(path)
            return
        # Dropped somewhere: if over lanes, insert at that time
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        over_lanes = False
        cur = w
        for _ in range(16):
            if cur is None:
                break
            if cur is self._lanes:
                over_lanes = True
                break
            cur = getattr(cur, "master", None)
        if over_lanes and self._active_path is not None:
            at = self._playhead_timeline()
            for canvas in getattr(self._lanes, "_canvases", []):
                try:
                    cx = canvas.winfo_rootx()
                    cy = canvas.winfo_rooty()
                    cw = canvas.winfo_width()
                    ch = canvas.winfo_height()
                except Exception:
                    continue
                if cx <= event.x_root <= cx + cw and cy <= event.y_root <= cy + ch:
                    local_x = event.x_root - cx
                    at = self._lanes._x_to_time(local_x, max(1, cw))
                    break
            self._insert_clip_into_edl(drag_path, at)
        elif self._active_path is not None and dialogs.ask_yes_no(
            "Insert at playhead?",
            f"Insert “{drag_path.name}” at the playhead "
            f"({format_duration(self._playhead_timeline())})?",
            parent=self,
            ok_text="Insert",
        ):
            self._insert_clip_into_edl(drag_path, self._playhead_timeline())

    def _clear_bin_drag(self) -> None:
        self._bin_drag_path = None
        self._bin_drag_moved = False
        self._bin_drag_origin = None
        try:
            self.configure(cursor="")
        except Exception:
            pass

    def _on_lane_media_drop(self, timeline_t: float) -> bool:
        """Called from lane release when a bin drag is in progress."""
        if self._bin_drag_path is None or not self._bin_drag_moved:
            return False
        path = self._bin_drag_path
        self._clear_bin_drag()
        return self._insert_clip_into_edl(path, timeline_t)

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
        self._hide_timeline_buttons_var.set(app_settings.get_hide_timeline_buttons())
        self._apply_timeline_chrome_visibility()
        self.reload_keybindings()
        self._refresh_skip_button_labels()
        self._reschedule_autosave()

    def _refresh_skip_button_labels(self) -> None:
        skip = app_settings.get_preview_skip_seconds()
        label = _format_skip_label(skip)
        if hasattr(self, "_btn_skip_back"):
            self._btn_skip_back.configure(text=f"<< {label}")
            self._btn_skip_fwd.configure(text=f"{label} >>")
        if hasattr(self, "_tip_skip_back"):
            self._tip_skip_back.set_text(
                f"Skip back\nJump backward by {label} (Settings).\nShortcut: ↓"
            )
            self._tip_skip_fwd.set_text(
                f"Skip forward\nJump forward by {label} (Settings).\nShortcut: ↑"
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
            playhead=self._playhead_timeline() if self._active_path is not None else None,
            track_edits={k: dict(v) for k, v in self._edits.items()},
            markers={k: list(v) for k, v in self._markers.items()},
            audio_volumes={k: dict(v) for k, v in self._audio_volumes.items()},
            track_offsets={k: dict(v) for k, v in self._track_offsets.items()},
            preview_audio_stream=dict(self._preview_audio_stream),
            preview_subtitle_stream=dict(self._preview_subtitle_stream),
            sequences={k: EditDecision(segments=list(v.segments)) for k, v in self._sequences.items()},
        )

    def _workspace_fingerprint(self) -> str:
        """Stable snapshot of savable state (playhead ignored — scrub is not dirty)."""
        data = state_to_dict(self._capture_workspace_state())
        data["playhead"] = None
        return json.dumps(data, sort_keys=True, default=str)

    def _remember_clean_workspace(self) -> None:
        self._saved_fingerprint = self._workspace_fingerprint()
        self._refresh_window_title()

    def _is_workspace_dirty(self) -> bool:
        return self._workspace_fingerprint() != self._saved_fingerprint

    def _confirm_leave_if_dirty(self) -> bool:
        """
        If dirty, prompt Save / Don't save / Cancel.
        Returns True when it is OK to leave the current workspace state.
        """
        if not self._is_workspace_dirty():
            return True
        choice = dialogs.ask_save_discard_cancel(
            "Unsaved workspace",
            "You have unsaved workspace changes.\n\n"
            "Save before continuing?",
            parent=self,
        )
        if choice == "cancel":
            return False
        if choice == "discard":
            return True
        # save
        if self._workspace_path is None:
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
                return False
            return self._write_workspace(Path(path_str), quiet=True)
        return self._write_workspace(self._workspace_path, quiet=True)

    def _write_workspace(self, path: Path, *, quiet: bool = False) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
            auto_slot = autosave_workspace_path().resolve()
        except OSError:
            resolved = Path(path).expanduser()
            auto_slot = autosave_workspace_path()
        if resolved == auto_slot:
            dialogs.show_warning(
                "Manual save",
                "That path is the Autosave recovery slot.\n\n"
                "Choose a different file for Save / Save As — "
                "autosave never replaces your manual workspace.",
                parent=self,
            )
            return False
        try:
            written = save_workspace(
                path, self._capture_workspace_state(), save_kind="manual"
            )
        except WorkspaceError as exc:
            _show_error("Save failed", str(exc), parent=self, exc=exc)
            return False
        self._workspace_path = written
        self._remember_clean_workspace()
        if not quiet:
            dialogs.show_info(
                "Workspace saved",
                f"Saved workspace to:\n{written}",
                parent=self,
            )
        else:
            logger.info("Workspace saved (quiet): %s", written)
        return True

    def open_workspace_dialog(self) -> None:
        if not self._confirm_leave_if_dirty():
            return
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
        chosen = Path(path_str)
        try:
            if chosen.expanduser().resolve() == autosave_workspace_path().resolve():
                dialogs.show_info(
                    "Open Workspace",
                    "That file is the Autosave recovery slot.\n\n"
                    "Use File → Restore Autosave… instead — "
                    "Open Workspace is for your manual Save files only.",
                    parent=self,
                )
                return
        except OSError:
            pass
        self._open_workspace(chosen)

    def _refresh_window_title(self) -> None:
        parts = [f"Donatello Solution {__version__}"]
        if self._workspace_path is not None:
            name = self._workspace_path.name
            if self._is_workspace_dirty():
                name = f"• {name}"
            parts.append(name)
        elif self._is_workspace_dirty():
            parts.append("• unsaved")
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
        self._track_offsets.clear()
        self._preview_audio_stream.clear()
        self._preview_subtitle_stream.clear()
        self._sequences.clear()
        self._undo_stack.clear()
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

    def _open_workspace(self, path: Path, *, from_autosave: bool = False) -> None:
        try:
            state = load_workspace(path)
        except WorkspaceError as exc:
            title = "Restore Autosave failed" if from_autosave else "Open workspace failed"
            _show_error(title, str(exc), parent=self, exc=exc)
            return

        self._reset_workspace_ui()
        if from_autosave:
            # Never treat the autosave slot as the manual workspace path.
            linked = linked_workspace_from_meta()
            if linked is not None:
                try:
                    if linked.is_file():
                        self._workspace_path = linked
                    else:
                        self._workspace_path = None
                except OSError:
                    self._workspace_path = None
            else:
                self._workspace_path = None
        else:
            self._workspace_path = path.expanduser()
        self._edits = {k: dict(v) for k, v in state.track_edits.items()}
        self._markers = {k: list(v) for k, v in state.markers.items()}
        self._audio_volumes = {
            k: dict(v) for k, v in state.audio_volumes.items()
        }
        self._track_offsets = {
            k: dict(v) for k, v in state.track_offsets.items()
        }
        self._preview_audio_stream = dict(state.preview_audio_stream)
        self._preview_subtitle_stream = dict(state.preview_subtitle_stream)
        self._sequences = {
            k: EditDecision(segments=list(v.segments))
            for k, v in state.sequences.items()
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
                    self._seek_timeline(max(0.0, float(state.playhead)))
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
                "Autosave restored with problems" if from_autosave else "Workspace opened with problems",
                "\n\n".join(notes),
                parent=self,
            )
        elif from_autosave:
            dest = (
                f"\n\nManual Save path (unchanged):\n{self._workspace_path}"
                if self._workspace_path
                else "\n\nNo linked manual workspace — use Save As to keep this."
            )
            dialogs.show_info(
                "Autosave restored",
                "Loaded the Autosave recovery slot "
                "(separate from your manual Save)."
                + dest,
                parent=self,
            )
        else:
            dialogs.show_info(
                "Workspace opened",
                f"Restored {len(self._items)} media item(s) from:\n{path}",
                parent=self,
            )
        if from_autosave:
            # Force dirty so Save writes the manual file, not the autosave slot.
            self._saved_fingerprint = "__restored_autosave__"
            self._refresh_window_title()
        else:
            self._remember_clean_workspace()

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

        stream_offsets = copy_track_offsets(self._track_offsets.get(key) or {})

        logger.info("Export requested: %s → %s", self._active_path.name, out)
        chapter_markers_list = chapter_markers(list(self._markers.get(key) or []))
        edl = self._active_sequence()
        needs_flatten = not self._edl_is_identity()
        duration = (
            edl.timeline_duration()
            if needs_flatten and edl is not None
            else (current.duration_seconds or 0.0)
        )

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

        flat_path: Path | None = None
        export_source = Path(self._active_path)
        tmp_out: Path | None = None
        wrote_direct = False

        def _job(prog) -> bool:
            nonlocal flat_path, export_source, duration, tmp_out, wrote_direct
            if needs_flatten:
                if edl is None or not edl.segments:
                    raise ExportError(
                        "Timeline edit decision is empty — nothing to export."
                    )
                flat_path = _work_dir() / (
                    f"{self._active_path.stem}_flat_{int(duration * 1000)}.mkv"
                )
                logger.info(
                    "Flattening EDL (%d segments) → %s", len(edl.segments), flat_path
                )
                prog.begin_stage("Flattening timeline…", 0.0, 0.55)
                flatten_edit_decision(edl, flat_path, edits=edits, progress=prog)
                prog.end_stage()
                export_source = flat_path
                try:
                    flat_probe = probe_mkv(flat_path)
                    duration = flat_probe.duration_seconds or duration
                except ProbeError:
                    pass
                prog.begin_stage("Exporting…", 0.55, 1.0)

            if out.resolve() == Path(self._active_path).resolve():
                tmp_out = out.with_suffix(".donatello-export.tmp.mkv")
                export_with_track_metadata(
                    export_source,
                    tmp_out,
                    edits,
                    markers=chapter_markers_list,
                    duration=duration,
                    range_start=range_start,
                    range_end=range_end,
                    progress=prog,
                    stream_offsets=stream_offsets,
                )
                tmp_out.replace(out)
                tmp_out = None
            else:
                wrote_direct = True
                export_with_track_metadata(
                    export_source,
                    out,
                    edits,
                    markers=chapter_markers_list,
                    duration=duration,
                    range_start=range_start,
                    range_end=range_end,
                    progress=prog,
                    stream_offsets=stream_offsets,
                )
            if needs_flatten:
                prog.end_stage()
            prog.complete()
            return True

        try:
            outcome = run_with_progress(self, "Exporting…", _job)
            if outcome.cancelled:
                safe_unlink(flat_path, tmp_out)
                if wrote_direct:
                    safe_unlink(out)
                dialogs.show_info(
                    "Export cancelled",
                    "Export was cancelled. The workspace was not changed.",
                    parent=self,
                )
                return
        except (ExportError, CutError) as exc:
            safe_unlink(flat_path, tmp_out)
            if wrote_direct:
                safe_unlink(out)
            _show_error("Export failed", str(exc), parent=self, exc=exc)
            return
        except OSError as exc:
            safe_unlink(flat_path, tmp_out)
            if wrote_direct:
                safe_unlink(out)
            _show_error("Export failed", str(exc), parent=self, exc=exc)
            return

        notes: list[str] = []
        if needs_flatten:
            notes.append("Baked timeline edits into the export.")
        if range_start is not None and range_end is not None:
            notes.append(
                f"Trimmed to In–Out: {format_duration(range_start)}–"
                f"{format_duration(range_end)} "
                f"({format_duration(range_end - range_start)})."
            )
        else:
            notes.append("Full clip (no In/Out set).")
        if chapter_markers_list:
            notes.append(f"Chapters: {len(chapter_markers_list)} named marker(s).")
        else:
            notes.append("No named markers — no chapters written.")
        dialogs.show_info(
            "Export complete",
            "\n".join(notes) + f"\n\n{out}",
            parent=self,
        )
        # After a successful flatten export, adopt identity EDL on the active source
        # only if we exported a bake of the current timeline — keep working on source.
        # Reload preview from active path (EDL preserved until he opens the export).
        self.load_mkv(Path(self._active_path), clear_marks=False)

    def _track_edits_for_export(self, current: ProbeResult) -> list:
        key = _path_key(self._active_path) if self._active_path else ""
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
        return edits

    def export_multiple_dialog(self) -> None:
        """Export several MKVs from split markers (In/Out as virtual bounds first)."""
        if self._active_path is None or self._result is None:
            dialogs.show_warning(
                "Nothing to export",
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return

        self._player.pause()
        self._play_btn.configure(text="Play")

        key = _path_key(self._active_path)
        all_markers = list(self._markers.get(key) or [])
        edl = self._active_sequence()
        needs_flatten = not self._edl_is_identity()
        duration = (
            edl.timeline_duration()
            if needs_flatten and edl is not None
            else float(self._result.duration_seconds or 0.0)
        )
        if duration <= 0:
            dialogs.show_warning(
                "Export multiple",
                "Clip has no usable duration.",
                parent=self,
            )
            return

        segments = build_export_multiple_segments(
            all_markers,
            duration=duration,
            mark_in=self._mark_in,
            mark_out=self._mark_out,
        )
        if not segments:
            has_bounds = self._mark_in is not None or self._mark_out is not None
            if has_bounds:
                dialogs.show_warning(
                    "Export multiple",
                    "No exportable ranges.\n\n"
                    "With Mark In/Out set, set both so Out is after In, "
                    "or add split markers inside that window.",
                    parent=self,
                )
            else:
                dialogs.show_warning(
                    "Export multiple",
                    "Add at least one split marker first "
                    "(Add split — name becomes the output filename).\n\n"
                    "Or set Mark In/Out to export that span as one file.",
                    parent=self,
                )
            return

        first_start = segments[0].start
        if (
            first_start > 0.001
            and app_settings.get_warn_export_multiple_discard_before_first()
        ):
            ok = dialogs.ask_yes_no(
                "Discard before first split?",
                f"Everything before {format_duration(first_start)} will not be "
                f"exported ({len(segments)} file(s) will be written).\n\n"
                "Put a split (or Mark In) at 0:00 if you want that span kept "
                "and named.\n\n"
                "Continue?",
                parent=self,
                ok_text="Export",
            )
            if not ok:
                return

        folder_str = filedialog.askdirectory(
            parent=self,
            title="Export multiple — choose output folder",
            initialdir=app_settings.export_dialog_initialdir(),
        )
        if not folder_str:
            return
        out_dir = Path(folder_str)
        if not out_dir.is_dir():
            dialogs.show_warning(
                "Export multiple",
                "Chosen path is not a folder.",
                parent=self,
            )
            return

        try:
            current = probe_mkv(self._active_path)
        except ProbeError as exc:
            _show_error("Export multiple failed", str(exc), parent=self, exc=exc)
            return

        edits = self._track_edits_for_export(current)
        stream_offsets = copy_track_offsets(
            self._track_offsets.get(_path_key(self._active_path)) or {}
        )
        flat_path: Path | None = None
        export_source = Path(self._active_path)
        written: list[Path] = []
        # Mutable bag so the worker can update segments after flatten
        bag: dict = {
            "segments": segments,
            "duration": duration,
            "export_source": export_source,
            "flat_path": None,
            "written": written,
            "stream_offsets": stream_offsets,
        }

        def _job(prog) -> list[Path]:
            segs = bag["segments"]
            dur = bag["duration"]
            src = bag["export_source"]
            flat_share = 0.35 if needs_flatten else 0.0
            if needs_flatten:
                if edl is None or not edl.segments:
                    raise ExportError(
                        "Timeline edit decision is empty — nothing to export."
                    )
                fp = _work_dir() / (
                    f"{self._active_path.stem}_flat_{int(dur * 1000)}.mkv"
                )
                bag["flat_path"] = fp
                logger.info(
                    "Flattening EDL for Export multiple (%d segments) → %s",
                    len(edl.segments),
                    fp,
                )
                prog.begin_stage("Flattening timeline…", 0.0, flat_share)
                flatten_edit_decision(edl, fp, edits=edits, progress=prog)
                prog.end_stage()
                src = fp
                bag["export_source"] = src
                try:
                    flat_probe = probe_mkv(fp)
                    dur = flat_probe.duration_seconds or dur
                    bag["duration"] = dur
                except ProbeError:
                    pass
                segs = build_export_multiple_segments(
                    all_markers,
                    duration=dur,
                    mark_in=self._mark_in,
                    mark_out=self._mark_out,
                )
                bag["segments"] = segs
                if not segs:
                    raise ExportError("No exportable ranges after flattening.")

            logger.info(
                "Export multiple: %d file(s) from %s → %s",
                len(segs),
                self._active_path.name,
                out_dir,
            )
            total = len(segs)
            for i, seg in enumerate(segs):
                prog.check()
                out = out_dir / f"{seg.stem}.mkv"
                if out.exists():
                    n = 2
                    while True:
                        candidate = out_dir / f"{seg.stem}_{n}.mkv"
                        if not candidate.exists():
                            out = candidate
                            break
                        n += 1
                lo = flat_share + (1.0 - flat_share) * (i / max(1, total))
                hi = flat_share + (1.0 - flat_share) * ((i + 1) / max(1, total))
                prog.begin_stage(f"Exporting {i + 1} of {total}…", lo, hi)
                export_with_track_metadata(
                    src,
                    out,
                    edits,
                    markers=all_markers,
                    duration=dur,
                    range_start=seg.start,
                    range_end=seg.end,
                    progress=prog,
                    stream_offsets=bag.get("stream_offsets") or {},
                )
                prog.end_stage()
                written.append(out)
            prog.complete()
            return written

        try:
            outcome = run_with_progress(self, "Export multiple…", _job)
            if outcome.cancelled:
                safe_unlink(bag.get("flat_path"), *written)
                dialogs.show_info(
                    "Export multiple cancelled",
                    "Export was cancelled. Partial output files were removed.\n"
                    "The workspace was not changed.",
                    parent=self,
                )
                return
            written = list(outcome.value or [])
        except (ExportError, CutError) as exc:
            safe_unlink(bag.get("flat_path"), *written)
            _show_error("Export multiple failed", str(exc), parent=self, exc=exc)
            return
        except OSError as exc:
            safe_unlink(bag.get("flat_path"), *written)
            _show_error("Export multiple failed", str(exc), parent=self, exc=exc)
            return

        lines = [
            f"Wrote {len(written)} file(s).",
        ]
        if needs_flatten:
            lines.append("Baked timeline edits into each export.")
        if self._mark_in is not None or self._mark_out is not None:
            lines.append(
                "Mark In/Out used as outer bounds; split markers cut inside."
            )
        if first_start > 0.001:
            lines.append(
                f"Discarded media before {format_duration(first_start)}."
            )
        preview = "\n".join(p.name for p in written[:8])
        if len(written) > 8:
            preview += f"\n… and {len(written) - 8} more"
        dialogs.show_info(
            "Export multiple complete",
            "\n".join(lines) + f"\n\n{out_dir}\n\n{preview}",
            parent=self,
        )
        self.load_mkv(Path(self._active_path), clear_marks=False)

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
        else:
            self._refresh_window_title()

    # ── timeline / streams ──────────────────────────────────────────────

    def _show_empty_state(self) -> None:
        self._lanes.clear()
        self._clear_marks()
        self._selected_stream_index = None
        self._monitor_audio_stream_index = None
        self._visible_subtitle_stream_index = None
        self._reset_preview_ui()
        self._refresh_preview_track_menus()
        self._refresh_window_title()

    def _reset_preview_ui(self) -> None:
        self._player.close()
        self._preview_media_path = None
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
        file_duration = result.duration_seconds or 0.0
        self._sync_preview_monitor()
        self._player.open(result.path, file_duration if file_duration > 0 else None)
        self._preview_media_path = result.path
        timeline_dur = self._timeline_duration()
        scrub_to = max(timeline_dur if timeline_dur > 0 else file_duration, 0.1)
        self._scrub.configure(state="normal", to=scrub_to)
        self._updating_scrub = True
        self._scrub.set(0)
        self._updating_scrub = False
        self._play_btn.configure(text="Play")
        self._time_label.configure(
            text=f"0:00 / {format_duration(timeline_dur if timeline_dur > 0 else file_duration)}"
        )
        self._refresh_skip_button_labels()

    def _ensure_preview_source(self, path: Path) -> bool:
        """Open *path* in the preview player if it is not already open."""
        try:
            want = _path_key(path)
        except OSError:
            want = str(path)
        if self._preview_media_path is not None:
            try:
                if _path_key(self._preview_media_path) == want:
                    return True
            except OSError:
                if str(self._preview_media_path) == str(path):
                    return True
        try:
            result = probe_mkv(path)
        except ProbeError as exc:
            logger.error("Could not open preview source %s: %s", path, exc)
            return False
        was_playing = self._player.playing
        dur = result.duration_seconds or 0.0
        self._sync_preview_monitor()
        self._player.open(result.path, dur if dur > 0 else None)
        self._preview_media_path = result.path
        if was_playing:
            self._player.play()
            self._play_btn.configure(text="Pause")
        return True

    def _timeline_duration(self) -> float:
        edl = self._active_sequence()
        if edl is not None:
            d = edl.timeline_duration()
            if d > 0:
                return d
        if self._result is not None:
            return float(self._result.duration_seconds or 0.0)
        return 0.0

    def _edl_is_identity(self) -> bool:
        if self._active_path is None or self._result is None:
            return True
        edl = self._active_sequence()
        if edl is None:
            return True
        return edl.is_identity(
            self._active_path, float(self._result.duration_seconds or 0.0)
        )

    def _preview_open_path(self) -> Path | None:
        return self._preview_media_path or self._active_path

    def _playhead_timeline(self) -> float:
        if self._active_path is None:
            return 0.0
        src_t = float(self._player.position)
        if self._edl_is_identity():
            return src_t
        edl = self._active_sequence()
        open_path = self._preview_open_path()
        if edl is None or open_path is None:
            return src_t
        resolved = edl.resolve_playback(open_path, src_t)
        if resolved is None:
            return edl.timeline_duration()
        return resolved[2]

    def _seek_timeline(self, timeline_t: float) -> None:
        if self._active_path is None:
            return
        edl = self._active_sequence()
        if edl is None or self._edl_is_identity():
            self._ensure_preview_source(self._active_path)
            self._player.show_frame_at(max(0.0, float(timeline_t)))
            return
        mapped = edl.map_timeline_to_source(float(timeline_t))
        if mapped is None:
            return
        source, src_t = mapped
        if not self._ensure_preview_source(source):
            return
        self._player.show_frame_at(src_t)

    def _sync_scrubber_duration(self) -> None:
        dur = self._timeline_duration()
        if dur <= 0:
            return
        self._scrub.configure(state="normal", to=max(dur, 0.1))
        self._time_label.configure(
            text=f"{format_duration(self._playhead_timeline())} / {format_duration(dur)}"
        )

    def _remap_markers_after_remove(self, key: str, start: float, end: float) -> None:
        removed = max(0.0, float(end) - float(start))
        marks = list(self._markers.get(key) or [])
        kept: list[TimelineMarker] = []
        for mark in marks:
            if mark.time < start - 1e-6:
                kept.append(mark)
            elif mark.time >= end - 1e-6:
                kept.append(
                    TimelineMarker(
                        time=max(0.0, mark.time - removed),
                        name=mark.name,
                        kind=mark.kind,
                    )
                )
        self._markers[key] = kept

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
            self._seek_timeline(0.0)

    def _step_frames(self, direction: int) -> None:
        if self._active_path is None:
            return
        self._play_btn.configure(text="Play")
        if self._edl_is_identity():
            self._player.step_frames(direction)
            return
        delta = direction * float(self._player.frame_duration)
        self._seek_timeline(self._playhead_timeline() + delta)

    def _step_seconds(self, direction: int) -> None:
        if self._active_path is None:
            return
        skip = app_settings.get_preview_skip_seconds()
        self._play_btn.configure(text="Play")
        if self._edl_is_identity():
            self._player.step_seconds(direction * skip)
            return
        self._seek_timeline(self._playhead_timeline() + direction * skip)

    def _goto_marker(self, direction: int) -> None:
        """Jump to previous (-1) or next (+1) named marker relative to playhead."""
        if self._active_path is None:
            return
        markers = sorted(self._active_marker_list(), key=lambda m: (m.time, m.name.lower()))
        if not markers:
            return
        t = float(self._playhead_timeline())
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
        self._seek_timeline(float(target.time))

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
        self._seek_timeline(seconds)

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
        tl_dur = self._timeline_duration()
        display_t = float(seconds)
        if self._active_path is not None and not self._edl_is_identity():
            edl = self._active_sequence()
            open_path = self._preview_open_path()
            if edl is not None and open_path is not None:
                resolved = edl.resolve_playback(open_path, float(seconds))
                if resolved is None:
                    self._player.pause()
                    self._play_btn.configure(text="Play")
                    display_t = tl_dur
                else:
                    media_path, src_t, display_t = resolved
                    need_switch = False
                    try:
                        need_switch = _path_key(media_path) != _path_key(open_path)
                    except OSError:
                        need_switch = str(media_path) != str(open_path)
                    if need_switch or abs(src_t - float(seconds)) > 0.05:
                        was_playing = self._player.playing
                        if not self._ensure_preview_source(media_path):
                            return
                        self._player.show_frame_at(src_t)
                        if was_playing:
                            self._player.play()
                            self._play_btn.configure(text="Pause")
                        return

        self._time_label.configure(
            text=f"{format_duration(display_t)} / {format_duration(tl_dur or self._player.duration)}"
        )
        if tl_dur and tl_dur > 0:
            self._updating_scrub = True
            self._scrub.set(min(display_t, tl_dur))
            self._updating_scrub = False
        if hasattr(self, "_lanes"):
            self._lanes.set_position(display_t)

    def _on_preview_ended(self) -> None:
        self._play_btn.configure(text="Play")

    def _on_preview_error(self, message: str) -> None:
        self.after(0, lambda m=message: dialogs.show_warning("Preview", m, parent=self))

    def _poll_preview(self) -> None:
        self._player.poll()
        self.after(33, self._poll_preview)

    def _on_close(self) -> None:
        if not self._confirm_leave_if_dirty():
            return
        try:
            mark_clean_quit()
        except Exception:
            logger.exception("Could not mark clean quit")
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
            self._autosave_job = None
        self._player.close()
        self.destroy()

    def _reschedule_autosave(self) -> None:
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
            self._autosave_job = None
        interval = app_settings.get_autosave_interval_seconds()
        if interval <= 0:
            return
        self._autosave_job = self.after(interval * 1000, self._autosave_tick)

    def _autosave_tick(self) -> None:
        self._autosave_job = None
        try:
            if self._is_workspace_dirty():
                write_autosave(
                    self._capture_workspace_state(),
                    linked_workspace=self._workspace_path,
                )
        except Exception:
            logger.exception("Autosave failed")
        self._reschedule_autosave()

    def _maybe_offer_autosave_restore(self) -> None:
        if not should_offer_crash_restore():
            return
        ok = dialogs.ask_yes_no(
            "Restore Autosave?",
            "Donatello may have closed unexpectedly.\n\n"
            "An Autosave recovery slot is available "
            "(separate from your manual Save).\n\n"
            "Restore the autosave now?",
            parent=self,
            ok_text="Restore Autosave",
        )
        if ok:
            self._restore_autosave()

    def restore_autosave_dialog(self) -> None:
        if not autosave_exists():
            dialogs.show_info(
                "Restore Autosave",
                "No autosave found yet.\n\n"
                "Autosave writes a separate recovery file while you work "
                "(Settings → Folders → interval).",
                parent=self,
            )
            return
        if not self._confirm_leave_if_dirty():
            return
        ok = dialogs.ask_yes_no(
            "Restore Autosave?",
            "Load the Autosave recovery slot?\n\n"
            "This does not overwrite your manual workspace file. "
            "Use Save / Save As afterward to keep changes.",
            parent=self,
            ok_text="Restore Autosave",
        )
        if ok:
            self._restore_autosave()

    def _restore_autosave(self) -> None:
        """Load autosave state; keep manual workspace path as link only (never the slot)."""
        try:
            # Validate file via dedicated loader
            load_autosave_state()
        except WorkspaceError as exc:
            _show_error("Restore Autosave failed", str(exc), parent=self, exc=exc)
            return
        path = autosave_workspace_path()
        self._open_workspace(path, from_autosave=True)

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
        clear_undo: bool = True,
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
        self._restore_preview_track_picks(result)
        self._ensure_edits(result)
        self._seed_markers_from_probe(result)
        self._ensure_sequence(result)
        if clear_undo:
            self._undo_stack.clear()
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
            TimelineMarker(time=m.time, name=m.name, kind=MARKER_KIND_CHAPTER)
            for m in result.chapters
        ]
        logger.info(
            "Loaded %d chapter marker(s) from %s",
            len(self._markers[key]),
            result.path.name,
        )

    def _ensure_sequence(self, result: ProbeResult) -> EditDecision:
        """Default EDL = one segment covering the whole active file."""
        key = _path_key(result.path)
        existing = self._sequences.get(key)
        if existing is not None and existing.segments:
            return existing
        # Try alternate keys from a saved workspace
        for alt_key, edl in list(self._sequences.items()):
            try:
                if _path_key(Path(alt_key)) == key and edl.segments:
                    self._sequences[key] = edl
                    return edl
            except OSError:
                continue
        edl = EditDecision.single_clip(
            result.path, float(result.duration_seconds or 0.0)
        )
        self._sequences[key] = edl
        return edl

    def _active_sequence(self) -> EditDecision | None:
        if self._active_path is None:
            return None
        key = _path_key(self._active_path)
        edl = self._sequences.get(key)
        if edl is not None and edl.segments:
            return edl
        if self._result is not None:
            return self._ensure_sequence(self._result)
        return None

    def _capture_checkpoint(self) -> TimelineCheckpoint | None:
        if self._active_path is None or self._result is None:
            return None
        key = _path_key(self._active_path)
        edl = self._ensure_sequence(self._result)
        edits = self._ensure_edits(self._result)
        return TimelineCheckpoint(
            media_key=key,
            segments=copy_edl(edl),
            markers=copy_markers(list(self._markers.get(key) or [])),
            mark_in=self._mark_in,
            mark_out=self._mark_out,
            playhead=float(self._playhead_timeline()),
            track_edits=copy_track_edits(edits),
            audio_volumes=copy_audio_volumes(self._audio_volumes.get(key) or {}),
            track_offsets=copy_track_offsets(self._track_offsets.get(key) or {}),
        )

    def _push_undo(self) -> None:
        cp = self._capture_checkpoint()
        if cp is not None:
            self._undo_stack.push(cp)

    def _apply_checkpoint(self, cp: TimelineCheckpoint) -> None:
        """Restore a checkpoint; reloads the clip if undo steps across a bake."""
        want = Path(cp.media_key)
        need_reload = False
        if self._active_path is None:
            need_reload = True
        else:
            try:
                need_reload = _path_key(self._active_path) != cp.media_key
            except OSError:
                need_reload = str(self._active_path) != cp.media_key

        if need_reload:
            if not want.is_file():
                dialogs.show_warning(
                    "Undo",
                    "Cannot restore the previous clip — the file is missing:\n"
                    f"{want}",
                    parent=self,
                )
                return
            self.load_mkv(want, clear_marks=False, clear_undo=False)

        self._sequences[cp.media_key] = cp.edl()
        self._markers[cp.media_key] = copy_markers(cp.markers)
        self._edits[cp.media_key] = copy_track_edits(cp.track_edits)
        self._audio_volumes[cp.media_key] = copy_audio_volumes(cp.audio_volumes)
        self._track_offsets[cp.media_key] = copy_track_offsets(cp.track_offsets)
        self._mark_in = cp.mark_in
        self._mark_out = cp.mark_out
        self._update_marks_label()
        self._sync_scrubber_duration()
        self._refresh_timeline_lanes()
        self._sync_preview_monitor()
        self._seek_timeline(cp.playhead)
        self._refresh_window_title()

    def _undo_edit(self) -> None:
        if self._active_path is None:
            return
        if not self._undo_stack.can_undo:
            return
        current = self._capture_checkpoint()
        if current is None:
            return
        prev = self._undo_stack.undo(current)
        if prev is None:
            return
        logger.info("Undo timeline edit")
        self._apply_checkpoint(prev)

    def _redo_edit(self) -> None:
        if self._active_path is None:
            return
        if not self._undo_stack.can_redo:
            return
        current = self._capture_checkpoint()
        if current is None:
            return
        nxt = self._undo_stack.redo(current)
        if nxt is None:
            return
        logger.info("Redo timeline edit")
        self._apply_checkpoint(nxt)

    def _apply_result(self, result: ProbeResult) -> None:
        self._ensure_edits(result)
        self._refresh_timeline_lanes()
        self._sync_preview_monitor()

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
            self._refresh_window_title()
            return
        if (
            self._monitor_audio_stream_index is not None
            and self._monitor_audio_stream_index == stream_index
        ):
            self._player.set_volume(value)
        self._refresh_window_title()

    def _track_offset_map(self) -> dict[int, float]:
        if self._active_path is None:
            return {}
        key = _path_key(self._active_path)
        return self._track_offsets.setdefault(key, {})

    def _get_track_offset(self, stream_index: int) -> float:
        return float(self._track_offset_map().get(stream_index, 0.0))

    def _set_track_offset(self, stream_index: int, seconds: float) -> None:
        value = float(max(-3600.0, min(3600.0, seconds)))
        offsets = self._track_offset_map()
        if abs(value) < 1e-6:
            offsets.pop(stream_index, None)
        else:
            offsets[stream_index] = value
        self._restart_preview_if_playing()
        self._refresh_window_title()

    def _restore_preview_track_picks(self, result: ProbeResult) -> None:
        """Load Mon/Vis picks for this clip (defaults: first audio, subs off)."""
        key = _path_key(result.path)
        audios = [t for t in result.tracks if t.kind == "audio"]
        saved_audio = self._preview_audio_stream.get(key)
        if saved_audio is not None and any(
            t.stream_index == saved_audio for t in audios
        ):
            self._monitor_audio_stream_index = saved_audio
        elif audios:
            self._monitor_audio_stream_index = audios[0].stream_index
            self._preview_audio_stream[key] = audios[0].stream_index
        else:
            self._monitor_audio_stream_index = None

        subs = [t for t in result.tracks if t.kind == "subtitle"]
        saved_sub = self._preview_subtitle_stream.get(key)
        if saved_sub is not None and any(t.stream_index == saved_sub for t in subs):
            self._visible_subtitle_stream_index = saved_sub
        else:
            self._visible_subtitle_stream_index = None
            self._preview_subtitle_stream.pop(key, None)

    def _sync_preview_monitor(self) -> None:
        """Push Mon/Vis picks into the preview player."""
        if self._result is None:
            self._player.set_audio_type_index(0)
            self._player.set_subtitle_type_index(None)
            self._player.set_volume(1.0)
            self._player.set_audio_sync_offset(0.0)
            self._player.set_subtitle_sync_offset(0.0)
            return

        audios = [t for t in self._result.tracks if t.kind == "audio"]
        if not audios:
            self._player.set_audio_type_index(0)
            self._player.set_volume(1.0)
            self._player.set_audio_sync_offset(0.0)
        else:
            chosen = audios[0]
            if self._monitor_audio_stream_index is not None:
                for track in audios:
                    if track.stream_index == self._monitor_audio_stream_index:
                        chosen = track
                        break
            self._monitor_audio_stream_index = chosen.stream_index
            if self._active_path is not None:
                self._preview_audio_stream[_path_key(self._active_path)] = (
                    chosen.stream_index
                )
            self._player.set_audio_type_index(chosen.type_index)
            self._player.set_volume(self._get_audio_volume(chosen.stream_index))
            self._player.set_audio_sync_offset(
                self._get_track_offset(chosen.stream_index)
            )

        subs = [t for t in self._result.tracks if t.kind == "subtitle"]
        sub_type: int | None = None
        sub_offset = 0.0
        if self._visible_subtitle_stream_index is not None:
            for track in subs:
                if track.stream_index == self._visible_subtitle_stream_index:
                    sub_type = track.type_index
                    sub_offset = self._get_track_offset(track.stream_index)
                    break
            if sub_type is None:
                self._visible_subtitle_stream_index = None
        self._player.set_subtitle_type_index(sub_type)
        self._player.set_subtitle_sync_offset(sub_offset)

    def _restart_preview_if_playing(self) -> None:
        """Re-apply monitor settings; restart so a mid-play change is heard/seen."""
        self._sync_preview_monitor()
        was_playing = self._player.playing
        pos = float(self._player.position)
        if was_playing:
            self._player.pause()
        self._player.show_frame_at(pos)
        if was_playing:
            self._player.play()
            self._play_btn.configure(text="Pause")

    def _monitor_audio_stream(self, stream_index: int) -> None:
        if self._result is None:
            return
        track = next(
            (t for t in self._result.tracks if t.stream_index == stream_index),
            None,
        )
        if track is None or track.kind != "audio":
            return
        self._monitor_audio_stream_index = stream_index
        if self._active_path is not None:
            self._preview_audio_stream[_path_key(self._active_path)] = stream_index
        self._restart_preview_if_playing()
        self._refresh_preview_track_menus()
        self._refresh_window_title()

    def _set_visible_subtitle(self, stream_index: int | None) -> None:
        if self._result is None:
            self._visible_subtitle_stream_index = None
            return
        if stream_index is not None:
            track = next(
                (t for t in self._result.tracks if t.stream_index == stream_index),
                None,
            )
            if track is None or track.kind != "subtitle":
                return
        self._visible_subtitle_stream_index = stream_index
        if self._active_path is not None:
            key = _path_key(self._active_path)
            if stream_index is None:
                self._preview_subtitle_stream.pop(key, None)
            else:
                self._preview_subtitle_stream[key] = stream_index
        self._restart_preview_if_playing()
        self._refresh_preview_track_menus()
        self._refresh_window_title()

    def _preview_track_menu_label(self, track: MediaTrack) -> str:
        label = track.display_label()
        if len(label) > 56:
            return label[:55] + "…"
        return label

    def _refresh_preview_track_menus(self) -> None:
        if not hasattr(self, "_preview_audio_menu"):
            return
        self._updating_preview_menus = True
        try:
            if self._result is None:
                self._preview_audio_menu_map = {}
                self._preview_sub_menu_map = {}
                self._preview_audio_menu.configure(
                    values=["(no audio)"], state="disabled"
                )
                self._preview_audio_menu.set("(no audio)")
                self._preview_sub_menu.configure(values=["Off"], state="disabled")
                self._preview_sub_menu.set("Off")
                return

            audios = [t for t in self._result.tracks if t.kind == "audio"]
            audio_map: dict[str, int] = {}
            audio_values: list[str] = []
            for track in audios:
                label = self._preview_track_menu_label(track)
                # Disambiguate duplicate labels
                base = label
                n = 2
                while label in audio_map:
                    label = f"{base} ({n})"
                    n += 1
                audio_map[label] = track.stream_index
                audio_values.append(label)
            self._preview_audio_menu_map = audio_map
            if not audio_values:
                self._preview_audio_menu.configure(
                    values=["(no audio)"], state="disabled"
                )
                self._preview_audio_menu.set("(no audio)")
            else:
                self._preview_audio_menu.configure(values=audio_values, state="normal")
                chosen = next(
                    (
                        label
                        for label, idx in audio_map.items()
                        if idx == self._monitor_audio_stream_index
                    ),
                    audio_values[0],
                )
                self._preview_audio_menu.set(chosen)

            subs = [t for t in self._result.tracks if t.kind == "subtitle"]
            sub_map: dict[str, int] = {}
            sub_values = ["Off"]
            for track in subs:
                label = self._preview_track_menu_label(track)
                base = label
                n = 2
                while label in sub_map or label == "Off":
                    label = f"{base} ({n})"
                    n += 1
                sub_map[label] = track.stream_index
                sub_values.append(label)
            self._preview_sub_menu_map = sub_map
            self._preview_sub_menu.configure(
                values=sub_values,
                state="normal" if subs else "disabled",
            )
            if self._visible_subtitle_stream_index is None:
                self._preview_sub_menu.set("Off")
            else:
                chosen_sub = next(
                    (
                        label
                        for label, idx in sub_map.items()
                        if idx == self._visible_subtitle_stream_index
                    ),
                    "Off",
                )
                self._preview_sub_menu.set(chosen_sub)
        finally:
            self._updating_preview_menus = False

    def _on_preview_audio_menu(self, choice: str) -> None:
        if self._updating_preview_menus:
            return
        stream_index = self._preview_audio_menu_map.get(choice)
        if stream_index is None:
            return
        self._monitor_audio_stream(stream_index)

    def _on_preview_subtitle_menu(self, choice: str) -> None:
        if self._updating_preview_menus:
            return
        if choice == "Off":
            self._set_visible_subtitle(None)
            return
        stream_index = self._preview_sub_menu_map.get(choice)
        if stream_index is None:
            return
        self._set_visible_subtitle(stream_index)

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
        edl = self._ensure_sequence(self._result)
        edl_dur = edl.timeline_duration()
        if edl_dur > 0:
            duration = edl_dur
        spans = [(a, b) for a, b, _seg in edl.timeline_spans()]
        self._lanes.set_tracks(
            lanes,
            duration=duration,
            selected_stream=self._selected_stream_index,
            mark_in=self._mark_in,
            mark_out=self._mark_out,
            markers=list(self._active_marker_list()) if self._active_path else [],
            position=float(self._player.position),
            segment_spans=spans,
        )
        self._refresh_preview_track_menus()
        self._refresh_window_title()

    def _on_segment_reorder(self, from_index: int, drop_timeline_t: float) -> None:
        """Reorder an EDL segment after a lane drag-and-drop."""
        if self._active_path is None or self._result is None:
            return
        edl = self._active_sequence()
        if edl is None or len(edl.segments) < 2:
            self._refresh_timeline_lanes()
            return
        to_index = edl.drop_index_at(drop_timeline_t, moving_index=from_index)
        new_edl = edl.move_segment(from_index, to_index)
        # No-op if order unchanged
        same = len(new_edl.segments) == len(edl.segments) and all(
            a.source == b.source
            and abs(a.src_in - b.src_in) < 1e-6
            and abs(a.src_out - b.src_out) < 1e-6
            for a, b in zip(edl.segments, new_edl.segments)
        )
        if same:
            self._refresh_timeline_lanes()
            return
        self._push_undo()
        key = _path_key(self._active_path)
        self._sequences[key] = new_edl
        logger.info(
            "EDL reorder: %s segment %d → %d (%d segments)",
            self._active_path.name,
            from_index,
            to_index,
            len(new_edl.segments),
        )
        self._refresh_timeline_lanes()

    def _select_stream(self, stream_index: int) -> None:
        self._selected_stream_index = stream_index
        self._update_marks_label()
        if hasattr(self, "_lanes"):
            self._lanes.set_selected(stream_index)
        # Sel is for Delete/Insert only — does not change Mon/Vis.

    def _mark_in_here(self) -> None:
        if self._active_path is None:
            return
        self._mark_in = self._playhead_timeline()
        self._update_marks_label()

    def _mark_out_here(self) -> None:
        if self._active_path is None:
            return
        self._mark_out = self._playhead_timeline()
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
        t = float(self._playhead_timeline())
        n_chapters = sum(
            1 for m in self._active_marker_list() if m.kind == MARKER_KIND_CHAPTER
        )
        default_name = f"Chapter {n_chapters + 1}"
        name = dialogs.ask_string(
            "Add marker",
            f"Chapter name at {format_duration(t)}:",
            initialvalue=default_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip() or default_name
        self._place_chapter_marker(t, name)

    def _drop_named_chapter(self, name: str) -> None:
        """Tools dropper — one-click chapter at the playhead (no prompt)."""
        if self._active_path is None:
            dialogs.show_warning(
                name,
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return
        t = float(self._playhead_timeline())
        self._place_chapter_marker(t, name)

    def _place_chapter_marker(self, time: float, name: str) -> None:
        self._push_undo()
        markers = self._active_marker_list()
        markers.append(
            TimelineMarker(time=float(time), name=name, kind=MARKER_KIND_CHAPTER)
        )
        markers.sort(key=lambda m: (m.time, m.kind, m.name.lower()))
        logger.info(
            "Added chapter marker %r @ %.3f on %s",
            name,
            time,
            self._active_path.name if self._active_path else "?",
        )
        self._update_marks_label()

    def _add_split_here(self) -> None:
        if self._active_path is None:
            dialogs.show_warning(
                "Add split",
                "Load a clip onto the timeline first.",
                parent=self,
            )
            return
        t = float(self._playhead_timeline())
        n_splits = sum(
            1 for m in self._active_marker_list() if m.kind == MARKER_KIND_SPLIT
        )
        default_name = f"Part {n_splits + 1}"
        name = dialogs.ask_string(
            "Add split",
            f"Output filename at {format_duration(t)} (no extension):",
            initialvalue=default_name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip() or default_name
        self._push_undo()
        markers = self._active_marker_list()
        markers.append(TimelineMarker(time=t, name=name, kind=MARKER_KIND_SPLIT))
        markers.sort(key=lambda m: (m.time, m.kind, m.name.lower()))
        logger.info("Added split marker %r @ %.3f on %s", name, t, self._active_path.name)
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
        # One undo step for the whole dialog session (rename/delete/reorder).
        self._push_undo()

        def on_seek(seconds: float) -> None:
            try:
                self._seek_timeline(max(0.0, float(seconds)))
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
        self._refresh_window_title()

    def _do_razor(self) -> None:
        """Split the EDL at the playhead — nothing deleted."""
        if self._active_path is None or self._result is None:
            dialogs.show_warning(
                "Razor", "Load a clip onto the timeline first.", parent=self
            )
            return
        key = _path_key(self._active_path)
        edl = self._ensure_sequence(self._result)
        at = self._playhead_timeline()
        new_edl = edl.split_at(at)
        if new_edl is None:
            dialogs.show_info(
                "Razor",
                "Nothing to split here — playhead is at a segment edge "
                "or the start/end of the timeline.",
                parent=self,
            )
            return
        self._push_undo()
        self._sequences[key] = new_edl
        logger.info(
            "Razor at %.3f on %s → %d segment(s)",
            at,
            self._active_path.name,
            len(new_edl.segments),
        )
        self._refresh_timeline_lanes()
        self._seek_timeline(at)

    def _do_delete(self, single_stream: bool) -> None:
        if self._active_path is None or self._result is None:
            dialogs.show_warning(
                "Delete", "Load a clip onto the timeline first.", parent=self
            )
            return
        if self._mark_in is None or self._mark_out is None:
            dialogs.show_warning(
                "Delete",
                "Set Mark In and Mark Out first.\n\n"
                "Delete removes that span from the timeline.\n"
                "(In/Out also define what Export keeps.)",
                parent=self,
            )
            return
        try:
            cut = CutRange(start=self._mark_in, end=self._mark_out)
        except CutError as exc:
            _show_error("Delete", str(exc), parent=self, exc=exc)
            return

        confirmed = dialogs.ask_yes_no(
            "Delete In→Out?",
            f"Remove {format_duration(cut.start)}–{format_duration(cut.end)} "
            f"from the timeline and close the gap?\n\n"
            "This does not change Mark In/Out’s meaning for Export — "
            "it removes that span from the sequence.",
            parent=self,
            ok_text="Delete",
            ok_danger=True,
        )
        if not confirmed:
            return

        # All-streams Delete updates the EDL. Single-stream still bakes.
        if not single_stream:
            key = _path_key(self._active_path)
            edl = self._ensure_sequence(self._result)
            new_edl = edl.remove_range(cut.start, cut.end)
            if not new_edl.segments:
                dialogs.show_warning(
                    "Delete",
                    "That range would remove the entire timeline.",
                    parent=self,
                )
                return
            self._push_undo()
            self._sequences[key] = new_edl
            self._remap_markers_after_remove(key, cut.start, cut.end)
            land_at = min(cut.start, new_edl.timeline_duration())
            self._clear_marks()
            self._sync_scrubber_duration()
            self._refresh_timeline_lanes()
            self._seek_timeline(land_at)
            logger.info(
                "EDL delete all streams: %s %.3f–%.3f → %d segment(s), duration %.3f",
                self._active_path.name,
                cut.start,
                cut.end,
                len(new_edl.segments),
                new_edl.timeline_duration(),
            )
            dialogs.show_info(
                "Delete complete",
                f"Removed {format_duration(cut.start)}–{format_duration(cut.end)} "
                f"(all streams).\n\n"
                f"Timeline is now {format_duration(new_edl.timeline_duration())} "
                f"in {len(new_edl.segments)} segment(s).\n"
                "Export will bake the edit to a new MKV.",
                parent=self,
            )
            return

        if not self._edl_is_identity():
            dialogs.show_warning(
                "Delete selected",
                "The timeline has pending Razor/Delete edits.\n\n"
                "Export (to bake) before Delete selected.",
                parent=self,
            )
            return

        if self._selected_stream_index is None:
            dialogs.show_warning(
                "Delete selected",
                "Select a video or audio stream first.",
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
            _show_error("Delete selected", "Selected stream not found.", parent=self)
            return

        stem = self._active_path.stem
        out_path = _work_dir() / f"{stem}_del_{int(cut.start * 1000)}_{int(cut.end * 1000)}.mkv"
        key = _path_key(self._active_path)
        edits_map = self._edits.get(key) or edits_from_tracks(self._result.tracks)
        edits = list(edits_map.values())
        audio_tracks = [t for t in self._result.tracks if t.kind == "audio"]

        logger.info(
            "Delete selected track: %s %.3f–%.3f → %s",
            self._active_path.name,
            cut.start,
            cut.end,
            out_path.name,
        )
        try:
            outcome = run_with_progress(
                self,
                "Delete selected…",
                lambda prog: perform_cut(
                    self._active_path,
                    out_path,
                    cut,
                    duration=self._result.duration_seconds,
                    edits=edits,
                    selected_track=selected,
                    audio_tracks=audio_tracks,
                    progress=prog,
                ),
            )
            if outcome.cancelled:
                safe_unlink(out_path)
                dialogs.show_info(
                    "Delete cancelled",
                    "Delete selected was cancelled. The workspace was not changed.",
                    parent=self,
                )
                return
        except CutError as exc:
            safe_unlink(out_path)
            _show_error("Delete failed", str(exc), parent=self, exc=exc)
            return

        dialogs.show_info(
            "Delete complete",
            f"Removed {format_duration(cut.start)}–{format_duration(cut.end)} "
            f"(selected track).\n\nSaved:\n{out_path}",
            parent=self,
        )
        self.import_paths([out_path], load_first_if_empty=False)
        self.load_mkv(out_path)

    def _insert_point(self) -> float:
        if self._mark_in is not None:
            return self._mark_in
        return self._playhead_timeline()

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
            dialogs.show_warning(
                "Insert", "Load a base clip onto the timeline first.", parent=self
            )
            return

        insert_path = self._pick_insert_source()
        if insert_path is None:
            return

        at = self._insert_point()

        # All-streams Insert → EDL (non-destructive)
        if not single_stream:
            if self._insert_clip_into_edl(insert_path, at):
                dialogs.show_info(
                    "Insert complete",
                    f"Inserted “{insert_path.name}” at {format_duration(at)}.\n\n"
                    "Export will bake the sequence to a new MKV.",
                    parent=self,
                )
            return

        # Insert selected still bakes — require a simple identity timeline
        if not self._edl_is_identity():
            dialogs.show_warning(
                "Insert selected",
                "The timeline has pending Razor/Delete/Insert edits.\n\n"
                "Export (to bake) before Insert selected.",
                parent=self,
            )
            return

        if _path_key(insert_path) == _path_key(self._active_path):
            dialogs.show_warning(
                "Insert selected",
                "Pick a different clip than the one already on the timeline.",
                parent=self,
            )
            return

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

        self.import_paths([insert_path], load_first_if_empty=False)

        stem = self._active_path.stem
        out_path = _work_dir() / f"{stem}_ins_{int(at * 1000)}.mkv"
        key = _path_key(self._active_path)
        edits_map = self._edits.get(key) or edits_from_tracks(self._result.tracks)
        edits = list(edits_map.values())
        audio_tracks = [t for t in self._result.tracks if t.kind == "audio"]

        logger.info(
            "Insert selected: base=%s insert=%s at=%.3f → %s",
            self._active_path.name,
            insert_path.name,
            at,
            out_path.name,
        )
        pre_bake = self._capture_checkpoint()
        try:
            outcome = run_with_progress(
                self,
                "Insert selected…",
                lambda prog: perform_insert(
                    self._active_path,
                    insert_path,
                    out_path,
                    at=at,
                    base_duration=self._result.duration_seconds,
                    edits=edits,
                    selected_base_track=selected_base,
                    selected_insert_track=selected_ins,
                    base_audio_tracks=audio_tracks,
                    progress=prog,
                ),
            )
            if outcome.cancelled:
                safe_unlink(out_path)
                dialogs.show_info(
                    "Insert cancelled",
                    "Insert selected was cancelled. The workspace was not changed.",
                    parent=self,
                )
                return
        except CutError as exc:
            safe_unlink(out_path)
            _show_error("Insert failed", str(exc), parent=self, exc=exc)
            return

        dialogs.show_info(
            "Insert complete",
            f"Inserted at {format_duration(at)} (selected track).\n\nSaved:\n{out_path}",
            parent=self,
        )
        self.import_paths([out_path], load_first_if_empty=False)
        self.load_mkv(out_path)
        # One Ctrl+Z restores the pre-bake clip (stack was cleared by load).
        if pre_bake is not None:
            self._undo_stack.push(pre_bake)

    def _insert_clip_into_edl(
        self, insert_path: Path, at: float, *, quiet: bool = False
    ) -> bool:
        """Splice *insert_path* into the active EDL at timeline *at*. Returns success."""
        if self._active_path is None or self._result is None:
            if not quiet:
                dialogs.show_warning(
                    "Insert", "Load a base clip onto the timeline first.", parent=self
                )
            return False
        try:
            ins_probe = probe_mkv(insert_path)
        except ProbeError as exc:
            if not quiet:
                _show_error("Insert", str(exc), parent=self, exc=exc)
            return False
        dur = float(ins_probe.duration_seconds or 0.0)
        if dur <= 0.05:
            if not quiet:
                dialogs.show_warning(
                    "Insert", "Insert clip has no usable duration.", parent=self
                )
            return False

        self.import_paths([insert_path], load_first_if_empty=False)
        key = _path_key(self._active_path)
        edl = self._ensure_sequence(self._result)
        piece = TimelineSegment(
            source=ins_probe.path, src_in=0.0, src_out=dur
        )
        at = max(0.0, min(float(at), edl.timeline_duration()))
        self._push_undo()
        new_edl = edl.insert_at(at, piece)
        self._sequences[key] = new_edl
        self._remap_markers_after_insert(key, at, dur)
        self._remap_marks_after_insert(at, dur)
        logger.info(
            "EDL insert: %s into %s at %.3f (dur %.3f) → %d segments",
            insert_path.name,
            self._active_path.name,
            at,
            dur,
            len(new_edl.segments),
        )
        self._sync_scrubber_duration()
        self._refresh_timeline_lanes()
        self._seek_timeline(at)
        return True

    def _remap_markers_after_insert(self, key: str, at: float, duration: float) -> None:
        marks = list(self._markers.get(key) or [])
        if not marks or duration <= 0:
            return
        shifted: list[TimelineMarker] = []
        for mark in marks:
            if mark.time >= at - 1e-6:
                shifted.append(
                    TimelineMarker(
                        time=mark.time + duration,
                        name=mark.name,
                        kind=mark.kind,
                    )
                )
            else:
                shifted.append(mark)
        self._markers[key] = shifted

    def _remap_marks_after_insert(self, at: float, duration: float) -> None:
        if duration <= 0:
            return
        if self._mark_in is not None and self._mark_in >= at - 1e-6:
            self._mark_in += duration
        if self._mark_out is not None and self._mark_out >= at - 1e-6:
            self._mark_out += duration
        self._update_marks_label()

    def _edit_track(self, stream_index: int) -> None:
        if self._result is None or self._active_path is None:
            return
        edits = self._ensure_edits(self._result)
        edit = edits.get(stream_index)
        track = next((t for t in self._result.tracks if t.stream_index == stream_index), None)
        if edit is None or track is None:
            return

        heading = f"{edit.kind.capitalize()} stream {edit.type_index}  ({track.codec})"
        show_sync = edit.kind in ("audio", "subtitle")
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
            show_sync_offset=show_sync,
            sync_offset=self._get_track_offset(stream_index),
        )
        if result is None:
            return

        self._push_undo()
        edit.title = result.title
        edit.language = result.language
        if edit.kind == "subtitle":
            edit.is_default = result.is_default
            edit.is_forced = result.is_forced
        if edit.kind == "audio" and result.volume is not None:
            self._set_audio_volume(stream_index, result.volume)
        if show_sync and result.sync_offset is not None:
            self._set_track_offset(stream_index, result.sync_offset)
        self._refresh_timeline_lanes()


def run() -> None:
    app = DonatelloApp()
    app.mainloop()
