# Donatello Solution

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-lightgrey.svg)](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)

Premiere-inspired timeline editor for **MKV only** — cut, assemble, and export with FFmpeg.

*(Main-window screenshot will go here once captured — `docs/images/main-window.png`.)*

---

## Features

- **Project panel** — import MKVs by browse or drag-and-drop; click to load on the timeline
- **In-app preview** — Play / Pause / Stop, scrub, ±1 frame, ±skip seconds (PyAV; HEVC 10-bit + AC3 and friends)
- **Preview monitor audio** — Settings for output device, sample rate, and downmix (Stereo / Mono / Keep channels); Export never downmixes
- **Mark In / Out** — export work area (keep In→Out; drop before In / after Out). Same marks bound **Delete** when removing a span from the timeline
- **Razor** — split at the playhead (nothing deleted; rearrange segments by drag)
- **Delete** — remove In→Out from the timeline after confirm (all streams by default; Delete selected for one video/audio)
- **Insert** — splice another MKV into the timeline EDL at In or playhead (Export bakes); Insert selected still bakes one V/A stream
- **Bin → timeline** — drag a Project clip onto the lanes (or OS-drop MKVs on the timeline) to insert
- **Undo / Redo** — Edit menu or Ctrl+Z / Ctrl+Y after Razor, Delete, Insert, or segment reorder
- **Track edit** — title and language on video / audio / subtitle; Default / Forced on subs; Edit lives on each timeline lane; written on Export
- **Named markers** — become chapters in the exported MKV; opening an MKV that already has chapters loads them as markers
- **Multi-lane timeline** — one row per video / audio / subtitle with playhead, In/Out, and markers; click a lane or marker to seek; Sel / Edit on the lane; audio volume (0%–200%) in Edit; **‹ M** / **M ›** jump between markers; drag segments to reorder after Razor/Delete
- **Workspace save / open** — `.donatello` restores the bin, active clip, marks, playhead, selection, track edits, markers, audio volumes, and timeline sequence
- **Export (single MKV)** — remux with track metadata and chapters; prefers stream copy when possible; bakes pending timeline edits
- **Settings** — default workspace and export folders; open the diagnostic log
- Dark CustomTkinter chrome with a themed menu bar and themed dialogs
- Softsubs stay lined up through cuts and inserts
- Audio channel layouts preserved on Export: **stereo · 2.1 · 5.1 · 7.1**
- FFmpeg via `static-ffmpeg` for normal use — no separate install required when developing

---

## Download

Releases are not published yet (alpha `0.0.1`). When they exist:

1. Open this repository’s **[Releases](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)** page.
2. Download the file for your OS:
   - **Windows portable** — `…-windows-portable.zip` (extract and run the `.exe`)
   - **Windows installer** — `…-windows-setup.exe` (run the Setup wizard)
   - **Mac** — `…-mac-apple-silicon.tar.gz` or `…-mac-intel.tar.gz`
   - **Linux** — `…-<distro>.tar.gz` (e.g. `…-ubuntu.tar.gz`)
3. Extract if needed, then run **Donatello Solution**.

Until then, run from source (see [CONTRIBUTING.md](CONTRIBUTING.md)).

---

## How to use

1. **Import** MKVs into the Project panel (browse or drag-and-drop), then click a clip to load it on the timeline.
2. **Preview** with Play / scrub; set **Mark In** and **Mark Out** when you need an export work area.
3. **Razor** to split at the playhead (then drag segments), **Delete** to remove In→Out (confirm), or **Insert** / drag from Project onto the timeline. **Undo** / **Redo** (Ctrl+Z / Ctrl+Y) if you need to step back. Edit track titles and languages as needed.
4. Drop **named markers** while watching — they become chapters on Export.
5. **Save Workspace** to keep project state, or **Export** a single MKV (with marks set, Export keeps that span; Delete removes a span from the sequence).

**Save** = workspace only. **Export** = produce an MKV.

---

## Tips

- Use **Keep channels** in Settings → Audio when you want the preview monitor to leave 5.1 / 7.1 alone (falls back to stereo if the device can’t open that many channels).
- Open the log from Settings → Help if something fails — details live under `%APPDATA%\DonatelloSolution\donatello.log` on Windows.
- Multi-file Export from split markers is still ahead.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Contributing

Want to build from source or send a pull request? See [CONTRIBUTING.md](CONTRIBUTING.md).
