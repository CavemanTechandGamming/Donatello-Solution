"""User-customizable keyboard shortcuts for timeline / Tools actions."""

from __future__ import annotations

from dataclasses import dataclass

from src.core import settings as app_settings

# Stored chord = tk event pattern *inside* angle brackets, e.g. "Key-r", "Control-Left".
# Empty string means unbound.


@dataclass(frozen=True)
class KeyAction:
    id: str
    label: str
    default: str  # chord; never empty for registered actions


# Order = Settings list order.
ACTIONS: tuple[KeyAction, ...] = (
    KeyAction("mark_in", "Mark In", "bracketleft"),
    KeyAction("mark_out", "Mark Out", "bracketright"),
    KeyAction("clear_marks", "Clear marks", "Key-c"),
    KeyAction("add_marker", "Add marker", "Key-m"),
    KeyAction("add_split", "Add split", "Key-s"),
    KeyAction("markers", "Markers", "Control-m"),
    KeyAction("prev_marker", "Previous marker", "Control-Left"),
    KeyAction("next_marker", "Next marker", "Control-Right"),
    KeyAction("razor", "Razor", "Key-r"),
    KeyAction("delete", "Delete", "Delete"),
    KeyAction("delete_selected", "Delete selected", "Shift-Delete"),
    KeyAction("insert", "Insert", "Key-i"),
    KeyAction("insert_selected", "Insert selected", "Shift-I"),
    KeyAction("play_pause", "Play / Pause", "space"),
    KeyAction("stop", "Stop", "Shift-space"),
    KeyAction("prev_frame", "Previous frame", "Left"),
    KeyAction("next_frame", "Next frame", "Right"),
    KeyAction("skip_back", "Skip back", "Down"),
    KeyAction("skip_forward", "Skip forward", "Up"),
    KeyAction("drop_prelog", "Prelog", "Key-1"),
    KeyAction("drop_intro", "Intro", "Key-2"),
    KeyAction("drop_episode", "Episode", "Key-3"),
    KeyAction("drop_credits", "Credits", "Key-4"),
    KeyAction("drop_epilog", "Epilog", "Key-5"),
)

_ACTIONS_BY_ID = {a.id: a for a in ACTIONS}

_DISPLAY_KEYS = {
    "Left": "←",
    "Right": "→",
    "Up": "↑",
    "Down": "↓",
    "bracketleft": "[",
    "bracketright": "]",
    "space": "Space",
    "Return": "Enter",
    "BackSpace": "Backspace",
    "Delete": "Delete",
    "Escape": "Esc",
    "Prior": "Page Up",
    "Next": "Page Down",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "minus": "-",
    "equal": "=",
    "semicolon": ";",
    "apostrophe": "'",
    "grave": "`",
}


def default_bindings() -> dict[str, str]:
    return {a.id: a.default for a in ACTIONS}


def action_by_id(action_id: str) -> KeyAction | None:
    return _ACTIONS_BY_ID.get(action_id)


def get_effective_bindings() -> dict[str, str]:
    """
    Action id → chord. Missing keys use defaults.
    Explicit empty string in settings means unbound.
    """
    overrides = app_settings.get_keybinding_overrides()
    out = default_bindings()
    for action_id, chord in overrides.items():
        if action_id not in _ACTIONS_BY_ID:
            continue
        if chord is None:
            continue
        out[action_id] = str(chord)
    return out


def get_binding(action_id: str) -> str:
    return get_effective_bindings().get(action_id, "")


def set_binding(action_id: str, chord: str) -> None:
    if action_id not in _ACTIONS_BY_ID:
        raise KeyError(f"Unknown action: {action_id}")
    app_settings.set_keybinding_override(action_id, str(chord))


def clear_binding(action_id: str) -> None:
    """Unbind — stores empty string override."""
    set_binding(action_id, "")


def reset_binding(action_id: str) -> None:
    """Remove override so the default applies again."""
    if action_id not in _ACTIONS_BY_ID:
        raise KeyError(f"Unknown action: {action_id}")
    app_settings.clear_keybinding_override(action_id)


def reset_all_bindings() -> None:
    app_settings.clear_all_keybinding_overrides()


def find_conflict(chord: str, *, excluding: str | None = None) -> KeyAction | None:
    """Return another action that already uses *chord*, if any."""
    if not chord:
        return None
    for action_id, bound in get_effective_bindings().items():
        if action_id == excluding:
            continue
        if bound == chord:
            return _ACTIONS_BY_ID[action_id]
    return None


