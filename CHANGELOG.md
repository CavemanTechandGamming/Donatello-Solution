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
- Timeline cut: Mark In/Out; Cut = all streams (copy+concat); Cut selected = one video/audio track
- Insert footage: Insert… at In/playhead (all streams); Insert selected… for one V/A track
- Rotating file log under `%APPDATA%\DonatelloSolution\donatello.log` (Settings → Open log file)
- Workspace save/open: `.donatello` restores project panel, active clip, Mark In/Out, playhead, stream selection, track edits, markers, and audio volumes
- Single Export: remux with track metadata; named markers as MKV chapters; with Mark In/Out set, Export **keeps** that span (Cut still removes it)
- Multi-lane timeline: one row per video / audio / subtitle; title above each bar; Sel / Edit on the lane; Streams panel removed
- Audio volume 0%–200% in the audio Edit dialog (slider + typed percent); preview monitor gain; soft-clips peaks; persisted in the workspace
- Marker strip (gold ticks + names); click a marker on the strip or lane to seek; transport **‹ M** / **M ›** for previous / next marker
- Import MKV chapters as timeline markers on load (when the clip has none in the workspace yet)
- Themed top menu bar (File / Edit / Help); themed About dialog; themed info / warning / error / string-prompt dialogs
- Dark hover tooltips on controls; quieter chrome; active clip / workspace name in window title

### Changed

- README reshaped to the vault README template (current alpha features)

### Fixed

- Export failure on files with softsubs (`-disposition:s:N` instead of invalid `s:s:N`)

### Removed

- Separate Streams list panel (Select / Edit / volume live on timeline lanes)

## [0.0.1] — 2026-09-15

### Added

- Project scaffold (Python desktop layout, docs, gitignore, MIT)
- v1 scope locked: MKV-only timeline cut/insert, workspace save/reopen, MKV export, H.264/H.265 (HEVC 10-bit + AC3 primary), subtitle-aware cuts via FFmpeg
