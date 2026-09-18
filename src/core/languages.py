"""Language names for the track-edit dialog (ISO 639-2 codes FFmpeg/MKV use)."""

from __future__ import annotations

# (name shown in the UI, 3-letter code written into the file)
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("Undetermined", "und"),
    ("English", "eng"),
    ("Spanish", "spa"),
    ("French", "fra"),
    ("German", "deu"),
    ("Italian", "ita"),
    ("Portuguese", "por"),
    ("Japanese", "jpn"),
    ("Korean", "kor"),
    ("Chinese", "chi"),
    ("Russian", "rus"),
    ("Arabic", "ara"),
    ("Hindi", "hin"),
    ("Dutch", "nld"),
    ("Polish", "pol"),
    ("Swedish", "swe"),
    ("Norwegian", "nor"),
    ("Danish", "dan"),
    ("Finnish", "fin"),
    ("Czech", "ces"),
    ("Hungarian", "hun"),
    ("Romanian", "ron"),
    ("Turkish", "tur"),
    ("Greek", "ell"),
    ("Hebrew", "heb"),
    ("Thai", "tha"),
    ("Vietnamese", "vie"),
    ("Indonesian", "ind"),
    ("Ukrainian", "ukr"),
    ("Catalan", "cat"),
    ("Croatian", "hrv"),
    ("Serbian", "srp"),
    ("Slovak", "slk"),
    ("Slovenian", "slv"),
    ("Bulgarian", "bul"),
    ("Albanian", "sqi"),
    ("Latin", "lat"),
)

_ALIASES = {
    "fre": "fra",
    "ger": "deu",
    "dut": "nld",
    "cze": "ces",
    "gre": "ell",
    "chi": "chi",
    "zho": "chi",
    "rum": "ron",
    "slo": "slk",
}


def language_names() -> list[str]:
    return [name for name, _ in LANGUAGES]


def code_from_name(name: str) -> str:
    for display, code in LANGUAGES:
        if display == name:
            return code
    return "und"


def name_from_code(code: str) -> str | None:
    raw = (code or "und").strip().lower()
    raw = _ALIASES.get(raw, raw)
    for display, known in LANGUAGES:
        if known == raw:
            return display
    return None
