"""Native tk.Menu bar (system look; reliable open/dismiss on Windows)."""

from __future__ import annotations

from collections.abc import Callable

import tkinter as tk

# (label, command) or (label, command, accelerator)
# or (label, command, accelerator, "check", BooleanVar)
# or ("---", None)
MenuItem = tuple


def _parse_item(
    item: MenuItem,
) -> tuple[str, Callable[[], None] | None, str | None, str | None, tk.Variable | None]:
    if not item:
        return "---", None, None, None, None
    label = item[0]
    if label == "---":
        return "---", None, None, None, None
    command = item[1] if len(item) > 1 else None
    accel = item[2] if len(item) > 2 else None
    kind = item[3] if len(item) > 3 else None
    var = item[4] if len(item) > 4 else None
    return (
        str(label),
        command,
        (str(accel) if accel else None),
        (str(kind) if kind else None),
        var,
    )


class NativeMenuBar:
    """
    Thin wrapper around ``tk.Menu`` so callers keep the same item-list API
    as the old themed bar. Attaches via ``root.configure(menu=…)``.
    """

    def __init__(self, master) -> None:
        self._root = master
        self._bar = tk.Menu(master, tearoff=0)
        master.configure(menu=self._bar)

    def add_menu(
        self,
        title: str,
        items: list[MenuItem],
        *,
        last: bool = False,
    ) -> None:
        """Add a top-level cascade. ``last`` is accepted for API compat (unused).

        Items: ``(label, command)``, ``(label, command, accelerator)``,
        ``(label, command, accelerator, \"check\", BooleanVar)``,
        or ``(\"---\", None)``.
        """
        del last  # native menus are left-to-right; Help should be added last
        cascade = tk.Menu(self._bar, tearoff=0)
        for raw in items:
            label, command, accel, kind, var = _parse_item(raw)
            if label == "---":
                cascade.add_separator()
                continue
            kwargs: dict = {"label": label}
            if command is not None:
                kwargs["command"] = command
            if accel:
                kwargs["accelerator"] = accel
            if kind == "check" and var is not None:
                kwargs["variable"] = var
                kwargs["onvalue"] = True
                kwargs["offvalue"] = False
                cascade.add_checkbutton(**kwargs)
            else:
                cascade.add_command(**kwargs)
        self._bar.add_cascade(label=title, menu=cascade)
