"""Central logging configuration for Café Desk.

The desktop app previously printed tracebacks straight to stdout/stderr, which does not
exist in the shipped ``--windowed`` build and left no structured record of failures. This
module provides ONE configured logger (`cafedesk`) for the whole app and small helpers so
that exception handling is consistent (see the review note: "introduce consistent logging
and exception handling").

No external config file is required: a stream handler is attached once, lazily, the first
time this module is imported. Screens/workers simply do ``from logging_utils import logger``.
"""

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _build_logger() -> logging.Logger:
    log = logging.getLogger("cafedesk")
    if log.handlers:
        return log
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    log.addHandler(handler)
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


def record_last_error(store: dict, where: str, value: str) -> None:
    """Persist a short, human-readable last-error note on ``store`` (e.g. ``self``).

    Keeps the existing ``_last_gui_error`` / ``_last_recovery_error`` bookkeeping that the
    Help screen and recovery UI surface, but routes the durable record through the logger.
    """
    note = f"{where}: {value}"
    try:
        store["_last_gui_error"] = note
    except Exception:
        pass
    logger.error(note)