def chord_to_sequence(chord: str) -> str:
    """``Key-r`` → ``<Key-r>``."""
    chord = (chord or "").strip()
    if not chord:
        return ""
    if chord.startswith("<") and chord.endswith(">"):
        return chord
    return f"<{chord}>"


def chord_to_label(chord: str) -> str:
    """Human label for menus / Settings (e.g. Ctrl+←)."""
    chord = (chord or "").strip()
    if not chord:
        return "(none)"
    if chord.startswith("<") and chord.endswith(">"):
        chord = chord[1:-1]

    mods: list[str] = []
    rest = chord
    while True:
        lowered = rest.lower()
        if lowered.startswith("control-"):
            mods.append("Ctrl")
            rest = rest[8:]
            continue
        if lowered.startswith("shift-"):
            mods.append("Shift")
            rest = rest[6:]
            continue
        if lowered.startswith("alt-"):
            mods.append("Alt")
            rest = rest[4:]
            continue
        if lowered.startswith("meta-"):
            mods.append("Meta")
            rest = rest[5:]
            continue
        break

    key = rest
    if key.startswith("Key-") and len(key) > 4:
        key_name = key[4:]
    elif key.startswith("KP_") and len(key) > 3:
        key_name = f"Num {key[3:]}"
    else:
        key_name = key

    display = _DISPLAY_KEYS.get(key_name, key_name)
    if len(display) == 1 and display.isalpha():
        display = display.upper()
    return "+".join([*mods, display])


def event_to_chord(event) -> str | None:
    """
    Build a stored chord from a Tk key event.
    Returns None for modifier-only presses (Shift alone, etc.).
    """
    keysym = str(getattr(event, "keysym", "") or "")
    if not keysym or keysym in (
        "Shift_L",
        "Shift_R",
        "Control_L",
        "Control_R",
        "Alt_L",
        "Alt_R",
        "Meta_L",
        "Meta_R",
        "Caps_Lock",
        "Num_Lock",
        "Scroll_Lock",
        "Win_L",
        "Win_R",
        "App",
    ):
        return None

    state = int(getattr(event, "state", 0) or 0)
    shift = bool(state & 0x0001)
    control = bool(state & 0x0004)
    alt = bool(state & 0x20000) or bool(state & 0x0008)  # platform variance

    # Normalize letter/digit keysyms into stable storage forms.
    mods_letter_shift = False
    if len(keysym) == 1 and keysym.isalpha():
        letter = keysym.lower()
        if control:
            key_part = letter  # Control-m
        elif shift:
            key_part = letter.upper()  # Shift-I
            mods_letter_shift = True
        else:
            key_part = f"Key-{letter}"
    elif len(keysym) == 1 and keysym.isdigit():
        key_part = f"Key-{keysym}"
    elif keysym in ("bracketleft", "bracketright", "space", "Return", "Escape"):
        key_part = keysym
    elif keysym.startswith("KP_"):
        key_part = keysym
    else:
        key_part = keysym

    mods: list[str] = []
    if control:
        mods.append("Control")
    if shift or mods_letter_shift:
        mods.append("Shift")
    if alt:
        mods.append("Alt")

    if not mods:
        return key_part
    return "-".join([*mods, key_part])


def chord_modifier_flags(chord: str) -> dict[str, bool]:
    """Flags for ``_hotkey`` when binding *chord*."""
    chord = (chord or "").strip()
    if chord.startswith("<") and chord.endswith(">"):
        chord = chord[1:-1]
    has_shift = "Shift-" in chord or chord.startswith("Shift-")
    has_control = "Control-" in chord or chord.startswith("Control-")
    # Bare letter/digit / arrows / brackets should ignore Ctrl/Shift so
    # Ctrl+S still saves, Control-Left still reaches prev marker, etc.
    key_only = chord
    for prefix in ("Control-", "Shift-", "Alt-"):
        key_only = key_only.replace(prefix, "")
    bare_key = (not has_shift and not has_control) and (
        key_only.startswith("Key-")
        or key_only in (
            "bracketleft",
            "bracketright",
            "space",
            "Left",
            "Right",
            "Up",
            "Down",
            "Delete",
            "BackSpace",
        )
    )
    return {
        "require_shift": has_shift,
        "require_control": has_control,
        "forbid_shift": bare_key,
        "forbid_control": bare_key or (has_shift and not has_control),
    }
