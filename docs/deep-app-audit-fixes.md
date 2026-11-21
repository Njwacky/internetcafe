# Deep-audit fixes — 0.8.4

This is the implementation follow-up to [the baseline audit](deep-app-audit.md), not a claim that every audit recommendation has been completed.

> [Five-whys recheck](deep-app-five-whys.md): four further gaps were reproduced and corrected after the initial PR patch. The validation totals below include those fixes.

## Included fixes

| Audit finding | Change |
|---|---|
| 1 — recovery resurrection | `RecoveryStore` serializes projects in memory, then checks a generation under the same lock used by clear. Rotation/publication and deletion cannot race. Disable, timeout and close invalidate pending snapshots. Unrelated widget errors no longer release an active worker. Clear also removes abandoned atomic-write recovery staging archives. |
| 2 — station collision | Sync filenames use a persistent ledger UUID bound to the machine identity, not an editable/sanitized station name. Renaming a station does not change its filename; a backup copied to another machine gets a different identity. |
| 3 — history/old void omissions | LAN snapshots and SQLite imports no longer cap history. Cloud protocol 2 uploads batches of 250 and downloads full history with a sale-ID keyset cursor, including old voids. Each run scans again rather than retaining an unsafe creation-date watermark. |
| 4 — silent failed peers | LAN returns per-file errors and explicit partial status. Healthy files still merge. Rejected rows and conflicts are displayed in LAN/cloud/manual import results. |
| 5 — mixed currencies | Owner totals, averages and shop rows are grouped by currency. Ranking bars compare within each currency, never across currencies. Custom currency text is HTML-escaped. |
| 6 — unsafe template HTML | Template rows/errors use DOM nodes and `textContent`; buttons use event listeners with captured IDs. Template values are never interpolated into executable HTML/inline handlers. |
| 9 — import validation | Reject fractional/boolean quantities, invalid void dates, mismatched declared totals and immutable-ID conflicts. Valid rows commit with explicit rejection details; local database failures remain fatal. An incoming void cannot attach to a different transaction merely by reusing its ID. |
| 12 — misleading analytics | Pagination also handles a server row cap smaller than the requested page size. No lifetime-as-period online fallback; upper date bounds exclude future rows. Bounded dashboard pagination checks for overflow and reports an incomplete refresh instead of using a truncated total. Failed feeds retain last-good data with a visible warning; initially unavailable sales are labeled unavailable. License records also use checked pagination. |

Existing successful-save receipt/earnings automation, void-aware totals, original retry dates/stations/prices and durable accounting retries are preserved. Recovery copies are still not revenue.

## Validation

- **236 Python tests pass** using the locked Python dependencies.
- **26 JavaScript tests pass**, including **12 real PostgreSQL/PGlite integration tests** for the migration and **10 dashboard regressions**, plus the existing 4 signer tests.
- Version synchronization (`0.8.4`), owner-digest synchronization and `git diff --check` pass.
- Python regressions execute actual recovery/watchdog/controller methods with fake UI objects and actual project serialization. They exercise clear during publication, old-generation completion, rotation and crash staging-file cleanup.
- Actual SQLite fixtures cover 5,002-row LAN snapshots/old voids and a 50,002-row backup import, same-name PCs, station rename, copied-backup machine binding, partial/corrupt peers and conflicting imports.
- Cloud client tests cover more than the former upload/download windows, bounded requests, acknowledgement failures, missing migration and invalid cursor rejection.
- SQL integration tests execute the checked-in migration against isolated PostgreSQL, testing pagination, shop-secret isolation, public-role table access denial, void monotonicity, validation, immutable conflicts and transaction rollback. These are not tests of deployed Supabase settings.
- No live database, customer data or Windows release was changed or published during this work.

Run locally:

```sh
python -m pip install -r requirements-lock.txt
python .github/run_tests.py
npm ci --ignore-scripts
npm test
python tools/check_version_sync.py --check
python tools/sync_owner_digest.py --check
```

