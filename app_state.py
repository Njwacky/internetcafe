"""Central application state manager for Café Desk.

CafeDesk previously kept a large, flat bag of session/telemetry state directly on ``self``.
This module collects the *shared, mutable* cross-cutting state — the worker event queue and
the telemetry counters + their lock — into one place so screens and workers read from a
single source of truth (review note: "create a central application state manager").

Design notes
------------
* ``CafeDesk`` still exposes the legacy ``self._telemetry_*`` attribute names and keeps the
  scalar flags (``_telemetry_enabled``, ``_telemetry_inflight``, ``_telemetry_consent_asked``,
  ``_last_telemetry_flush``) on ``self`` for compatibility with the rest of the code and the
  test suite. ``SessionState`` is given the *same* dict/lock objects, so writes through either
  path stay in sync.
* ``SessionState.snapshot`` is a thread-safe read API. The flush path copies counters
  directly while holding the shared lock so taking and clearing a snapshot is atomic.
"""

import threading
from collections import defaultdict


class SessionState:
    """UI-agnostic holder for Café Desk session + telemetry state."""

    def __init__(self, events=None, counts=None, lock=None):
        # `events` is the queue CafeDesk.poll_events drains on the Tk main thread.
        self.events = events
        # Mutable, shared with CafeDesk._telemetry_counts (same object reference).
        self._counts = counts if counts is not None else defaultdict(int)
        # Mutable, shared with CafeDesk._telemetry_lock (same object reference).
        self._lock = lock if lock is not None else threading.Lock()

    def snapshot(self) -> dict:
        """Return a point-in-time copy of the telemetry counters (thread-safe)."""
        with self._lock:
            return dict(self._counts)

    def increment(self, screen_name: str) -> int:
        """Record one visit to ``screen_name``; return the total queued count.

        Thread-safe. This is the write path paired with :meth:`snapshot` (read path)
        so all telemetry mutations go through the state manager.
        """
        with self._lock:
            self._counts[screen_name] = self._counts.get(screen_name, 0) + 1
            return sum(self._counts.values())

    def total_queued(self) -> int:
        with self._lock:
            return sum(self._counts.values())
