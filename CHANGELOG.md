# Changelog

All notable changes to Donatello Solution are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Timeline shell: File → Open MKV, duration + track list (video / audio layout / softsubs)
- FFmpeg bootstrap (`static-ffmpeg`) + `ffprobe` MKV probe (`src/core/`)
- Project panel (left): Import + drag-and-drop MKVs (`tkinterdnd2`); click item to load on timeline
- Settings: tabbed **Folders / Preview / Audio / Help** — default workspace/export folders; preview skip seconds; Audio = output device (System default or pinned) + sample rate + downmix Stereo / Mono / **Keep channels** (monitor only)
- Track edit (Muxer-style): title + language on video / audio / subtitle; Default / Forced on subs; written on Export remux
- In-app preview: Play / Pause / Stop + scrub (PyAV; HEVC 10-bit + AC3); aspect-fit; ±1 frame; ±skip seconds; decode off UI thread
- Preview monitor audio: resample to float @ Settings sample rate; soft clip; Keep channels uses source layout when the device allows (else stereo fallback); Export never downmixes
- **Razor** — split the timeline at the playhead (nothing deleted)
- **Delete** — remove Mark In→Out from the timeline after confirm (all streams via EDL; Delete selected still bakes one V/A track)
- **Insert (EDL)** — splice another MKV into the sequence at In/playhead without baking; multi-source preview; Export flattens
- Project **bin → timeline** drag (and OS-drop onto the timeline) inserts at the drop/playhead point
- Insert footage: Insert selected… still bakes one matching V/A track when the timeline is a single full clip
- Rotating file log under `%APPDATA%\DonatelloSolution\donatello.log` (Settings → Open log file)
- Workspace save/open: `.donatello` restores project panel, active clip, Mark In/Out, playhead, stream selection, track edits, markers, audio volumes, and timeline **sequence** (EDL)
- Edit decision list (`src/core/sequence.py`): multi-segment timeline; Export flattens pending edits then remuxes
- Single Export: remux with track metadata; named markers as MKV chapters; with Mark In/Out set, Export **keeps** that span (before In / after Out dropped)
- Multi-lane timeline: one row per video / audio / subtitle; title above each bar; Sel / Edit on the lane; Streams panel removed; multi-rect segments; **drag to reorder** chunks; **bin drop** onto lanes to insert
- Audio volume 0%–200% in the audio Edit dialog (slider + typed percent); preview monitor gain; soft-clips peaks; persisted in the workspace
- Marker strip (gold ticks + names); click a marker on the strip or lane to seek; transport **‹ M** / **M ›** for previous / next marker
- Import MKV chapters as timeline markers on load (when the clip has none in the workspace yet)
- **Undo / Redo** — Edit menu + Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z); covers Razor, Delete (all-streams), EDL Insert, segment reorder; stack clears on load clip / open workspace
- **Split markers** + **Add split** (blue on the strip); chapter markers stay gold
- **Export multiple…** — File menu; folder of MKVs from split markers; Mark In/Out as outer bounds first; discard-before-first warn (Settings → Folders, default on); chapters inside each range remapped to 0
- **Warn on unsaved workspace** — Save / Don't save / Cancel when closing or opening another workspace; title shows `•` when dirty (playhead scrub alone does not dirty)
- **Autosave** — separate recovery slot under app data (never overwrites manual Save); interval when dirty (Settings → Folders, default 60s, 0 = off); File → Restore Autosave…; offer restore after an unclean quit
- **Tools menu** — Mark In / Out; named chapter droppers **Prelog / Intro / Episode / Credits / Epilog** at the playhead; accelerators `1`–`5`, `[`, `]`
- Native system menu bar (File / Edit / Tools / Help); themed About dialog; themed info / warning / error / string-prompt / yes-no dialogs
- Dark hover tooltips on controls; quieter chrome; active clip / workspace name in window title

### Changed

- README reshaped to the vault README template (current alpha features)
- **Mark In / Out** documented as the export work area (and Delete range) — not “delete marks” by themselves
- Former **Cut** control renamed to **Delete** (with confirm); split-without-delete is **Razor**
- Menu bar uses native `tk.Menu` instead of a custom themed bar (system colors; click to activate, then hover switches — normal Windows behavior)

### Fixed

- Export failure on files with softsubs (`-disposition:s:N` instead of invalid `s:s:N`)

### Removed

- Separate Streams list panel (Select / Edit / volume live on timeline lanes)
- Timeline **Cut** button label (replaced by Razor + Delete)
- Custom dark-themed `ThemedMenuBar` (replaced by `NativeMenuBar`)

## [0.0.1] — 2026-09-15

### Added

- Project scaffold (Python desktop layout, docs, gitignore, MIT)
- v1 scope locked: MKV-only timeline cut/insert, workspace save/reopen, MKV export, H.264/H.265 (HEVC 10-bit + AC3 primary), subtitle-aware cuts via FFmpeg
