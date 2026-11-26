"""Recovery publication barrier: clearing invalidates in-flight customer snapshots.

Compression and image reads happen in memory outside the lock. Only short file
publication/rotation shares the clear lock; a late worker cannot recreate a file
(or a plaintext staging archive) after clear returns.

Cross-process hardening (five-whys recheck, section 1)
------------------------------------------------------
The 0.8.4 barrier used a process-local ``threading.Lock`` and an in-memory
generation counter. That is not enough when more than one Café Desk instance can
run on the same computer against the same protected config folder (a portable
USB copy started twice, a second copy of the folder): each process has its own
lock and its own counter, so a late worker in the *other* process never sees the
bump and publishes the previous customer's snapshot after a clear.

This module therefore makes the barrier OS-level:

* the authoritative generation lives in a small marker file next to the
  recovery folder, so every process sees the same value;
* every mutation (save / clear / invalidate) runs inside a cross-process
  exclusive lock - ``fcntl.flock`` on POSIX, a Windows byte-range lock via
  ``msvcrt`` on Windows - so publication and deletion cannot interleave across
  processes;
* a worker that captured an old generation is rejected under the same lock, no
  matter which process it belongs to.

The lock wait is bounded. If it cannot be acquired, ``save`` fails cleanly (the
next recovery tick retries) and ``clear`` reports failure instead of deleting
files while another instance may be publishing - a clear that cannot be
serialised must not run, otherwise the late publish would resurrect the file.
``invalidate`` is best-effort (short timeout, in-memory fallback) because it is
called on UI paths such as close/shutdown where blocking is undesirable; the
strict guarantee is carried by ``clear``.

The marker and lock files live in the parent of the recovery directory (the
protected Café Desk config folder) so the recovery folder itself contains only
customer recovery data.
"""
import errno
import os
import threading
import time
from pathlib import Path

import projects
import services

# Bounded waits for the cross-process lock. Publication is a short file write,
# so these are generous; they exist to bound a wedged peer, not normal operation.
_LOCK_TIMEOUT = 15.0
_INVALIDATE_LOCK_TIMEOUT = 3.0
_POLL_INTERVAL = 0.05

GENERATION_MARKER = ".cafedesk-recovery.generation"
LOCK_FILE = ".cafedesk-recovery.lock"


class _FileLock:
    """Cross-process exclusive lock on a file (flock on POSIX, byte-range on Windows).

    The lock file is created if missing. On Windows a single byte is written so
    the byte-range lock has a valid range; note that Windows byte-range locks are
    enforced per process, while POSIX ``flock`` locks are enforced per open file
    description (two independent opens in one process conflict on POSIX).
    """

    def __init__(self, path, timeout=_LOCK_TIMEOUT):
        self.path = Path(path)
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            # Ensure the file has one byte so Windows byte-range locks are valid.
            try:
                os.write(fd, b"x")
            except OSError:
                pass
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return self
                except OSError as exc:
                    # A filesystem that cannot enforce advisory locks (some NFS/SMB/FUSE
                    # mounts) fails every attempt with ENOLCK/EOPNOTSUPP/ENOSYS. That is
                    # a permanent condition, not a busy lock: retrying cannot help and
                    # stalling for the full timeout hides the real problem. Fail fast
                    # with a clear message instead (fail closed - the barrier must not
                    # silently degrade).
                    if os.name != "nt" and getattr(exc, "errno", None) in (
                        errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOSYS
                    ):
                        os.close(fd)
                        raise OSError(
                            f"The filesystem for {self.path} does not support "
                            "cross-process locking; recovery isolation cannot be "
                            "guaranteed on this disk."
                        ) from exc
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise OSError(
                            f"Could not acquire the recovery lock ({self.path.name}); "
                            "another Café Desk instance may be publishing."
                        ) from None
                    time.sleep(_POLL_INTERVAL)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def __exit__(self, *exc):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


