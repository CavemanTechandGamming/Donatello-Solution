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
- **In-app preview** — Play / Pause / Stop, scrub, ±1 frame, ±skip seconds (PyAV; HEVC 10-bit + AC3 and friends); arrow keys ←/→ frame, ↑/↓ skip
- **Preview Audio / Subs** — dropdowns above the preview: which audio to hear, which softsub to show (text or PGS); Off hides subs; picks save with the workspace
- **Preview monitor audio** — Settings for output device, sample rate, and downmix (Stereo / Mono / Keep channels); Export never downmixes
- **Mark In / Out** — export work area (keep In→Out; drop before In / after Out). Same marks bound **Delete** when removing a span from the timeline
- **Razor** — split at the playhead (nothing deleted; rearrange segments by drag)
- **Delete** — remove In→Out from the timeline after confirm (all streams by default; Delete selected for one video/audio)
- **Insert** — splice another MKV into the timeline EDL at In or playhead (Export bakes); Insert selected still bakes one V/A stream
- **Bin → timeline** — drag a Project clip onto the lanes (or OS-drop MKVs on the timeline) to insert
- **Undo / Redo** — Edit menu or Ctrl+Z / Ctrl+Y after Razor, Delete, Insert, or segment reorder
- **Track edit** — title and language on video / audio / subtitle; Default / Forced on subs; Edit lives on each timeline lane; written on Export
- **Named markers** — **chapter** markers (gold) become chapters on single Export; opening an MKV that already has chapters loads them as chapter markers
- **Split markers** — **Add split** (blue); title = output filename for Export multiple
- **Multi-lane timeline** — one row per video / audio / subtitle with playhead, In/Out, and markers; click a lane or marker to seek; Sel / Edit on the lane; audio volume (0%–200%) in Edit; **‹ M** / **M ›** jump between markers; drag segments to reorder after Razor/Delete
- **Workspace save / open** — `.donatello` restores the bin, active clip, marks, playhead, selection, track edits, markers, audio volumes, preview Audio/Subs picks, and timeline sequence; warns on close / Open Workspace if unsaved (`•` in the title when dirty)
- **Autosave** — separate recovery slot (does not overwrite manual Save); interval in Settings (default 60s); File → Restore Autosave…; prompt after an unclean quit
- **Export (single MKV)** — remux with track metadata and chapters; prefers stream copy when possible; bakes pending timeline edits; progress dialog with Cancel
- **Export multiple…** — write several MKVs from split markers (In/Out as outer bounds when set); optional warn before discarding media before the first split; progress + Cancel
- **Tools** — Marks / edit / transport / chapter droppers; hide timeline button rows; shortcuts customizable in Settings → Keyboard
- **Settings** — folders, preview, **Keyboard** (change / clear / reset shortcuts), audio, autosave, Export-multiple discard warn, log, **FFmpeg check** (Help tab)
- Dark CustomTkinter chrome with a native system menu bar and themed dialogs
- Softsubs stay lined up through cuts and inserts; **MKV attachments** (fonts, etc.) travel with them on bake/export
- Audio channel layouts preserved on Export: **stereo · 2.1 · 5.1 · 7.1**
- FFmpeg via `static-ffmpeg` for normal use — no separate install required when developing; Help → Check FFmpeg… confirms binaries

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
2. **Preview** with Play / scrub (←/→ frame, ↑/↓ skip); use the **Audio** / **Subs** dropdowns above the preview to pick what you hear and which softsub to show; set **Mark In** and **Mark Out** when you need an export work area.
3. **Razor** to split at the playhead (then drag segments), **Delete** to remove In→Out (confirm), or **Insert** / drag from Project onto the timeline. **Undo** / **Redo** (Ctrl+Z / Ctrl+Y) if you need to step back. Edit track titles and languages as needed.
4. Drop **chapter markers** while watching (Tools droppers or Add marker — chapters on single Export), or **Add split** where each output file should start (name = filename).
5. **Save Workspace** to keep project state, **Export** one MKV, or **Export multiple…** for a folder of split outputs (with marks set, Export keeps In→Out; Export multiple uses In/Out as outer bounds then splits). Closing with unsaved changes prompts Save / Don't save / Cancel.

**Save** = workspace only. **Export** = one MKV. **Export multiple** = several MKVs from splits.

---

## Tips

- Use **Keep channels** in Settings → Audio when you want the preview monitor to leave 5.1 / 7.1 alone (falls back to stereo if the device can’t open that many channels).
- Open the log from Settings → Help if something fails — details live under `%APPDATA%\DonatelloSolution\donatello.log` on Windows.
- Turn off the Export-multiple discard warning in Settings → Folders when cleaning a series the same way every time.
- If open/export fails oddly, use **Help → Check FFmpeg…** (or Settings → Help) to confirm FFmpeg is found before digging the log.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Contributing

Want to build from source or send a pull request? See [CONTRIBUTING.md](CONTRIBUTING.md).
