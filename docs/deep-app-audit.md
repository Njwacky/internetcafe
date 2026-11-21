# Café Desk — deep application audit

> This report describes the reviewed baseline. See [the 0.8.4 implementation follow-up](deep-app-audit-fixes.md) and [five-whys recheck](deep-app-five-whys.md) for fixes in this branch, validation and deployment requirements; not all recommendations are implemented.

**Date:** 21 November 2026  
**Reviewed baseline:** `ee51f1a7cf17113df29eb52812d20eb7b7a9867b` (includes saved-work accounting PR #45)  
**Scope:** desktop customer workflow, receipts/sales, multi-PC sync, recovery/privacy, owner dashboard, licensing, templates, cloud SQL and release pipeline.

## Executive assessment

The app has useful safeguards and a passing automated baseline, but should not yet be described as completely functional or safe for unattended multi-PC use. The most important next work is **customer-data isolation, trustworthy synchronization, and accurate owner reporting**, not additional tools or visual polish.

No production data was accessed or modified. Findings below distinguish disposable reproductions from source inspection and deployment-dependent concerns. This audit adds documentation, not application fixes.

### Validation performed

- **217 Python tests pass**, including a second run after installing `requirements-lock.txt` (19.032 seconds).
- **4 JavaScript signer tests pass**.
- Version consistency and browser/database owner-digest checks pass.
- Additional probes executed real sales functions against temporary databases and actual app methods extracted through Python AST with fake UI objects. Dashboard probes executed extracted JavaScript functions in Node's VM.
- Reproduced: recovery-file resurrection, station-name sync collision, old void omitted from LAN sync, silent corrupt-peer handling, fractional quantity truncation, malformed-row batch rollback, mixed-currency analytics, future sales included in current range, unsafe template markup insertion, and cancellation of the remote-revocation close path.
- A downloaded-template cache probe found the saved definition in the cache but not in the built-in CV selector.

**Limits:** native Tk/Windows GUI, actual printer/scanner/OCR hardware, real AI requests, installed Windows artifacts, and deployed Supabase policies/auth settings were not exercised. Passing tests are not end-to-end certification. No assertion below proves a public exploit of the live service.

## Priority 1 — fix before relying on privacy and multi-PC totals

### 1. Cleared customer recovery data can return after a worker times out

**Evidence:** reproduced; `app.py:545–603`, `app.py:1520–1540`.

The watchdog makes the desk available again and ignores late callbacks, but does not stop a background worker's disk writes. A recovery worker retains a snapshot of the previous customer's document. Clearing recovery can delete the file successfully, then the old worker writes the snapshot back.

Disposable reproduction:

```text
busy_after_watchdog: false
exists_just_after_clear: false
exists_after_old_worker: true
contains_previous_customer: true
```

**Impact:** a customer/staff member can be told recovery was cleared while another customer's data becomes recoverable again. Slow storage or a stuck write is enough to reach this race.

**Root cause:** callback invalidation is being treated as cancellation of side effects.

**Improve:** assign every customer session a generation ID; workers write to generation-specific staging files. Under one shared lock, verify the generation immediately before publishing a recovery file. Clear must invalidate the generation and delete its files under the same coordination. Audit other long-running operations for late writes too. Do not merely add another check before starting the write.

**Acceptance test:** pause a save before publication, trigger timeout, clear/end the session, start a new customer, release the old worker; neither primary nor previous recovery may reappear or overwrite new work.

### 2. Two PCs with the same station name collide in LAN sync

**Evidence:** reproduced; `sales_store.py:275–296`.

The sync filename comes from a sanitized, truncated, editable label rather than a stable installation identity. Two PCs named `Counter 1` write the same file and each excludes that filename as its own. Empty labels and labels that sanitize to the same string also collide.

```text
shared_files: [sales-Counter-1.cafesales.json]
peer_files_seen_by_second_pc: 0
second_pc_sale_ids: [from-two]
```

**Impact:** peer receipts are silently absent; the shared snapshot is overwritten. This is not deletion of the original local SQLite ledger, but it defeats aggregation.

**Improve:** persistent random installation UUID for filenames; keep station name as display metadata. Handle cloned portable installations explicitly and migrate legacy files safely.

**Acceptance test:** two databases with identical/empty/long labels exchange both sales, including after a station rename, without overwriting either peer's identity.

### 3. Fixed-size sync snapshots lose older transactions and old void changes

**Evidence:** old-void failure reproduced on LAN; cloud/file limits confirmed in source. `sales_store.py:271,283`; `sales_ui.py:151–178`; `supabase_schema.sql:524–529`.

Current windows are latest **1,500 uploaded cloud sales**, **2,000 returned cloud sales**, **5,000 LAN sales**, and **50,000 rows** when merging a SQLite backup. They are not an acknowledged, paginated change stream. An old sale voided today retains its old creation date, so it can stay outside every snapshot.

```text
snapshot_records: 5000
second_pc_old_sale_voided: false
```

**Impact:** a new PC may never obtain full history; an existing PC may retain revenue that was already voided elsewhere. Cloud truncation is source-confirmed, not tested against a live project.

**Improve:** append-only change sequence or reliable `(updated_at, ID)` cursor, pagination, peer acknowledgements and explicit void tombstones. Bootstrap full history independently from incremental changes. Never age out an unacknowledged mutation solely because its original sale is old.

**Acceptance test:** synchronize beyond every existing cap, void the oldest sale, add an offline PC, then reconcile every sale ID, per-currency daily/monthly total and void across all PCs.

### 4. Broken sync files can be reported as an ordinary successful sync

**Evidence:** reproduced; `sales_store.py:288–295`.

Every peer-file exception is swallowed. A corrupt peer produced:

```json
{"inserted":0,"updated_voids":0,"peer_files":0}
```

The caller cannot distinguish no peers from a peer it failed to read.

**Improve:** structured per-peer results, explicit partial-success/error status, last successful synchronization timestamp, retry guidance and safe diagnostics. Keep processing healthy peers, but never silently claim completeness.

**Acceptance test:** one healthy and one corrupt peer; healthy sales import, corrupt peer is named, totals carry an incomplete-sync warning until repaired.

### 5. Owner revenue combines incompatible currencies

**Evidence:** reproduced in actual `buildAnalytics()`; `owner-dashboard.html:1438–1449,1841–1855`.

Two sales, **R100 and USD100**, produced **R200,00** in range revenue. Per-shop aggregation also uses only shop ID, retaining the first currency while adding all amounts. Average-sale figures inherit the error. Desktop reports already group currencies correctly.

**Improve:** group monetary values by currency everywhere, including charts, shops and averages. If a consolidated currency is wanted, make exchange rates, conversion dates, rounding and original amounts explicit. Do not infer an exchange rate of 1:1.

**Acceptance test:** same and different shops, mixed currencies and a currency setting changed mid-month; no unlabeled cross-currency sum.

### 6. Owner-controlled template fields reach executable HTML sinks

**Evidence:** markup injection reproduced without executing a payload; `owner-dashboard.html:2585–2607`; inline-permitting CSP in `vercel.json`.

Template category/name/slug/version/ID are interpolated into `innerHTML` and inline handlers. A template name containing an image/event-handler string reached the output unchanged. Error messages also enter HTML.

**Risk boundary:** template writes are owner-restricted by the checked-in SQL policies. This is a dangerous stored-HTML/XSS sink, **not proof an anonymous visitor can write malicious templates**. Imported content, another privileged writer or compromised owner access would matter.

**Improve:** build DOM nodes, assign `textContent`, use event listeners and validate IDs/URLs. Remove inline handlers and tighten CSP after moving inline scripts. Add hostile-string rendering tests.

## Priority 2 — security, integrity and feature-completeness work

### 7. Owner privileges do not require MFA assurance in checked-in code

**Evidence:** source review; `supabase_schema.sql:573–585`; `supabase/functions/owner-resign/index.ts:13–19`; dashboard password/OTP login.

The database owner gate checks an email-derived digest; the privileged re-sign function validates the authenticated user's email. Neither requires AAL2. Live Auth settings were not inspected, so this is **lack of repository-enforced MFA**, not a claim about whether the owner has enrolled MFA.

**Improve:** stable owner user ID/role, MFA challenge flow and server-side AAL2 enforcement for issuing/re-signing/revoking licenses, releases, deletion and other sensitive actions. Add audit records and step-up confirmation. Enrolling MFA alone is insufficient if privileged endpoints still accept AAL1 tokens.

### 8. Remote revocation relies on a close operation that the user can cancel

**Evidence:** reproduced with actual methods and fake UI; `app.py:222` and `app.py:623–632`.

When an invalid required license is detected after remote revocation, the app calls ordinary `close_app()`. With unsaved work and a declined close confirmation, the window remained open and staff stayed unlocked in the probe.

**Boundary:** this proves the revocation callback does not enforce a lock. It does not prove every individually guarded feature remains usable.

**Improve:** transition to a restricted/license-required state independently of window closing. Allow safe saving of existing customer work under a clearly defined grace policy; block new paid operations. Do not destroy customer work just to enforce licensing.

**Acceptance test:** busy worker, unsaved draft, pending sale and declined close must not leave the unrestricted workspace available after revocation.

### 9. Sale imports need stricter validation and conflict handling

**Evidence:** reproductions plus source; `sales_store.py:76–86,208–258`.

- Quantity `1.9` was silently stored as `1` because of `int(...)` coercion.
- A malformed `items_json` raised `JSONDecodeError` and rolled back an otherwise valid batch. Atomic rollback can be a deliberate policy, but the current behavior is not a useful per-row validation report.
- For an existing sale ID, a remote void can be applied without checking the immutable sale details match. Void timestamps are not validated like creation timestamps.

**Improve:** strict integer quantities excluding booleans/fractions; schema validation before transaction; explicit all-or-nothing versus validated-row policy; immutable-content conflict detection; validated void events and reasons. Shared-folder access is a trust boundary: do not describe unauthenticated LAN JSON as tamper-proof accounting.

**Acceptance test:** malformed JSON, fractional quantities, ID collision with changed money/business/items, invalid void date, and valid surrounding rows all produce documented outcomes with no silent financial mutation.

### 10. Downloaded templates are cached but not applied to editors

**Evidence:** cache/selector probe and reference tracing; `online_templates.py`, `online_templates_ui.py`, `app.py:727–732`, built-in selectors/renderers.

A downloaded CV definition was present on disk but absent from `CV_NAMES`; no editor/renderer consumption path for downloaded definitions was found. The app opens the catalog without an apply callback. Downloading currently does not complete the advertised customization workflow.

**Improve:** typed, versioned template definitions; validation and size limits; editor selector integration; live preview; explicit **Use template** action; offline support and compatibility/error messaging. Use the configured Supabase endpoint/key rather than silently falling back to the baked-in project in the normal catalog UI path. Cache writes should be atomic and performed off the UI thread.

**Acceptance test:** publish → browse → preview → download → select → export → restart offline → reopen/export, for each supported category. Validate the online-template migration is included in the actual deployment setup; it exists separately from the consolidated schema.

### 11. Entitlement lookup contains destructive repair logic

**Evidence:** source review; final `get_machine_entitlements` definition in `supabase_schema.sql:1033–1079`; overlapping-key trigger at `129–146`.

The lookup counts unexpired base/combo keys including revoked entries and can revoke active licenses/add-ons before its request-UUID authorization check when duplicates exist. A normal read therefore also performs broad repair/revocation.

**Important qualification:** the existing serialized overlap trigger blocks ordinary new overlapping base-key issuance, including paused keys. This concern is about **legacy/imported/inconsistent duplicate data and side-effect ordering**, not a claim that every normal renewal produces duplicates.

**Improve:** authenticate first; make entitlement reads side-effect-free; move repair into an owner-only, previewable, audited transaction with an explicit selection policy. Test expired, paused, combo/add-on and legacy duplicate combinations in isolated database integration tests.

### 12. Owner dashboard can show incomplete or misleading analytics

**Evidence:** source review plus future-sale reproduction; `owner-dashboard.html:1280–1290,1318–1349,1440,1473–1484`.

- Lifetime heartbeat seconds substitute for selected-period online duration when no daily rows exist in that period. An inactive PC can appear to have substantial 7/30-day usage.
- Bounded pagination returns at most 12,000 rows for several feeds (3,000 stations) without a completeness indicator; keys are capped at 1,000. Large installations can silently undercount.
- Failed sales/online fetches become empty arrays; refresh can still announce success. Zero is not the same as unavailable.
- Range filters have a lower bound but no upper bound. A sale dated in 2099 was included in the current range in the probe.

**Improve:** separate lifetime from period data; display unavailable/partial/stale states; retain last-good data with its timestamp; aggregate revenue server-side with explicit pagination/completeness; enforce both date bounds and a consistent shop timezone.

### 13. Telemetry should be described as pseudonymous, with stronger writer authentication

**Evidence:** source review; `cloud_sync.py:728–795`; telemetry SQL functions near `supabase_schema.sql:1140–1230` and online-duration migration.

The code calls telemetry strictly anonymized while sending stable machine/shop identifiers plus business/station/city and usage information. That is not a sound promise of anonymity. Some RPC checks establish a license exists for a claimed machine ID, not possession of a device credential. Rate/seconds caps reduce abuse but are not proof of device identity.

**Improve:** accurate disclosure/consent, documented retention/deletion, minimize labels, device-bound authentication or signed short-lived credentials and sensible per-day aggregate bounds. Review local files/recovery handling against the shop's privacy obligations, including POPIA; this audit is not legal compliance certification.

## Priority 3 — reliability, performance and operational maturity

### 14. Releases should be immutable and customer-verifiable

**Evidence:** source review; `.github/workflows/build-usb.yml:158–188`; `cloud_sync.is_newer_version`; Windows packaging configuration.

Main-branch builds can overwrite assets for the same version using `--clobber`. A different binary under `0.8.3` will not be offered as a newer version to a client already on `0.8.3`; the comparison probe returned false as expected. This weakens traceability and predictable rollout/rollback.

No Authenticode signing configuration was found; this does not prove the externally distributed binary has never been signed. The app already has important Ed25519 update checks, which should be preserved. The download page currently instructs users to bypass SmartScreen.

**Improve:** unique versions and immutable tagged releases; serialized release jobs; exact build-tool pins; provenance/SBOM/checksums; Windows signing; staged rollout and tested rollback. Check untrusted/fork PR builds: the workflow tolerates a missing key at injection but later installer guards also need to handle throwaway artifacts consistently.

### 15. Remaining UI work should avoid blocking the Tk event loop

**Evidence:** source review; `forms_ui.py:146–166`, `cards_ui.py:181–211`, `sales_ui.py:33–48`, `online_templates_ui.py:171–180`.

Form/card previews execute render work directly in scheduled UI callbacks. Debouncing reduces frequency but does not move work off-thread. Report reads and template writes can also block, particularly on slow/network storage. Career-document preview already uses a worker; that path should not be reported as synchronous.

**Improve:** snapshot on UI thread, render/read on bounded workers, publish only the latest generation on the UI thread. Add cancellation/generation safety from finding 1, loading/error states and a modest preview cache. Measure input latency on the actual shop PCs.

### 16. Finish durability, backup and operational visibility

**Evidence:** source review and existing saved-work audit.

- Saved output and accounting-outbox persistence are separate resources. A process/power failure between them remains a reconciliation gap even though ordinary recording failures now have durable retries.
- Cloud-applied license records use direct `write_text` in `cloud_sync.py:539–611`, unlike atomic local activation/settings/output paths.
- Customer recovery/project files are not encrypted. The UI acknowledges this; disk permissions alone are not encryption or a retention policy.

**Improve:** durable save intent/reconciliation journal with carefully specified committed-output semantics; atomic license/cache replacement; scheduled verified database backups and a restore drill; health screen for pending sales, last sync, partial peers, disk space, recovery retention and app version. Never count an intent for a discarded/failed export as earned revenue.

## What is already working well

Preserve these during fixes:

- Saved successful exports automatically create receipt lines and ledger records, with durable retry files when recording fails.
- Unsaved drafts, discarded work, previews and recovery copies are excluded from earned sales.
- Local reports calculate daily/monthly totals by currency; voided sales do not count.
- Existing retries preserve sale identity, original date/station/rates and do not revive voided records.
- Settings and customer output paths use atomic replacement in important places.
- Project archive validation, ownership/RLS checks and signed updater verification provide useful existing defenses.
- Card/form state-sync hooks on navigation exist. Project/recovery loading switches screens before installing loaded state, avoiding the suspected overwrite issue. These were checked and are not findings.

## Recommended implementation sequence

1. **Privacy/integrity patch:** recovery generation barrier; immutable PC identity; surfaced partial-sync errors; regression tests reproducing the observed races/collisions.
2. **Accounting correctness:** complete change-based sync and void propagation; strict import/conflict rules; currency-safe dashboard and explicit completeness/date bounds.
3. **Owner security:** remove HTML sinks; implement server-enforced step-up/MFA; audit sensitive actions; repair entitlement read semantics and revocation state transitions.
4. **Complete promised workflows:** downloadable-template application end to end; worker-based previews; clear save/earnings/sync status.
5. **Operational release gate:** Windows smoke tests on representative hardware, database integration tests, backup/restore and migration drills, immutable signed release and update/rollback tests.

### Required regression coverage before claiming “fully functional”

- Slow worker timeout → clear → new customer → late old-worker completion.
- Two PCs with identical names, station rename and cloned portable identity.
- Full-history bootstrap beyond sync caps; oldest-record void; offline reconciliation.
- Multi-currency daily/monthly totals, changed currencies, invalid/future dates.
- Partial/corrupt sync, read-only share, unavailable cloud, restored SQLite backup.
- Forced termination at each output/outbox/ledger boundary; no discarded-job revenue.
- Hostile template strings and AAL1/AAL2 access across SQL and edge functions.
- License pause/revocation while busy or holding unsaved customer work.
- Download/apply/render template, offline restart, incompatible definition.
- Windows installer/update/rollback, fonts, real printer/scanner and low-disk scenarios.

The next best investment is to fix findings **1–6** and automate their reproductions before expanding the feature set.