class RecoveryStore:
    def __init__(self, directory, lock_timeout=_LOCK_TIMEOUT):
        self.directory = Path(directory)
        # Shared state lives beside the recovery folder (protected config folder),
        # so the recovery folder holds only customer recovery data.
        self._state_dir = self.directory.parent
        self._marker = self._state_dir / GENERATION_MARKER
        self._lockfile = self._state_dir / LOCK_FILE
        self._lock = threading.Lock()
        self._generation = 0  # in-process mirror; the marker file is authoritative
        self._lock_timeout = lock_timeout

    # -- generation state -----------------------------------------------------

    def _read_generation_locked(self) -> int:
        """Current generation. The marker file is authoritative across processes."""
        try:
            return int(self._marker.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return self._generation

    def _write_generation_locked(self, value: int) -> None:
        self._generation = value
        try:
            services._atomic_write(
                self._marker,
                lambda tmp: Path(tmp).write_text(str(value), encoding="ascii"),
                allow_internal=True,
            )
        except OSError:
            # Best effort: the in-memory mirror still guards this process. A
            # missing/corrupt marker is rewritten by the next invalidate/clear.
            pass

    def generation(self) -> int:
        with self._lock:
            return self._read_generation_locked()

    def invalidate(self):
        """Abandon pending snapshots without deleting the last completed checkpoint.

        Best effort: a short bounded wait, and an in-memory fallback if the
        cross-process lock cannot be taken (close/shutdown paths must not block).
        ``clear`` carries the strict cross-process guarantee.
        """
        with self._lock:
            try:
                with _FileLock(self._lockfile, timeout=_INVALIDATE_LOCK_TIMEOUT):
                    self._write_generation_locked(self._read_generation_locked() + 1)
            except OSError:
                self._generation += 1

    # -- publication / deletion ----------------------------------------------

    def clear(self):
        """Delete recovery copies and bump the generation under the cross-process lock.

        Raises OSError if the lock cannot be acquired: deleting without the lock
        could let another instance's in-flight worker publish right after the
        deletion, resurrecting the previous customer's file.
        """
        with self._lock:
            with _FileLock(self._lockfile, timeout=self._lock_timeout):
                self._write_generation_locked(self._read_generation_locked() + 1)
                failed = []
                targets = [self.directory / (kind + ext)
                           for kind in ('career', 'sheet')
                           for ext in ('.cafeproject', '.prev.cafeproject')]
                # A process crash may leave an atomic-write staging file behind.
                targets.extend(self.directory.glob('.cafedesk-*.cafeproject'))
                for target in targets:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        failed.append(target.name)
                return failed

    def save(self, generation, jobs):
        # Serialise everything in memory first; only the short publication step
        # needs the cross-process lock.
        archives = [(kind, projects.project_bytes(kind, snapshot, allow_internal=True))
                    for kind, snapshot in jobs]
        with self._lock:
            with _FileLock(self._lockfile, timeout=self._lock_timeout):
                # Under the lock the generation cannot change in any process:
                # invalidate/clear also need this lock. The in-process mirror is
                # consulted as well: when invalidate() had to fall back to the
                # in-memory bump (cross-process lock unavailable), the marker is
                # stale but the mirror is ahead - a save captured before that
                # fallback must still be rejected.
                if generation != self._read_generation_locked() or generation < self._generation:
                    return False
                # Make the authoritative generation visible to other processes
                # even if the marker was deleted externally.
                if not self._marker.exists():
                    self._write_generation_locked(generation)
                for kind, archive in archives:
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    current = self.directory / (kind + '.cafeproject')
                    previous = self.directory / (kind + '.prev.cafeproject')
                    if current.exists():
                        old = current.read_bytes()
                        services._atomic_write(previous, lambda tmp: Path(tmp).write_bytes(old), allow_internal=True)
                    services._atomic_write(current, lambda tmp: Path(tmp).write_bytes(archive), allow_internal=True)
        return True
