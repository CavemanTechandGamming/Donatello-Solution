"""Themed modal dialogs — dark CustomTkinter chrome (replaces tk messagebox/simpledialog)."""

from __future__ import annotations

import customtkinter as ctk

# (button fg, hover) — app is dark-mode first
_KIND_ACCENT = {
    "info": ("#1f538d", "#14375e"),
    "warning": ("#8a7018", "#5c4a10"),
    "error": ("#8a2f2f", "#5c1f1f"),
}


class _MessageDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        message: str,
        *,
        kind: str = "info",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.minsize(360, 140)
        self.resizable(True, False)
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_set()

        fg, hover = _KIND_ACCENT.get(kind, _KIND_ACCENT["info"])

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16, 8))
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkLabel(
            body,
            text=message,
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=("gray30", "gray70"),
        ).grid(row=1, column=0, sticky="ew")

        ctk.CTkButton(
            self,
            text="OK",
            width=100,
            fg_color=fg,
            hover_color=hover,
            command=self._close,
        ).grid(row=1, column=0, pady=(4, 16))

        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Return>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.update_idletasks()
        w = max(380, min(520, self.winfo_reqwidth() + 24))
        h = max(160, self.winfo_reqheight() + 8)
        self.geometry(f"{w}x{h}")

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class _AskStringDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        prompt: str,
        *,
        initialvalue: str = "",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("420x180")
        self.minsize(360, 160)
        self.resizable(True, False)
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_set()

        self.result: str | None = None

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text=prompt,
            anchor="w",
            justify="left",
            wraplength=380,
            text_color=("gray30", "gray70"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))

        self._entry = ctk.CTkEntry(self)
        self._entry.grid(row=1, column=0, sticky="ew", padx=18, pady=4)
        self._entry.insert(0, initialvalue)
        self._entry.select_range(0, "end")
        self._entry.focus_set()

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="e", padx=18, pady=(12, 16))
        ctk.CTkButton(
            btns,
            text="Cancel",
            width=90,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=self._cancel,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="OK", width=90, command=self._ok).pack(side="left")

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _ok(self) -> None:
        self.result = self._entry.get()
        self._finish()

    def _cancel(self) -> None:
        self.result = None
        self._finish()

    def _finish(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def show_info(title: str, message: str, *, parent=None) -> None:
    dlg = _MessageDialog(parent, title, message, kind="info")
    dlg.wait_window()


def show_warning(title: str, message: str, *, parent=None) -> None:
    dlg = _MessageDialog(parent, title, message, kind="warning")
    dlg.wait_window()


def show_error(title: str, message: str, *, parent=None) -> None:
    dlg = _MessageDialog(parent, title, message, kind="error")
    dlg.wait_window()


def ask_string(
    title: str,
    prompt: str,
    *,
    initialvalue: str = "",
    parent=None,
) -> str | None:
    dlg = _AskStringDialog(parent, title, prompt, initialvalue=initialvalue)
    dlg.wait_window()
    return dlg.result


class _AskYesNoDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        message: str,
        *,
        ok_text: str = "OK",
        ok_danger: bool = False,
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.minsize(360, 140)
        self.resizable(True, False)
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_set()
        self.result = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16, 8))
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(
            body,
            text=message,
            anchor="w",
            justify="left",
            wraplength=420,
            text_color=("gray30", "gray70"),
        ).grid(row=1, column=0, sticky="ew")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=1, column=0, sticky="e", padx=18, pady=(4, 16))
        ctk.CTkButton(
            btns,
            text="Cancel",
            width=100,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=self._no,
        ).pack(side="left", padx=(0, 8))
        if ok_danger:
            ctk.CTkButton(
                btns,
                text=ok_text,
                width=100,
                fg_color="#8a2f2f",
                hover_color="#5c1f1f",
                command=self._yes,
            ).pack(side="left")
        else:
            ctk.CTkButton(btns, text=ok_text, width=100, command=self._yes).pack(
                side="left"
            )

        self.bind("<Escape>", lambda _e: self._no())
        self.protocol("WM_DELETE_WINDOW", self._no)
        self.update_idletasks()
        w = max(380, min(520, self.winfo_reqwidth() + 24))
        h = max(160, self.winfo_reqheight() + 8)
        self.geometry(f"{w}x{h}")

    def _yes(self) -> None:
        self.result = True
        self._finish()

    def _no(self) -> None:
        self.result = False
        self._finish()

    def _finish(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask_yes_no(
    title: str,
    message: str,
    *,
    parent=None,
    ok_text: str = "OK",
    ok_danger: bool = False,
) -> bool:
    dlg = _AskYesNoDialog(
        parent, title, message, ok_text=ok_text, ok_danger=ok_danger
    )
    dlg.wait_window()
    return bool(dlg.result)


class _AskSaveDiscardCancelDialog(ctk.CTkToplevel):
    """Three-way: Save / Don't save / Cancel. result is one of those strings or None."""

    def __init__(self, master, title: str, message: str) -> None:
        super().__init__(master)
        self.title(title)
        self.minsize(400, 160)
        self.resizable(True, False)
        self.result: str | None = None
        if master is not None:
            self.transient(master)
        self.grab_set()
        self.focus_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16, 8))
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            body,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(
            body,
            text=message,
            anchor="w",
            justify="left",
            wraplength=440,
            text_color=("gray30", "gray70"),
        ).grid(row=1, column=0, sticky="ew")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=1, column=0, sticky="e", padx=18, pady=(4, 16))
        ctk.CTkButton(
            btns,
            text="Cancel",
            width=100,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=lambda: self._finish("cancel"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns,
            text="Don't save",
            width=110,
            fg_color=("gray70", "gray35"),
            hover_color=("gray60", "gray45"),
            command=lambda: self._finish("discard"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns,
            text="Save",
            width=100,
            command=lambda: self._finish("save"),
        ).pack(side="left")

        self.bind("<Escape>", lambda _e: self._finish("cancel"))
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish("cancel"))
        self.update_idletasks()
        w = max(420, min(540, self.winfo_reqwidth() + 24))
        h = max(170, self.winfo_reqheight() + 8)
        self.geometry(f"{w}x{h}")

    def _finish(self, choice: str) -> None:
        self.result = choice
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask_save_discard_cancel(
    title: str,
    message: str,
    *,
    parent=None,
) -> str:
    """Return ``\"save\"``, ``\"discard\"``, or ``\"cancel\"``."""
    dlg = _AskSaveDiscardCancelDialog(parent, title, message)
    dlg.wait_window()
    return dlg.result or "cancel"
