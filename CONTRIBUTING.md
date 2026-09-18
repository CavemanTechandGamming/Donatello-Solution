# Contributing

Thanks for helping improve **Donatello Solution**. This document is for people developing or packaging the app. End-user download and usage instructions live in [README.md](README.md).

## Repository layout

Keep the **repository root** reserved for project metadata only:

| Path | Purpose |
|------|---------|
| `README.md` | End-user overview |
| `CHANGELOG.md` | Version history — Keep a Changelog + SemVer (**always committed**) |
| `KNOWN_ISSUES.md` | Public acknowledged limitations (when needed; **committed**) |
| `LICENSE` | MIT |
| `CONTRIBUTING.md` | This file |
| `.gitignore` | Must list `LOCAL_NOTES.md` |
| `docs/images/` | Screenshots for the README |
| `src/` | Application source code |
| `scripts/` | Setup / run / build helpers |
| `requirements/` | Python dependency pins |

Do **not** add application code, build outputs, or virtualenvs at the root.

## One job

Edit **MKV** in a dedicated all-in-one editor (grow toward full NLE; ship the locked v1 slice first). **Save** = workspace. **Export** = single or multiple MKV. Do not add non-MKV containers as first-class without an explicit scope change.

## Hard rules

- No phone-home, telemetry, accounts, or automatic update checks
- No AI / Cursor mentions in public docs
- Prefer FFmpeg stream-copy / remux where quality and sync allow; re-encode only when required
- Soft subtitle tracks must stay lined up through cuts and inserts
- Dark mode first (CustomTkinter)

## Local notes

`LOCAL_NOTES.md` is **gitignored**. Do not commit it.

## Versioning

- `0.0.x` = alpha · `0.x.x` (minor ≥ 1) = beta · major ≥ 1 = release
- Bump `__version__` in `src/__init__.py` with every release; keep [CHANGELOG.md](CHANGELOG.md) in sync

## Pull requests

Prefer small, focused PRs. Confirm approach before large UI or pipeline rewrites.
