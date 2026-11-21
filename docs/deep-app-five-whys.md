# PR #46 — five-whys recheck

**Date:** 21 November 2026  
**Starting commit:** `0e9f8a9`  
**Method:** trace each intended safety property through writers, readers, storage types, lifecycle paths and retries; add adversarial regressions before changing code; execute isolated SQLite/PostgreSQL tests and the full suites. No live cloud data or settings were touched.

## Result

The original patch was not complete enough to call fully verified. The recheck reproduced **four additional gaps**, fixed below. The original root-cause fixes remain in place; the broader deferred audit work remains deferred.

Initial failing regression evidence:

- Dashboard with a 100-row server cap and 350 matching records returned **100**, not 350.
- A retry/void with equivalent item values raised **“Sale reference conflicts with immutable recorded details.”**
- A locally valid `999 × 10,000,000.00` sale failed the cloud ledger limit instead of syncing **999,000,000,000 cents**.
- An unrelated UI exception set `busy=False` while `_active_job_id` still identified a running worker.

## 1. Why could dashboard totals still be incomplete?

1. **Why were 250 matching records absent?** The fetch stopped after receiving 100 of the 350 records.
2. **Why stop at 100?** The first response was shorter than the requested page size of 1,000.
3. **Why was a short response treated as completion?** The client assumed it controlled the effective page size.
4. **Why did the overflow check not catch it?** It probed at `maxPages × requestedSize`, skipping the unread middle records.
5. **Why did tests pass?** The fake server honored the requested range exactly; it never imposed a smaller server limit.

**Root cause:** requested page size was mistaken for the server's delivery contract.

**Fix:** advance the offset by the number of rows actually delivered; continue until an empty response; probe for overflow at the actual next offset. Invalid/non-array responses fail explicitly. A safety page limit still exists, but cannot silently turn an incomplete dataset into a total.

**Regression:** 100-row server limit / 350 rows returns 350 distinct rows; an insufficient two-page budget reports partial data instead of returning 200 as complete. Exact-full pages, normal paging, errors and script parsing remain covered.

## 2. Why could a valid retry or void look like an immutable conflict?

1. **Why was the same sale rejected?** Its item JSON was not byte/representation-equivalent to the stored JSON.
2. **Why did the representation change?** Import normalization converts string quantities and numeric prices to integer quantities and two-decimal price strings.
3. **Why was cloud behavior different?** The server validated these representations but stored the unnormalized input.
4. **Why did immutability then block a void?** The comparison treated formatting as financial identity.
5. **Why was this missed?** Prior SQL fixtures already used the desktop's canonical JSON shape, so no normalize → retry cycle changed them.

**Root cause:** client and server lacked a shared semantic normalization contract at the immutable-comparison boundary.

**Fix:** a private SQL normalization helper validates/canonicalizes service names, integer quantities and decimal prices before storage and comparison. Existing legacy item JSON is normalized for comparison without rewriting the original content. True changes to service/quantity/price still conflict. Local service names must be strings. Signed zero is normalized to `0.00` in both paths, including comparisons with old local ledger entries.

**Regressions:** upload `qty:"1", price:1`, then retry/void as `qty:1, price:"1.00"`; surrounding service-name whitespace; equivalent pre-existing legacy cloud JSON; free-service `-0.00` vs `0.00`; retry against old local signed-zero JSON; negative prices remain rejected. Immutable monetary/content changes are still blocked and upload batches still roll back on a conflict.

## 3. Why could a locally valid sale fail cloud synchronization?

1. **Why did the RPC reject the sale?** Its cents value exceeded the cloud's 32-bit integer range.
2. **Why could the local ledger hold it?** SQLite integer storage supports a wider range.
3. **Why did validation permit it?** The allowed unit-price and quantity bounds legitimately produce much larger totals.
4. **Why did the protocol-2 guard reject it?** It preserved the old storage ceiling rather than matching the application's existing valid range.
5. **Why did tests miss it?** Examples covered row-count boundaries but not monetary storage boundaries.

**Root cause:** the financial validation contract and database storage type were not reviewed together.

