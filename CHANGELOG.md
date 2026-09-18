# Changelog

All notable changes to Donatello Solution are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Timeline shell UI: File → Open MKV, duration + track list (video / audio layout / softsubs), basic timeline strip
- FFmpeg bootstrap (`static-ffmpeg`) + `ffprobe` MKV probe (`src/core/`)
- Project panel (left): Import + drag-and-drop MKVs (`tkinterdnd2`); click item to load on timeline
- Settings: default workspace save + export folders (persisted); Save/Export dialogs start there
- Track edit (Muxer-style): title + language on video/audio/subtitle; Default/Forced on subs; written on Export remux
- In-app preview: Play/Pause/Stop + scrub (PyAV; HEVC 10-bit + AC3); decode off UI thread
- Preview layout: aspect-fit (no crop); frame step ±1f; seconds skip ±N (Settings); compact streams strip
- Timeline cut: Mark In/Out; Cut = all streams (copy+concat); Cut selected = one video/audio track
- Insert footage: Insert… at In/playhead (all streams); Insert selected… for one V/A track
- Rotating file log under `%APPDATA%\DonatelloSolution\donatello.log` (Settings → Open log file); errors/warnings + ffmpeg stderr; crash hook
- Workspace save/open: `.donatello` restores project panel, active timeline clip, Mark In/Out, playhead, stream selection, track edits
- Single Export: timeline clip remux with track metadata; named markers written as MKV chapters (Add marker / Markers…)
- Export In/Out: when Mark In/Out are set, Export keeps that span (shorter file); Cut still removes the span
- UI cleanup: removed path/directory header; quieter chrome; active clip / workspace name in window title
- Preview monitor audio: resample to float @ Settings sample rate; larger buffer (no drop-on-full); soft clip
- Settings: tabbed **Folders / Preview / Audio / Help**; Audio = output (System default follows Windows or pin a device) + sample rate + downmix Stereo / Mono / **Keep channels** (monitor only; Keep uses source layout when the device allows, else stereo fallback)
- Themed top menu bar (File / Edit / Help) matching dark chrome; Help always last
- Themed About dialog (Help → About) matching dark chrome instead of native messagebox
- Themed modal dialogs for info / warning / error / string prompts (replaces native messagebox / simpledialog)
- Fix Export failure on files with softsubs (`-disposition:s:N` instead of invalid `s:s:N`)
- UX lock: Save/Open = workspace; Export = single or multiple MKV (markers/chapters via Export)
- Destination vision: full MKV-dedicated all-in-one editor (grow into it)
- Cut lock: default = all streams together; Ctrl = selected track only
- Audio layouts: preserve stereo · 2.1 · 5.1 · 7.1 (no default downmix)
- Import UX: drag-and-drop (required) + browse/dialog; left project panel; Settings default save/export folders
- Track edit (Muxer-style): title + language by name on **video · audio · subtitle**; Default/Forced on subs

### Changed

### Fixed

### Removed

## [0.0.1] — 2026-09-15

### Added

- Project scaffold (Python desktop layout, docs, gitignore, MIT)
- v1 scope locked: MKV-only timeline cut/insert, workspace save/reopen, MKV export, H.264/H.265 (HEVC 10-bit + AC3 primary), subtitle-aware cuts via FFmpeg
