# Saved-work receipts and earnings: five-whys review

Date: 2026-11-21

## Outcome

The review reproduced accounting gaps in the first implementation, fixed them, and expanded the release checks. **217 Python tests and 4 JavaScript tests pass.** All 12 customer-export categories were exercised with real output writers, not just mocked success callbacks.

This is verified application logic, **not a claim of complete Windows/hardware certification**. Native Tk/Windows scanner interaction and live remote services still require checks on the shop computer. See the limits and acceptance checklist below.

## Five whys: why could saved earnings disappear after an error?

1. **Why could a saved file be absent from earnings?** The customer-file export could succeed while the separate SQLite sales write failed.
2. **Why would retry not recover it later?** The first implementation retained the failed accounting line only in the live receipt's memory.
3. **Why could that line disappear?** Clearing the customer, resetting a receipt or closing the app destroys that memory. A timed-out worker's late UI callback is deliberately ignored to avoid leaking an old customer's data into the next customer's receipt.
4. **Why was recovery dependent on the UI?** There was no durable accounting retry record independent of the current customer session.
5. **Why did the initial checks miss this?** Tests covered retrying within the same live session, not persisted recovery and late-worker handling. Also, the release runner loaded `test_services` but did not discover the tests under `tests/`.

**Root-cause fixes:**

- Added a protected local `pending-sales` outbox containing only completed-sale accounting metadata: immutable reference, business, currency, items, save timestamp and station.
- The actual writer must succeed before a completed sale can enter this queue or the ledger. Failed/cancelled saves, drafts, previews, temporary merge copies and recovery copies never enter it.
- Retry snapshots are atomically replaced and flushed to disk before attempting the ledger. They are removed only after an idempotent ledger write succeeds.
- Saved-sale retries survive receipt/customer clearing and application restarts. Startup and Sales reports expose pending work; **Retry pending saved sales** replays it.
- When neither the ledger nor a valid retry copy can be written, clearing the receipt is blocked. Closing requires an explicit data-loss warning.
- The release runner now includes both the existing root suite and the `tests/` suites, with a regression test for discovery itself.

## Five whys: why could a voided service still be billed?

1. **Why did the receipt still show the charge?** Its in-memory line was rendered even after the ledger row was voided.
2. **Why did reports and receipts disagree?** Reports queried the ledger's current void status; receipts used a previous snapshot.
3. **Why was that snapshot not updated?** Receipt refresh/export did not reconcile its sales references with the ledger.
4. **Why could the renderer include invalid lines?** It treated every receipt item as active.
5. **Why did tests miss it?** They tested report void totals and receipt copies separately, not voiding followed by receipt export.

**Fix:** reconcile references when receipts refresh and before export. Voided lines are visibly excluded from the active amount. A receipt whose lines are all voided cannot be exported as a new bill. Replaying an old pending reference does not reactivate a voided sale.

## Five whys: why could a retry move earnings to another day?

1. **Why could a saved manual receipt appear on tomorrow's report?** Its failed accounting write could be retried after midnight.
2. **Why did the retry use tomorrow's date?** It defaulted to the time of the retry.
3. **Why was the original time unavailable?** New manual receipt lines did not capture the successful PDF-save timestamp.
4. **Why could station information drift too?** A retry could reload changed station settings instead of using the original save's station.
5. **Why did tests miss it?** The original retry checks ran immediately without a date or station change.

**Fix:** freeze save timestamp, station, price and reference for saved manual receipt lines and persist them in the outbox. A late retry belongs to the original local save date/month, not the retry date/month.

## Five whys: why was a locked database reported as corrupt?

1. **Why did a storage error suggest restoring/archiving the database?** Every SQLite `DatabaseError` was labelled damage.
2. **Why did that include a normal lock?** SQLite busy/locked errors also inherit from that exception type.
3. **Why was a healthy database quarantined?** The handler copied the database before checking its SQLite error code.
4. **Why is that unsafe advice?** Staff might replace a healthy database rather than release a lock or free disk space.
5. **Why did tests miss it?** Corrupt-file tests and mocked generic failures did not exercise real transaction contention.