**Fix:** widen cloud `total_cents` to `bigint`, use bigint casts and retain an explicit bound matching the maximum 500 lines × 999 units × 10,000,000.00. No floating-point conversion is used for SQL validation; totals still have to equal the recomputed line amounts.

**Regression:** the real SQL RPC stores and returns 999,000,000,000 cents. Wrong totals, negative values, invalid prices, invalid quantities and oversized batches remain rejected.

**Deployment consequence:** this migration now also performs a schema type change. Back up and stage it; on a large table, plan for the lock/rewrite and maintenance time. Do not downcast a ledger containing larger values.

## 4. Why could recovery workers overlap despite the busy guard?

1. **Why could another job start?** A callback exception cleared `busy`.
2. **Why did it clear it?** The generic exception handler assumed clearing busy was always safe recovery from a UI error.
3. **Why was that unsafe?** The background worker and its active job token were still alive.
4. **Why could that threaten recovery ordering?** Another recovery job could capture the same valid generation while the first was unfinished.
5. **Why did the initial tests miss this?** They covered normal completion, watchdog timeout and explicit clear, not an unrelated widget exception during a worker.

**Root cause:** UI callback health and background-job ownership were coupled through a writable Boolean rather than the active job's lifecycle.

**Fix:** widget-lifecycle noise does not change worker ownership. A generic UI failure may recover an orphan busy flag only when there is no active job ID. A tracked job is released by its result or existing watchdog, which also handles recovery generation invalidation when abandoning it.

**Regression:** both generic and Tk lifecycle exceptions retain an active job's busy flag/token; an orphan busy flag can still be recovered. Existing event-pump exception/rearming and watchdog tests continue to pass.

## Recheck of the original changes

| Safety property | Verification / remaining boundary |
|---|---|
| Clear beats an old recovery worker | Real controller/watchdog methods plus blocked serialization and blocked publication; old generations cannot publish over a new customer. Process-local coordination only, not multi-instance locking/encryption/secure erasure. |
| Station names are not identities | Same-name PCs, rename, separate ledgers and copied-backup machine binding tested. Legacy overwritten snapshots need another ledger/backup; code cannot recreate lost source history. |
| Old voids are not aged out | 5,002-row LAN and 50,002-row backup fixtures; client requests over old cloud caps; actual SQL keyset paging and monotonic voids. Concurrent edits converge on subsequent runs, not a fleet-wide atomic snapshot. |
| Failed peers/imports are visible | Healthy peer commits alongside corrupt/rejected peers, with explicit incomplete status and rejection details. Trusted LAN access is still required. |
| Currency totals are meaningful | Totals/averages/shop buckets remain currency-separated; custom currency text escaped; future and voided sales excluded. No implicit FX conversion. |
| Template strings stay data | Hostile metadata/errors go through text nodes; hostile IDs remain listener values. This is targeted sink removal, not removal of all dashboard inline code/CSP debt. |
| Shop data stays private | Wrong-secret calls disclose/mutate no sales; anon direct-table reads denied in isolated PostgreSQL; private helper execute rights revoked. Deployed RLS/Auth configuration is not certified by these tests. |
| Unsaved/discarded work is not revenue | Existing saved-work/outbox suite retained; project serialization itself does not invoke accounting. Successful saved exports retain automatic receipts and original retry dates/stations/rates. |

## Final validation

- **236 Python tests pass** with locked dependencies.
- **26 JavaScript tests pass:** 10 dashboard, 12 PostgreSQL/PGlite, 4 signer tests.
- Version (`0.8.4`) and owner-digest synchronization pass; Python compilation and whitespace checks pass.
- The original PR commit's hosted Linux tests, Windows packaging build and Vercel checks were green when inspected. New follow-up commits require their own CI run; a Windows package build is not a native GUI/hardware smoke test.

See [the implementation/deployment runbook](deep-app-audit-fixes.md) for the protocol migration and unresolved work. This recheck does not authorize or apply that migration, merge the PR, publish a release, or claim that every baseline audit recommendation is fixed.
