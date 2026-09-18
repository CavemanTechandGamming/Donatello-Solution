"""Dark-themed top menu bar (replaces native tk.Menu, which ignores CTk colors)."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk


class _MenuPopup(ctk.CTkToplevel):
    """Simple dropdown under a menu button."""

    def __init__(
        self,
        master,
        items: list[tuple[str, Callable[[], None] | None]],
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

        for label, command in items:
            if label == "---":
                ctk.CTkFrame(frame, height=1, fg_color=("#555555", "#555555")).pack(
                    fill="x", padx=8, pady=4
                )
                continue

            def _run(cmd: Callable[[], None] | None = command) -> None:
                self._dismiss()
                if cmd is not None:
                    cmd()

            ctk.CTkButton(
                frame,
                text=label,
                anchor="w",
                height=30,
                corner_radius=4,
                fg_color="transparent",
                hover_color=("#3d3d3d", "#3d3d3d"),
                text_color=("#e8e8e8", "#e8e8e8"),
                command=_run,
            ).pack(fill="x", padx=4, pady=1)

        self.bind("<Escape>", lambda _e: self._dismiss())

    def popup(self, x: int, y: int) -> None:
        self.update_idletasks()
        self.geometry(f"+{x}+{y}")
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
        self._menus: dict[str, list[tuple[str, Callable[[], None] | None]]] = {}

    def add_menu(
        self,
        title: str,
        items: list[tuple[str, Callable[[], None] | None]],
        *,
        last: bool = False,
    ) -> None:
        """Add a top-level menu. Pass last=True for Help (always rightmost)."""
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