JavaScript dependencies are development/test-only. PGlite is not bundled in the desktop app or website. CI installs them from the lockfile and runs every `tests/*.test.mjs` file.

## Deployment — coordinated cloud protocol upgrade required

**Do not treat merging this PR as applying the database migration.**

1. Back up each PC's ledger and the cloud sales table. Retry pending accounting sales before relying on database-only backups; the outbox is a separate local resource.
2. Test the migration and the new client against a non-production project first. Include representative old sales, a recent void of an old sale, and any known legacy conflicts.
3. Arrange a maintenance window to upgrade **every shop PC** to 0.8.4 and apply `supabase/migrations/20261121150000_complete_sales_sync.sql` using the normal owner-controlled migration process. It is also appended to `supabase_schema.sql` for fresh setups.
4. The migration installs `sync_shop_sales_v2`, retaining the existing shop passcode verification and owner-table access controls. It does not delete sales or change original sale values. It widens `total_cents` to `bigint`; stage and plan for the table lock/storage rewrite on large databases. The old RPC returns an explicit upgrade-required error instead of silently truncating history. **Older clients cannot use cloud sales sync after this migration.** Local saving and reporting are unaffected.
5. A new client against an unmigrated project fails visibly; it does not silently fall back to truncated protocol 1. No passcode rotation is required for this protocol change.
6. Synchronize each PC, then repeat the LAN round if using shared files so snapshots include newly learned peer records. Compare sale-ID sets, voids, and daily/monthly per-currency totals. Investigate every rejected row or partial-sync warning before trusting aggregated revenue.
7. Keep legacy station-named LAN snapshots until reconciliation is complete. The new code reads but does not automatically erase them, and cannot reconstruct records that an old colliding snapshot already overwrote unless another ledger/backup still contains them.
8. Publish the uniquely versioned 0.8.4 Windows artifact only after native smoke tests and the existing release-key/signature checks. The version is bumped so clients on 0.8.3 can recognize an actual update.

If cloud rollout must pause, keep local sales/outbox files intact and use verified backups. Do not restore protocol 1 just to make a sync success dialog return; that reinstates incomplete history. Versioned additive RPC deployment permits correcting v2 and retrying idempotently.

## Deliberate trade-offs and remaining work

- Full-history reconciliation is correctness-first and O(history) per run. It uses bounded cloud requests but currently retains complete records in client memory. Very large shops need a measured, acknowledged change-log/cursor protocol with tombstones and a full-history bootstrap—not a reintroduced `created_at` cap. Concurrent changes converge on subsequent runs; this is not a transactionally consistent fleet-wide snapshot.
- The recovery publication lock coordinates workers within an app process. It is not an OS-wide lock, encrypted storage or a secure-erasure guarantee. Do not run multiple app instances sharing one customer recovery directory; add an OS-level single-instance/locking policy as separate hardening.
- LAN JSON remains a trusted-shop-folder format, not an authenticated/tamper-proof financial transport. Do not give customers write access to the share.
- Dashboard overflow is now explicit rather than silently wrong. It still needs server-side aggregation for high-volume deployments; last-good data can be stale and is labeled accordingly.
- Native Windows/Tk interactions, printer/scanner hardware, installed artifacts, live cloud policies and actual owner Auth/MFA configuration still need validation.
- **Not implemented here:** server-enforced owner MFA/step-up; revocation-state redesign; downloadable-template application in editors; removal of destructive entitlement-read repair logic; device-authenticated/minimized telemetry; asynchronous form/card previews; a cross-resource output/accounting reconciliation journal; full release immutability, Authenticode signing and verified backup/restore automation.
- Removing the template-specific unsafe sinks does not eliminate the dashboard's existing inline scripts/handlers or tighten its entire CSP. That remains a separate compatibility/security change.

These remaining items are explicitly retained in the baseline audit and must not be described as resolved by this patch.
