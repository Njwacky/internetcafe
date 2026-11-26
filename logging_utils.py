"""Central logging configuration for Café Desk.

The desktop app previously printed tracebacks straight to stdout/stderr, which does
not exist in the shipped ``--windowed`` build and left no structured record of failures.
This module provides ONE configured logger (`cafedesk`) for the whole app and small
helpers so that exception handling is consistent (see the review note: "introduce
consistent logging and exception handling").

No external config file is required: handlers are attached once, lazily, the first time
this module is imported. Screens/workers simply do ``from logging_utils import logger``.

Two handlers are attached:

* a stream handler (stderr) for development / console runs, and
* a rotating file handler in the app data directory so the shipped ``--windowed`` build
  — which has no console — still leaves a durable record of failures for support.

If the data directory is not writable the file handler is skipped silently and the
stream handler alone is used; logging must never prevent the app from starting.

**Secret redaction (security gate, section 2).** The durable log file is a place a
credential could silently end up: an exception message or a chained traceback may
carry an API key or a URL that contains one. Every record therefore passes through
``_SecretRedactingFormatter``, which removes key-like tokens and any secret that the
app has explicitly registered (``register_secret`` / ``unregister_secret``). This is
defence in depth: even if a future code path builds a message containing the key, the
log never persists it.
"""

import logging
import re
import sys
import threading
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

# Same key-like token shapes the AI providers use; kept in sync with
# ai_safety.redact_secrets so log output matches what the UI shows.
_KEY_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-*]{6,}|sk_[A-Za-z0-9_*]{6,}|gsk_[A-Za-z0-9*]{6,}"
    r"|xai-[A-Za-z0-9*]{6,}|AIza[0-9A-Za-z_\-*]{10,}|hf_[A-Za-z0-9*]{6,})"
)
_REDACTED = "[hidden key]"

_SECRETS: set[str] = set()
_SECRETS_LOCK = threading.Lock()


def register_secret(secret: str) -> None:
    """Remember an exact secret so the log formatter can remove it from records.

    Called when the app holds an API key in memory; call ``unregister_secret`` when
    the key is cleared so the registry does not grow or keep stale material.
    """
    if not secret or len(secret) < 6:
        return
    with _SECRETS_LOCK:
        _SECRETS.add(secret)


def unregister_secret(secret: str) -> None:
    """Drop a previously registered secret (key cleared from memory)."""
    if not secret:
        return
    with _SECRETS_LOCK:
        _SECRETS.discard(secret)


def redact_text(text: str) -> str:
    """Remove registered secrets and key-like tokens from a string."""
    if not text:
        return text
    with _SECRETS_LOCK:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, _REDACTED)
    return _KEY_PATTERN.sub(_REDACTED, text)


class _SecretRedactingFormatter(logging.Formatter):
    """Format a record, then redact secrets from the whole output (incl. tracebacks)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


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
        handler.setFormatter(_SecretRedactingFormatter(_LOG_FORMAT))
        log.addHandler(handler)
    except Exception:
        # Logging must never break startup (e.g. read-only install dir).
        pass


def _add_root_redaction_handler() -> None:
    """Cover third-party/library loggers as well as the app logger.

    Without a handler on the root logger, Python's ``logging.lastResort`` prints
    WARNING+ records from any library logger straight to stderr **without** our
    redacting formatter. One future dependency that logs request details could
    therefore persist a key outside the redaction boundary. Attach a redacting
    stream handler to root once; the app logger keeps ``propagate = False`` so
    app records are not printed twice.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_cafedesk_redacting", False):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_SecretRedactingFormatter(_LOG_FORMAT))
    handler._cafedesk_redacting = True
    root.addHandler(handler)


def _build_logger() -> logging.Logger:
    log = logging.getLogger("cafedesk")
    if log.handlers:
        return log
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_SecretRedactingFormatter(_LOG_FORMAT))
    log.addHandler(handler)
    _add_file_handler(log)
    # INFO keeps the shipped build quiet while still recording real problems.
    log.setLevel(logging.INFO)
    log.propagate = False
    _add_root_redaction_handler()
    return log


logger = _build_logger()


def log_exception(where: str, exc: BaseException | None = None) -> None:
    """Record an unexpected exception with context.

    Called from ``CafeDesk._log_exception`` and startup error handling. When ``exc`` is
    given we log a single line (the exception is already being handled); otherwise we log
    the current traceback. Either way the app keeps running. Records are passed through
    the secret-redacting formatter, so a traceback cannot persist an API key.
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
    record through the logger so the note survives in the log file. The record is
    redacted by the formatter before it is written.
    """
    note = f"{where}: {value}"
    # Redact before the note is stored as well as when it is logged: the note is
    # surfaced in the Help screen, so the storage/display boundary needs the same
    # treatment as the log sink.
    redacted = redact_text(note)
    try:
        if isinstance(store, dict):
            store["_last_gui_error"] = redacted
        else:
            setattr(store, "_last_gui_error", redacted)
    except Exception:
        pass
    logger.error(note)
