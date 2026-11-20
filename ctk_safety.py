"""Defensive guards around CustomTkinter widget lifecycle.

Background
----------
CustomTkinter (observed on 5.2.x) can fire placeholder / textvariable callbacks on a
``CTkEntry`` *after* the widget has been destroyed during a screen change, which raises
``tk.TclError``. The app previously monkey-patched those *private* ``CTkEntry`` methods
inline at the top of ``app.py``.

Why this module no longer patches the library
---------------------------------------------
Overriding third-party library internals couples us to names that can change between
CustomTkinter releases (see the review note: "avoid modifying third-party library
internals where possible"). The patch has therefore been **removed**:

* ``install_ctk_entry_guard()`` is now a no-op kept only for backward compatibility with
  existing call sites. It logs once and returns ``False`` (guard not installed).
* Widget-lifecycle safety now comes from two supported mechanisms instead:

  1. :func:`safe_destroy` — screen teardown destroys children defensively and never
     raises on an already-destroyed widget, and
  2. ``CafeDesk.report_callback_exception`` — the Tk callback watchdog that swallows
     stray ``tk.TclError``s from callbacks firing on destroyed widgets.

Removing the patch eliminates the maintenance risk entirely: a future CustomTkinter
release that renames its private methods can no longer break startup, and the app's
stability no longer depends on library internals.

This module is import-safe without tkinter/customtkinter (e.g. in CI) so the pure
helpers can be unit-tested headlessly.
"""

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - headless CI without Tk
    tk = None  # type: ignore[assignment]

try:
    import customtkinter as ctk  # noqa: F401  (kept for callers that probe versions)
except ImportError:  # pragma: no cover - headless CI without CustomTkinter
    ctk = None  # type: ignore[assignment]

from logging_utils import logger


def install_ctk_entry_guard() -> bool:
    """Backward-compatible no-op.

    Previously installed a version-aware monkey-patch over ``CTkEntry`` private
    methods. The patch was removed to avoid modifying third-party library internals;
    lifecycle safety is now provided by :func:`safe_destroy` plus
    ``report_callback_exception``. Returns ``False`` (no guard installed) and logs at
    debug level so existing call sites keep working unchanged.
    """
    logger.debug(
        "CTkEntry lifecycle guard not installed (library internals left untouched); "
        "relying on safe_destroy + report_callback_exception."
    )
    return False


def safe_destroy(widget) -> None:
    """Destroy a widget defensively; never raise if it is already gone.

    ``widget.destroy()`` on an already-destroyed widget raises ``tk.TclError``. Screen
    teardown calls this so a half-destroyed window can never wedge the UI.
    """
    try:
        if widget is not None and getattr(widget, "winfo_exists", lambda: False)():
            widget.destroy()
    except Exception:
        # tk.TclError when tkinter is present; AttributeError / anything else on a
        # half-torn-down widget. Teardown must never raise.
        pass
