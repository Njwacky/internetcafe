"""Recovery publication barrier: clearing invalidates in-flight customer snapshots.

Compression and image reads happen in memory outside the lock. Only short file
publication/rotation shares the clear lock; a late worker cannot recreate a file
(or a plaintext staging archive) after clear returns.
"""
import threading
from pathlib import Path

import projects
import services


class RecoveryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._generation = 0

    def generation(self):
        with self._lock:
            return self._generation

    def invalidate(self):
        """Abandon pending snapshots without deleting the last completed checkpoint."""
        with self._lock:
            self._generation += 1

    def clear(self):
        failed = []
        with self._lock:
            self._generation += 1
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
        for kind, snapshot in jobs:
            # Never give an abandoned worker a path at which it could write later.
            archive = projects.project_bytes(kind, snapshot, allow_internal=True)
            with self._lock:
                if generation != self._generation:
                    return False
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                current = self.directory / (kind + '.cafeproject')
                previous = self.directory / (kind + '.prev.cafeproject')
                if current.exists():
                    old = current.read_bytes()
                    services._atomic_write(previous, lambda tmp: Path(tmp).write_bytes(old), allow_internal=True)
                services._atomic_write(current, lambda tmp: Path(tmp).write_bytes(archive), allow_internal=True)
        return True