**Fix:** distinguish busy/locked, full disk and read-only storage. Only SQLite corruption/not-a-database codes trigger corruption quarantine/advice. Regression tests use real exclusive SQLite locks, verify successful retry, and check that receipt-status lookup fails quickly rather than blocking the UI for the normal writer timeout.

## Verification performed

Command | Result
--- | ---
`.venv/bin/python .github/run_tests.py` | 217 tests passed; now includes the new regression suites
`node --test tests/owner-resign.test.mjs` | 4 tests passed
Python compile checks for changed application modules | Passed
`python tools/check_version_sync.py --check` | Passed
`python tools/sync_owner_digest.py --check` | Passed
`git diff --check` | Passed

### Functional coverage

- All 12 export categories: scan PDF, images-to-PDF, PDF merge, page extraction, split, password removal, career document, OCR text, picture sheets, cards, forms and editable projects.
- Generated a combined receipt PDF from those real exports and verified its total without increasing the ledger a second time.
- Cancelled and failed output saves do not count or create retry entries.
- Discarding unsaved manual lines does not count.
- Reprinting receipts and partial-failure retries do not duplicate sales.
- Pending entries replay after clearing the customer/session; original date, station and amounts remain intact.
- Corrupt/unreadable retry records remain available and produce errors rather than being silently counted or deleted.
- A fresh Python process replays a pending entry from disk without the original session in memory. A real corrupt database retains the completed sale for recovery after the broken database is archived/restored.
- Real worker threads and the actual event pump: normal completion updates the receipt; a watchdog-expired old save cannot populate the next customer's receipt, and its failed accounting remains recoverable from disk.
- Today/This month callbacks at December, January and leap-day boundaries.
- Currency-separated gross totals and excluded voids.
- Replayed records merge between two local PC databases without duplicates; void status propagates.
- No customer document contents or file paths enter the sales/outbox records.
- Database-only backup now warns if pending retry records are not included.

## Remaining limits — do not mistake these for verified guarantees

- **Native UI/hardware:** this sandbox has no Tk runtime/display or Windows WIA scanner. Controllers were executed with headless widget substitutes; real PDF/image/text/project writers and SQLite operations were exercised. Actual desktop dialogs, scanner drivers, printer interaction and installer behaviour still need the shop-PC check below.
- **Paid add-on:** the export matrix supplies a mocked approved entitlement while exercising the real PDF writer. Genuine machine activation was not performed.
- **Remote services:** no live cloud database was changed. Local cross-PC merge behaviour was tested; live cloud authentication/network behaviour was not exercised in this review.
- **Power loss:** customer-file export and accounting are separate resources, not a single transaction. A hard crash between committing an output file and persisting its retry snapshot still requires staff reconciliation. No guarantee against arbitrary hardware failure or deliberate deletion of local accounting files is made.
- **Storage:** if both the ledger and retry storage fail, the in-memory receipt must remain open until retried. Forced termination or overriding the close warning can lose it.
- **Pricing:** automatic rates are explicitly linked by staff and charged **once per successful export/job**, not inferred per page. Exporting again is a new job. Configure suitable rates, including an explicit zero-price rate for free work.
- **Meaning of earnings:** totals are recorded gross service amounts, not verified payments, profit, bank savings or tax calculations. Unresolved pending entries mean totals may be incomplete.
- **Backups:** a SQLite-only backup excludes pending retry files. Resolve them first or back up the full protected local Café Desk folder as well.

## Shop-PC acceptance checklist

1. Configure prices and automatic links, including a zero-price service.
2. Scan two pages, delete one before saving, and confirm earnings have not changed. Save the remaining scan PDF and verify its linked job rate appears in Receipts and Today.
3. Add another saved service and one manual line. Save the combined receipt twice; only the new manual line should increase earnings once.
4. Void a saved service. Return to Receipts and verify its amount is excluded; check Today and This month.
5. In a disposable test profile, simulate a locked/unwritable ledger. Save a customer file, confirm the pending warning, restart, restore storage access and retry from Sales reports. Confirm exactly one sale on its original save date.
6. Choose Next customer and verify that customer content/receipt view clears while recorded totals and any durable pending sales remain.
7. Exercise the actual WIA scanner, native dialogs, print workflow and licensed PDF add-on on the intended Windows installation before distributing it.
