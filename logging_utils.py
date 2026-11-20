"""Central logging configuration for Café Desk.

The desktop app previously printed tracebacks straight to stdout/stderr, which does not
exist in the shipped ``--windowed`` build and left no structured record of failures. This
module provides ONE configured logger (`cafedesk`) for the whole app and small helpers so
that exception handling is consistent (see the review note: "introduce consistent logging
and exception handling").

No external config file is required: handlers are attached once, lazily, the first time
this module is imported. Screens/workers simply do ``from logging_utils import logger``.

Two handlers are attached:

* a stream handler (stderr) for development / console runs, and
* a rotating file handler in the app data directory so the shipped ``--windowed`` build —
  which has no console — still leaves a durable record of failures for support.

If the data directory is not writable the file handler is skipped silently and the stream
handler alone is used; logging must never prevent the app from starting.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _add_file_handler(log: logging.Logger) -> None:
    """Attach a rotating file handler in the app data dir; never raise."""
    try:
        from settings_store import app_data_dir

        log_dir = app_data_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "cafedesk.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        log.addHandler(handler)
    except Exception:
        # Logging must never break startup (e.g. read-only install dir).
        pass


def _build_logger() -> logging.Logger:
    log = logging.getLogger("cafedesk")
    if log.handlers:
        return log
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    log.addHandler(handler)
    _add_file_handler(log)
    # INFO keeps the shipped build quiet while still recording real problems.
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


logger = _build_logger()


def log_exception(where: str, exc: BaseException | None = None) -> None:
    """Record an unexpected exception with context.

    Called from ``CafeDesk._log_exception`` and startup error handling. When ``exc`` is
    given we log a single line (the exception is already being handled); otherwise we log
    the current traceback. Either way the app keeps running.
    """
    if exc is None:
        logger.exception("Unhandled exception in %s", where)
    else:
        logger.error("Error in %s: %s", where, exc)


def record_last_error(store, where: str, value: str) -> None:
    """Persist a short, human-readable last-error note on ``store`` and log it.

    ``store`` may be a dict (``store["_last_gui_error"]``) or an object such as
    ``CafeDesk`` (``store._last_gui_error``). Keeps the existing ``_last_gui_error``
    bookkeeping that the Help screen and recovery UI surface, but routes the durable
    record through the logger so the note survives in the log file.
    """
    note = f"{where}: {value}"
    try:
        if isinstance(store, dict):
            store["_last_gui_error"] = note
        else:
            setattr(store, "_last_gui_error", note)
    except Exception:
        pass
    logger.error(note)
