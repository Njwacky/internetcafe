"""Durable retry records for completed saves / explicitly confirmed manual sales.

Never call submit() for drafts, previews, temporary files or cancelled/failed
exports. Entries contain service/accounting data only, never customer files,
contents or paths. The SQLite ledger remains the source of earnings totals.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

import sales_store
import settings_store


def _directory(path=None):
    return Path(path or sales_store.db_path()).parent / 'pending-sales'


def _target(reference, path=None):
    # Sale references are data, not filenames supplied by the user.
    name = hashlib.sha256(reference.encode('utf-8')).hexdigest() + '.json'
    return _directory(path) / name


def has_pending(reference, path=None):
    try:
        _read(_target(reference, path))
        return True
    except (OSError,ValueError,TypeError,KeyError):
        return False


def _pending_files(path=None):
    try:
        return sorted(p for p in _directory(path).iterdir() if p.name.endswith('.json'))
    except FileNotFoundError:
        return []


def pending_count(path=None):
    return len(_pending_files(path))


def _normalize(raw):
    reference = raw['sale_id']
    if not isinstance(reference, str) or not reference or len(reference) > 64:
        raise ValueError('Invalid pending sale reference.')
    business = settings_store.clean_name(raw['business'])
    currency = str(raw['currency']).strip()
    if not currency or len(currency) > 8 or any(c in currency for c in '\r\n|'):
        raise ValueError('Invalid pending sale currency.')
    items, _ = sales_store.validate_items(raw['items'])
    when = raw['created_at']
    datetime.fromisoformat(when)
    station = ' '.join(str(raw.get('station') or '').split())[:40]
    return dict(sale_id=reference, business=business, currency=currency,
                items=items, created_at=when, station=station)


def _read(target):
    if target.is_symlink() or target.stat().st_size > 512 * 1024:
        raise ValueError('Invalid pending sale file.')
    event = _normalize(json.loads(target.read_text(encoding='utf-8')))
    if _target(event['sale_id']).name != target.name:
        raise ValueError('Pending sale reference does not match its retry file.')
    return event


def _remember(event, path=None):
    target = _target(event['sale_id'], path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        old = _read(target)
        if any(old[key] != event[key] for key in ('sale_id', 'business', 'currency', 'items')):
            raise ValueError('Pending sale reference already has different details.')
        return old  # Preserve the actual save date/station on retries.
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(event, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        settings_store._harden(temp)
        os.replace(temp, target)
        # Best effort directory sync; Windows does not support opening directories.
        try:
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return event


def _record(event, path=None, *, cleanup=True):
    created = sales_store.record_sale(**event, path=path)
    try:
        if cleanup: _target(event['sale_id'], path).unlink(missing_ok=True)
    except OSError: pass  # Safe to replay: record_sale is idempotent, including voids.
    return created


def submit(reference, business, currency, items, created_at=None, station=None, *, path=None):
    """Persist a retry snapshot, then record it in SQLite without double counting.

    If the journal cannot be written, still try the ledger. If both fail the caller
    must keep its in-memory line and warn before discarding it. File export and
    accounting are separate resources, not one atomic filesystem transaction.
    """
    if station is None:
        station = settings_store.load_settings().get('station_name', '')
    event = _normalize(dict(sale_id=reference, business=business, currency=currency,
                            items=items, station=station,
                            created_at=created_at or datetime.now().astimezone().isoformat(timespec='seconds')))
    journal_error = None
    try:
        event = _remember(event, path)
    except OSError as exc:
        journal_error = exc
    try:
        return _record(event, path, cleanup=journal_error is None)
    except Exception as exc:
        if journal_error:
            raise ValueError('Sales recording failed: '+str(exc)+
                             '\nThe durable retry copy could not be saved: '+str(journal_error)+
                             '\nKeep this receipt open and retry before clearing it or closing the app.') from exc
        raise


def retry_pending(path=None):
    """Replay completed saved work only. Keep unreadable entries for staff review."""
    recorded = 0
    errors = []
    for target in _pending_files(path):
        try:
            recorded += int(bool(_record(_read(target), path)))
        except Exception as exc:
            errors.append(f'{target.name[:12]}: {exc}')
    return dict(recorded=recorded, remaining=pending_count(path), errors=errors)


def sale_states(references, path=None):
    """Look up current ledger status, including voids, without report date limits."""
    refs = list(dict.fromkeys(references))
    if not refs: return {}
    result = {}
    # Called during receipt drawing: fail quickly rather than freezing Tk for 10s.
    with closing(sales_store.connect(path,timeout=0.1)) as conn:
        for start in range(0, len(refs), 400):
            batch = refs[start:start+400]
            marks = ','.join('?' for _ in batch)
            result.update(conn.execute(f'SELECT sale_id,voided_at FROM sales WHERE sale_id IN ({marks})', batch))
    return result
