"""Dark-theme hover tooltips for CustomTkinter controls."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


class HoverTip:
    """Show a brief tip after a short hover delay. Matches Donatello dark chrome."""

    def __init__(
        self,
        widget,
        text: str,
        *,
        delay_ms: int = 450,
    ) -> None:
        self._widget = widget
        self._text = text.strip()
        self._delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: ctk.CTkToplevel | None = None

        self._bind_tree(widget)

    def set_text(self, text: str) -> None:
        self._text = text.strip()

    def _bind_tree(self, widget) -> None:
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        try:
            for child in widget.winfo_children():
                self._bind_tree(child)
        except Exception:
            pass

    def _on_enter(self, _event=None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay_ms, self._show)

    def _on_leave(self, event=None) -> None:
        # Ignore leave if pointer moved into a child of the same control
        if event is not None:
            try:
                x, y = self._widget.winfo_pointerxy()
                under = self._widget.winfo_containing(x, y)
                if under is not None:
                    w = under
                    while w is not None:
                        if w == self._widget:
                            return
                        w = w.master
            except Exception:
                pass
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _show(self) -> None:
        self._after_id = None
        if not self._text or self._tip is not None:
            return
        try:
            if not self._widget.winfo_exists():
                return
        except tk.TclError:
            return

        tip_win = ctk.CTkToplevel(self._widget)
        tip_win.withdraw()
        tip_win.overrideredirect(True)
        tip_win.attributes("-topmost", True)
        try:
            tip_win.configure(fg_color=("#2b2b2b", "#2b2b2b"))
        except Exception:
            pass

        frame = ctk.CTkFrame(
            tip_win,
            fg_color=("#2b2b2b", "#2b2b2b"),
            border_width=1,
            border_color=("#555555", "#555555"),
            corner_radius=6,
        )
        frame.pack(fill="both", expand=True)

        label = ctk.CTkLabel(
            frame,
            text=self._text,
            text_color=("#e8e8e8", "#e8e8e8"),
            font=ctk.CTkFont(size=12),
            justify="left",
            anchor="w",
            wraplength=280,
        )
        label.pack(padx=10, pady=8)

        tip_win.update_idletasks()
        x = self._widget.winfo_rootx() + 12
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        try:
            screen_h = self._widget.winfo_screenheight()
            if y + tip_win.winfo_reqheight() > screen_h - 8:
                y = self._widget.winfo_rooty() - tip_win.winfo_reqheight() - 6
        except tk.TclError:
            pass
        tip_win.geometry(f"+{x}+{y}")
        tip_win.deiconify()
        self._tip = tip_win


def tip(widget, title: str, detail: str) -> HoverTip:
    """Attach a themed tip: title line + short detail."""
    text = f"{title}\n{detail}".strip() if detail else title
    return HoverTip(widget, text)
