# Donatello Solution

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-lightgrey.svg)](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)

Premiere-inspired timeline editor for **MKV only** — cut and assemble MKV the way you’d edit MP4 in a traditional NLE. Powered by FFmpeg.

*(Screenshots will land under `docs/images/` once the UI exists.)*

---

## Features (v1 target)

- Timeline cut (remove sections) and insert footage
- **Default cut = all streams** (video + every audio + every subtitle); **Ctrl+cut = selected track only**
- Softsubs stay lined up through edits and splits (unless Ctrl single-stream edit)
- H.264 and H.265 / HEVC (including **HEVC 10-bit + AC3**, the primary library target)
- Preserve audio channel layouts: **stereo · 2.1 · 5.1 · 7.1** (no surprise downmix by default)
- Preview large MKV rips; drop **named markers** while watching (become chapters on Export)
- **Drag-and-drop** (required) plus browse/dialog import
- **Left project panel** — media imported into this workspace (Premiere-style)
- **Settings** — default workspace save folder + default export folder; **Open log file** for diagnostics
- **Track edit** (like [[Subtitle Muxer]]): title + language by name on **video · audio · subtitle**; Default/Forced on subs
- **Save / Open** = workspace (project state comes back intact)
- **Export** (NLE wording) = produce MKV(s):
  - **Single file** — one MKV (timeline and/or markers as chapters)
  - **Multiple files** — named ranges → separate MKVs
- Long-term: full **MKV-dedicated** all-in-one editor; v1 is the locked slice above
- FFmpeg-backed processing (bundled for normal use when packaging lands)

---

## Download

Releases are not published yet (alpha `0.0.1`). When they exist:

1. Open this repository’s **[Releases](https://github.com/CavemanTechandGamming/Donatello-Solution/releases)** page.
2. Download the build for your OS and run **Donatello Solution**.

---

## How to use (dev)

1. Create a venv and install `requirements/requirements.txt`.
2. From the repo root: `python -m src`
3. **File → Import…** or drop MKVs; click to load. **Preview** Play/scrub. **Add marker** for chapters. **Edit** stream title/language. **Settings** for default folders + log file. **Save Workspace** / **Open Workspace** for project round-trip. **Export** remuxes with metadata (+ chapters from markers). See `LOCAL_NOTES.md` for remaining work.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
