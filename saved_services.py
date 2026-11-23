"""Accounting boundary for customer exports (never drafts, previews or temp files).

Each completed export has an immutable sale reference so existing LAN/cloud merging
continues to be idempotent. A customer's on-screen receipt can contain several of
these records; printing it must never charge those lines a second time.
"""
from datetime import datetime
import sales_outbox
import price_list

# Quantities are per completed export/job, not inferred page counts. Staff should
# link job-priced services here, rather than per-page printing rates.
SERVICES = {
    'scan': 'Scanned PDF',
    'images': 'Images to PDF',
    'merge': 'PDF merge',
    'extract': 'PDF page extraction',
    'split': 'PDF split',
    'unlock': 'PDF password removal',
    'career': 'Career document',
    'ocr': 'OCR text',
    'pictures': 'A4 picture sheets',
    'cards': 'Cards and invitations',
    'forms': 'PDF form',
    'project': 'Editable project',
}


def linked_price(settings, service):
    """Only use an explicitly linked rate; never guess a price from a name."""
    if service not in SERVICES:
        raise ValueError('Unknown saved service.')
    currency, rows = price_list.read_prices(settings)
    links = settings.get('receipt_service_links', {})
    name = links.get(service) if isinstance(links, dict) else None
    return currency, next((dict(row, qty=1) for row in rows if row['name'] == name), None)


def save_and_record(write, sale_id, business, currency, item, *, path=None, station=None):
    """Run the actual writer first. A failed export cannot reach the sales ledger.

    Return accounting errors separately: the file really was saved and must not be
    reported as a failed export or silently omitted from the customer's receipt.
    The UI retains an unrecorded line for an idempotent retry in that case.
    """
    result = write()
    when = datetime.now().astimezone().isoformat(timespec='seconds')
    error = None
    try:
        sales_outbox.submit(sale_id, business, currency, [item], when,
                            path=path, station=station)
    except Exception as exc:
        error = str(exc)
    return result, when, error


def record_pending(items, business, currency, manual_reference, *, path=None):
    """Record only unsaved, included lines. Retries and receipt copies cannot double count."""
    groups = {}
    when = datetime.now().astimezone().isoformat(timespec='seconds')
    for item in items:
        if not item.get('_recorded') and item.get('_included', True):
            reference = item.get('_reference') or manual_reference
            item['_reference'] = reference
            item.setdefault('_created_at', when)
            groups.setdefault(reference, []).append(item)
    for reference, group in groups.items():
        lines = [{key: item[key] for key in ('name', 'qty', 'price')} for item in group]
        try:
            sales_outbox.submit(reference, business, currency, lines,
                                created_at=group[0].get('_created_at'), path=path,
                                station=group[0].get('_station'))
        except Exception:
            if sales_outbox.has_pending(reference, path):
                for item in group: item['_saved_work'] = True
            raise
        # Preserve partial successes for the UI if a later group fails. References
        # are frozen before writing so later additions cannot alter a retry payload.
        for item in group:
            item['_recorded'] = True
    return len(groups)


def reconcile_receipt(items, *, path=None):
    """Reflect report voids and successful outbox retries on an open receipt."""
    states = sales_outbox.sale_states([x['_reference'] for x in items if x.get('_reference')], path)
    for item in items:
        reference = item.get('_reference')
        if reference:
            item['_recorded'] = reference in states
            item['_voided'] = bool(states.get(reference))


def unprotected_sales(items, *, path=None):
    """Saved work with neither a confirmed ledger entry nor a durable retry copy."""
    return [x for x in items if x.get('_saved_work') and not x.get('_recorded')
            and (not x.get('_reference') or not sales_outbox.has_pending(x['_reference'], path))]


def billable_lines(items):
    """Lines that count toward this receipt's total: not voided and not excluded."""
    return [x for x in items if not x.get('_voided') and x.get('_included', True)]


def exclude_line(item, *, reason='Excluded from receipt', path=None):
    """Toggle a receipt line OFF so it is not billed to this customer.

    A recorded sale cannot be silently forgotten: excluding it voids the ledger
    entry (preserving the audit trail) so the earnings do not linger in history.
    Unrecorded manual lines simply stop being queued. Returns True if the line
    is now excluded.
    """
    if item.get('_excluded'):
        return True
    item['_excluded'] = True
    item['_included'] = False
    reference = item.get('_reference')
    if item.get('_recorded') and reference:
        import sales_store
        try:
            sales_store.void_sale(reference, reason=reason, path=path)
            item['_voided'] = True
        except Exception:
            # The line is excluded from the bill regardless; voiding is best-effort
            # so a locked DB cannot trap the staff member. reconcile_receipt will
            # reflect the true ledger state on the next pass.
            pass
    return True


def include_line(item, *, path=None):
    """Toggle a receipt line back ON so it is billed again.

    A previously voided sale cannot be un-voided; this re-queues the line as an
    unrecorded billable entry under a fresh reference so the sale is recorded
    once the receipt is saved, without double counting the voided ledger entry.
    """
    if not item.get('_excluded'):
        return True
    item['_excluded'] = False
    item['_included'] = True
    if item.get('_voided') and item.get('_reference'):
        # The old ledger entry was voided; keep that audit line and record fresh
        # under a new reference so saving the receipt records it exactly once.
        # Clear _voided too: it was tied to the discarded reference, and leaving it
        # would make the line look not-billable even though the checkbox is ON.
        item['_recorded'] = False
        item['_reference'] = None
        item['_voided'] = False
        item.pop('_created_at', None)
    return True


def receipt_text(business, currency, items, *, timestamp=None, reference=None):
    """Bill only active, included lines; keep voided and excluded references visibly listed."""
    import services
    active = [x for x in items if not x.get('_voided') and x.get('_included', True)]
    if active:
        text = services.receipt_text(business, currency, active, timestamp=timestamp, reference=reference)
    else:
        text = f'{business}\nRECEIPT\n{timestamp or ""}\nReference: {reference or ""}\n\nNo active services.\nTOTAL: {currency}0.00'
    refs = list(dict.fromkeys(x['_reference'] for x in active if x.get('_reference')))
    if refs: text += '\n\nIncluded sales references:\n' + '\n'.join(refs)
    excluded = list(dict.fromkeys(x['_reference'] for x in items if x.get('_excluded') and not x.get('_voided') and x.get('_reference')))
    if excluded: text += '\n\nExcluded (not billed) sales references:\n' + '\n'.join(excluded)
    voids = list(dict.fromkeys(x['_reference'] for x in items if x.get('_voided') and x.get('_reference')))
    if voids: text += '\n\nVOIDED — excluded from total:\n' + '\n'.join(voids)
    return text
