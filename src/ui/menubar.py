"""Dark-themed top menu bar (replaces native tk.Menu, which ignores CTk colors)."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

# (label, command) or (label, command, accelerator) or ("---", None)
MenuItem = tuple


def _parse_item(
    item: MenuItem,
) -> tuple[str, Callable[[], None] | None, str | None]:
    if not item:
        return "---", None, None
    label = item[0]
    if label == "---":
        return "---", None, None
    command = item[1] if len(item) > 1 else None
    accel = item[2] if len(item) > 2 else None
    return str(label), command, (str(accel) if accel else None)


class _MenuRow(ctk.CTkFrame):
    """One menu line: action on the left, shortcut flush right (Cursor-style)."""

    def __init__(
        self,
        master,
        label: str,
        *,
        accelerator: str | None = None,
        command: Callable[[], None] | None = None,
        on_pick: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent", height=30, corner_radius=4)
        self.pack_propagate(False)
        self._command = command
        self._on_pick = on_pick
        self._idle = "transparent"
        self._hover = ("#3d3d3d", "#3d3d3d")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._name = ctk.CTkLabel(
            self,
            text=label,
            anchor="w",
            text_color=("#e8e8e8", "#e8e8e8"),
            font=ctk.CTkFont(size=13),
        )
        self._name.grid(row=0, column=0, sticky="ew", padx=(12, 28), pady=4)

        if accelerator:
            self._accel = ctk.CTkLabel(
                self,
                text=accelerator,
                anchor="e",
                text_color=("#9a9a9a", "#9a9a9a"),
                font=ctk.CTkFont(size=12),
            )
            self._accel.grid(row=0, column=1, sticky="e", padx=(0, 12), pady=4)
        else:
            self._accel = None

        for widget in (self, self._name, self._accel):
            if widget is None:
                continue
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event=None) -> None:
        self.configure(fg_color=self._hover)

    def _on_leave(self, _event=None) -> None:
        self.configure(fg_color=self._idle)

    def _on_click(self, _event=None) -> None:
        if self._on_pick is not None:
            self._on_pick()
        if self._command is not None:
            self._command()


class _MenuPopup(ctk.CTkToplevel):
    """Simple dropdown under a menu button."""

    def __init__(
        self,
        master,
        items: list[MenuItem],
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._on_close = on_close
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.configure(fg_color=("#2b2b2b", "#2b2b2b"))
        except Exception:
            pass

        frame = ctk.CTkFrame(
            self,
            fg_color=("#2b2b2b", "#2b2b2b"),
            border_width=1,
            border_color=("#555555", "#555555"),
            corner_radius=6,
        )
        frame.pack(fill="both", expand=True)

        # Width from longest label + accelerator so shortcuts sit on a clean column.
        min_w = 200
        for raw in items:
            label, _cmd, accel = _parse_item(raw)
            if label == "---":
                continue
            est = 12 * len(label) + 24
            if accel:
                est += 12 * len(accel) + 40
            min_w = max(min_w, est)

        for raw in items:
            label, command, accel = _parse_item(raw)
            if label == "---":
                ctk.CTkFrame(frame, height=1, fg_color=("#555555", "#555555")).pack(
                    fill="x", padx=8, pady=4
                )
                continue

            row = _MenuRow(
                frame,
                label,
                accelerator=accel,
                command=command,
                on_pick=self._dismiss,
            )
            row.configure(width=min_w)
            row.pack(fill="x", padx=4, pady=1)

        self.bind("<Escape>", lambda _e: self._dismiss())
        self._min_w = min_w

    def popup(self, x: int, y: int) -> None:
        self.update_idletasks()
        h = max(40, self.winfo_reqheight())
        w = max(self._min_w + 16, self.winfo_reqwidth())
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.focus_force()

    def _dismiss(self) -> None:
        callback = self._on_close
        self._on_close = None
        try:
            self.destroy()
        except Exception:
            pass
        if callback is not None:
            callback()


class ThemedMenuBar(ctk.CTkFrame):
    """
    Horizontal menu: left cluster (File, Edit, …) then Help always last on the right.
    """

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            height=34,
            corner_radius=0,
            fg_color=("gray85", "gray17"),
            **kwargs,
        )
        self.grid_propagate(False)
        self.grid_columnconfigure(1, weight=1)

        self._left = ctk.CTkFrame(self, fg_color="transparent")
        self._left.grid(row=0, column=0, sticky="w", padx=(6, 0), pady=2)

        self._right = ctk.CTkFrame(self, fg_color="transparent")
        self._right.grid(row=0, column=2, sticky="e", padx=(0, 6), pady=2)

        self._open_popup: _MenuPopup | None = None
        self._open_title: str | None = None
        self._menus: dict[str, list[MenuItem]] = {}

    def add_menu(
        self,
        title: str,
        items: list[MenuItem],
        *,
        last: bool = False,
    ) -> None:
        """Add a top-level menu. Pass last=True for Help (always rightmost).

        Items: ``(label, command)``, ``(label, command, accelerator)``,
        or ``(\"---\", None)``.
        """
        self._menus[title] = items
        parent = self._right if last else self._left
        btn = ctk.CTkButton(
            parent,
            text=title,
            width=max(52, 12 * len(title) + 24),
            height=26,
            corner_radius=4,
            fg_color="transparent",
            hover_color=("gray75", "gray25"),
            text_color=("gray10", "gray90"),
            font=ctk.CTkFont(size=13),
        )
        btn.configure(command=lambda t=title, b=btn: self._toggle(t, b))
        btn.pack(side="left", padx=2)

    def _close_popup(self) -> None:
        if self._open_popup is not None:
            try:
                self._open_popup.destroy()
            except Exception:
                pass
        self._open_popup = None
        self._open_title = None

    def _toggle(self, title: str, button: ctk.CTkButton) -> None:
        if self._open_title == title and self._open_popup is not None:
            self._close_popup()
            return
        self._close_popup()

        items = self._menus.get(title) or []
        popup = _MenuPopup(self, items, on_close=self._close_popup)
        self._open_popup = popup
        self._open_title = title
        self.update_idletasks()
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height() + 2
        popup.popup(x, y)
