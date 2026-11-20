"""Defensive guards around CustomTkinter widget lifecycle.

Background
----------
CustomTkinter (observed on 5.2.x) can fire placeholder / textvariable callbacks on a
``CTkEntry`` *after* the widget has been destroyed during a screen change, which raises
``tk.TclError``. The app previously monkey-patched those *private* ``CTkEntry`` methods
inline at the top of ``app.py``.

Why this module exists
----------------------
Overriding third-party library internals couples us to names that can change between
CustomTkinter releases (see the review note: "avoid modifying third-party library internals
where possible"). Two changes reduce that maintenance risk:

1. The override is **isolated** here instead of buried in ``app.py``, and
2. it is **version-aware**: it only installs when the expected private methods still exist on
   the installed CustomTkinter. If a future build renames/removes them, we log a warning and
   skip the patch instead of crashing at import — and ``CafeDesk.report_callback_exception``
   already swallows stray ``tk.TclError``s as a second safety net.

This keeps the original stability benefit (global coverage of every ``CTkEntry``, including
those built directly via ``ctk.CTkEntry(...)``) while degrading gracefully.
"""

import tkinter as tk

import customtkinter as ctk

from logging_utils import logger


def _safe_textvariable_callback(self, var_name, index, mode):
    try:
        if not self.winfo_exists() or not self._entry.winfo_exists():
            return
        if self._textvariable is not None and self._textvariable.get() == "":
            self._activate_placeholder()
    except (AttributeError, tk.TclError):
        # Widget was torn down mid-callback; nothing to do.
        pass


def _safe_activate_placeholder(self):
    try:
        if not self.winfo_exists() or not self._entry.winfo_exists():
            return
        if (
            self._entry.get() == ""
            and self._placeholder_text is not None
            and (self._textvariable is None or self._textvariable == "")
        ):
            self._placeholder_text_active = True
            self._pre_placeholder_arguments = {"show": self._entry.cget("show")}
            self._entry.config(
                fg=self._apply_appearance_mode(self._placeholder_text_color),
                disabledforeground=self._apply_appearance_mode(self._placeholder_text_color),
                show="",
            )
            self._entry.delete(0, tk.END)
            self._entry.insert(0, self._placeholder_text)
    except (AttributeError, tk.TclError):
        pass


_installed = False


def install_ctk_entry_guard() -> bool:
    """Install the CTkEntry lifecycle guard.

    Returns ``True`` if the guard was applied, ``False`` if it was skipped (private methods
    absent on this CustomTkinter build). Idempotent.
    """
    global _installed
    if _installed:
        return True
    _installed = True

    has_textvariable = getattr(ctk.CTkEntry, "_textvariable_callback", None) is not None
    has_placeholder = getattr(ctk.CTkEntry, "_activate_placeholder", None) is not None
    if not (has_textvariable and has_placeholder):
        logger.warning(
            "CustomTkinter CTkEntry internals changed; skipping entry lifecycle guard. "
            "Relying on report_callback_exception TclError handling instead."
        )
        return False

    ctk.CTkEntry._textvariable_callback = _safe_textvariable_callback
    ctk.CTkEntry._activate_placeholder = _safe_activate_placeholder
    logger.debug("Installed CTkEntry lifecycle guard.")
    return True


def safe_destroy(widget) -> None:
    """Destroy a widget defensively; never raise if it is already gone.

    ``widget.destroy()`` on an already-destroyed widget raises ``tk.TclError``. Screen
    teardown calls this so a half-destroyed window can never wedge the UI.
    """
    try:
        if widget is not None and getattr(widget, "winfo_exists", lambda: False)():
            widget.destroy()
    except (tk.TclError, AttributeError):
        pass
