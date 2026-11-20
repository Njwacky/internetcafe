"""Dedicated background workers for Café Desk.

Long-running or network-bound work must stay off the Tk main thread so the desktop never
blocks on I/O. Results are posted back through the event queue that ``CafeDesk.poll_events``
drains on the main thread (see the review note: "move background tasks into dedicated
workers").

Why two layers
--------------
* ``WorkerPool`` is the reusable policy: run a callable on a daemon thread and hand its
  result to a callback. New background features should use it instead of spinning up a raw
  ``threading.Thread`` at a call site.
* ``BackgroundTasks`` binds that policy to the app's specific background jobs (cloud
  entitlement sync, the app-update badge check, and the telemetry retry/back-off merge).

Note on the core job pump: ``CafeDesk.job`` / ``CafeDesk.poll_events`` remain on ``CafeDesk``
because the test suite pins their exact bodies. They share the same event-queue contract this
module is built around.
"""

import threading

from logging_utils import logger


class WorkerPool:
    """Owns the off-main-thread execution policy for Café Desk background work."""

    def run(self, fn, *, on_result=None, daemon=True):
        """Run ``fn`` on a worker thread; deliver its result via ``on_result``.

        Any exception from ``fn`` is caught and handed to ``on_result`` as
        ``{'ok': False, 'error': str(exc)}`` so callers never have toGuard their workers.
        """

        def _target():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - surfaced to the callback, not swallowed silently
                logger.error("Background worker failed: %s", exc)
                result = {"ok": False, "error": str(exc)}
            if on_result is not None:
                on_result(result)

        threading.Thread(target=_target, daemon=daemon).start()


class BackgroundTasks:
    """The app's concrete background jobs, built on :class:`WorkerPool`."""

    def __init__(self, pool: WorkerPool | None = None):
        self._pool = pool or WorkerPool()

    def run_remote_entitlement_sync(self, cfg, on_result):
        """Fetch cloud licence entitlements off-thread and hand the result to ``on_result``."""
        # cloud_sync is imported lazily so this module has no import-time dependency on the
        # network/ Supabase stack; the call site already has a loaded ``cfg``.
        import cloud_sync

        self._pool.run(
            lambda: cloud_sync.sync_machine_entitlements_from_cloud(cfg),
            on_result=on_result,
        )

    def run_app_update_badge(self, check_fn, on_result):
        """Check for an available app update off-thread; call ``on_result(res)`` with the result."""
        self._pool.run(check_fn, on_result=on_result)

    def merge_failed_telemetry(self, snapshot: dict, counts: dict, lock: threading.Lock):
        """Re-merge a failed telemetry snapshot back into the live counters (capped at 5000).

        Kept here (not inline in ``_flush_telemetry_async``) so the back-off policy lives in
        one place and is independently testable.
        """
        with lock:
            for key, value in snapshot.items():
                counts[key] = min(5000, counts.get(key, 0) + value)
