# Café Desk — security & data-integrity gates (five-whys work log)

> Companion to [the baseline audit](deep-app-audit.md), the [0.8.4 patch](deep-app-audit-fixes.md)
> and the [five-whys recheck](deep-app-five-whys.md). The 0.8.4 patch closed the
> in-process halves of the Priority 1 findings but explicitly left the items below
> as **not implemented**. This log tracks the remaining security and data-integrity
> gates required before a commercial release, worked section by section with a
> five-whys root-cause analysis, a fix and regression coverage for each.

**Method:** trace each safety property through writers, readers, storage types,
lifecycle paths and process boundaries; reproduce the failure with a failing test
before changing code; then run the full suites. No live cloud data, customer data
or release artifacts are touched.

## Section 1 — Customer-data isolation after a session change (done)

**Risk addressed:** one customer's information being restored after another
customer has used the computer.

### Five whys: why could a cleared customer's recovery data reappear?

1. **Why did the cleared recovery file come back?** A background auto-recovery
   worker finished *after* the clear/“Next customer” and published its snapshot
   to the recovery folder.
2. **Why did the late worker still publish?** The 0.8.4 generation barrier is
   enforced by a process-local `threading.Lock` and an in-memory generation
   counter: a worker that captured the generation before the clear is rejected
   only if it runs in the *same* process.
3. **Why is the in-process barrier insufficient?** Café Desk can be started more
   than once on the same computer (a portable USB copy run twice, a second copy
   of the folder). Each process has its own lock and its own generation counter,
   so a late worker in the other process never sees the bump and publishes the
   previous customer's snapshot anyway.
4. **Why can two instances share one recovery folder?** Nothing prevents a second
   instance from opening the same protected config folder; the audit explicitly
   required an “OS-level single-instance/locking policy” as separate hardening,
   and it was never implemented.
5. **Why did the tests not catch this?** Every recovery regression used a single
   `RecoveryStore` in one process; no test exercised two stores (two app
   instances) sharing one directory, so the barrier was never tested across its
   actual failure boundary.

**Root cause:** the publication barrier's state and lock were process-local —
there was no cross-process authority over who may publish or delete.

### Fix (`recovery_store.py`, `app.py`)

- The authoritative generation now lives in a marker file
  (`.cafedesk-recovery.generation`) in the protected config folder next to the
  recovery folder, so every process sees the same value.
- Every mutation (`save`, `clear`, `invalidate`) runs inside a cross-process
  exclusive lock (`fcntl.flock` on POSIX, Windows byte-range lock via
  `msvcrt`) on a lock file (`.cafedesk-recovery.lock`), so publication and
  deletion cannot interleave across processes.
- A worker holding an old generation is rejected under the same lock, no matter
  which process it belongs to; `save` also re-creates the marker if it was
  deleted externally.
- Bounded waits: `save` fails cleanly (next 60 s tick retries) and `clear`
  raises/reports failure instead of deleting while another instance may be
  publishing — a clear that cannot be serialised must not run, otherwise the
  late publish would resurrect the file. `invalidate` is best-effort (short
  timeout, in-memory fallback) because it runs on close/shutdown UI paths.
- `app.py` `delete_recovery_copies` now surfaces a lock failure as a warning
  instead of crashing.
- The marker and lock live outside the recovery folder, so the recovery folder
  itself contains only customer recovery data.

### Validation

- **5 new regressions** in `tests/test_audit_fixes.py::RecoveryBarrierTests`:
  cross-process clear rejects a late worker from another store; cross-process
  invalidate rejects the other store's late worker; cross-process rotation keeps
  both stores in sync and rejects the stale generation; marker/lock stay outside
  the recovery folder; lock contention makes `save` and `clear` fail cleanly
  without publishing or deleting.
- **Real two-process check:** process A captured generation 0, process B cleared,
  then A's late publication was rejected and no file reappeared (verified with
  separate OS processes).
- **318 Python tests pass** (313 baseline + 5 new; 1 pre-existing skip),
  **30 JavaScript tests pass**, version-sync and owner-digest checks pass,
  `git diff --check` clean.

### Remaining boundary for this section

The lock is per recovery folder and per machine; it is not encrypted storage,
secure erasure, or protection against an administrator who deletes files
directly. Running two instances is now *safe* (the barrier holds), but the
recommendation to run one instance per computer still stands for operational
clarity.

## Section 2 — API key exposure (done)

**Risk addressed:** the AI provider API key being exposed — through an error
message, a log record, or a URL — instead of staying protected at rest and in
memory.

Web best practice consulted for this section (OWASP Secrets Management Cheat
Sheet, OWASP API Security Top 10, NIST SP 800-53 IA-5/AC-6): an API key is a
bearer credential; storage is part of its security boundary; secrets must never
be logged, never appear in error messages, never travel in URLs, and must be
masked/redacted at every sink.

### Five whys: why could the API key be exposed?

1. **Why would the key reach a place it shouldn't?** Error messages were built
   from raw strings: the endpoint URL — which a user can paste with the key in
   its query string — was interpolated into connection-error and redirect
   messages without redaction. Only the HTTP error *body* was redacted.
2. **Why was the endpoint interpolated raw?** `services.call_ai_chat` applied
   `redact_secrets` at a single boundary (the HTTP error body) but not to the
   connection-error/redirect messages; `services.detect_ai_provider` validated
   only the URL scheme and never rejected a URL that carries the key.
3. **Why does a key-in-URL matter?** Secrets must never travel in URLs — they
   leak into error messages, logs, proxy logs and browser history. A failed
   connection or a blocked redirect would then carry the key out of the app.
4. **Why was there no defence in depth at the logging layer?** The app writes a
   durable rotating log (`cafedesk.log`) with full tracebacks, but no
   redaction at the logging layer — any future exception whose message or
   chained traceback carries the key would be persisted to disk silently.
5. **Why did tests not catch this?** Existing AI tests covered redaction of
   HTTP error bodies, but not connection-error/redirect messages, not URLs with
   embedded keys, and not the logging layer.

**Root cause:** secret redaction was applied at a single boundary instead of at
message construction and at persistence (logs).

### Fix (`services.py`, `logging_utils.py`, `app.py`)

- `services.detect_ai_provider`: a custom endpoint URL that contains the API
  key or any key-like token is **rejected** before any request — secrets must
  never travel in a URL.
- `services.call_ai_chat`: the endpoint is redacted (exact key + key-like
  patterns) in the connection-error and redirect messages, so a URL that
  somehow carries a key cannot leave the app through an error.
- `logging_utils`: every log record now passes through
  `_SecretRedactingFormatter`, which removes registered exact secrets and
  key-like tokens from the whole output, **including tracebacks**. The app
  registers the in-memory key when it is set or used
  (`app._set_key_memory`, plus the request/save paths) and unregisters it when
  the key is cleared (staff lock, Next customer, clear/forget key).
- The key's existing protections are unchanged: AES-256-GCM + scrypt under the
  staff PIN (plus DPAPI on Windows) at rest, owner-only file permissions,
  atomic writes, tamper-evident payload, separate file from settings/exports,
  and clearing from memory on lock.

### Validation

- **8 new regressions:** custom URL with an embedded key is rejected; URL with
  a key-like token is rejected; connection-error message redacts the endpoint;
  connection-error with a key-bearing URL is redacted; redirect message contains
  no key; the log redactor removes registered and pattern secrets; the logger
  formatter redacts messages *and* chained tracebacks; `_set_key_memory`
  registers on set and unregisters on clear/replace (wiring test updated).
- **326 Python tests pass** (318 + 8 new; 1 pre-existing skip), **30 JavaScript
  tests pass**, version-sync and owner-digest checks pass, `git diff --check`
  clean.

### Remaining boundary for this section

The log redactor covers standard provider key formats and any exact key the app
registers. A non-standard secret that is never registered and never matches a
key-like pattern could still appear in a log record if some future code path
puts it in an exception message; the request paths register the exact key to
close that window for AI keys. The key remains recoverable by someone with
administrator access to the same Windows account who also knows the staff PIN
(documented, unavoidable for a local-first desktop app).

## Section 3 — Licence integrity (COMPLETE)

**Risk.** Two failures that a passing-test count cannot see:

* **A.** A public entitlement read RPC performed a *destructive repair*: whenever
  one Machine ID had more than one active base licence, it bulk-revoked **all**
  unexpired base keys for that Machine ID — before the request-UUID authorization
  check, on every customer check-in, with the owner never choosing which key to
  keep. Legacy rows (issued before the `guard_duplicate_base_license` trigger)
  can still carry duplicates, so this path is reachable in the field.
* **B.** Remote revocation enforcement ended in the ordinary, **user-cancelable**
  `close_app()` prompt; since licensing is only checked at startup, declining the
  prompt keeps a revoked app running.

### 5 Whys

**A — why could the wrong licence be revoked?**

1. **Why was a licence revoked?** A duplicate-base-key repair block inside
   `check_machine_entitlements` set `revoked = true` for every unexpired base key
   of the Machine ID.
2. **Why did that block run during a *read*?** The RPC performs the repair as a
   side effect whenever it sees more than one active key — it does not need to
   modify anything to answer its question.
3. **Why was the repair reachable by an ordinary customer check-in?** The
   repair fires *before* the request-UUID authorization check (and even for the
   legacy one-argument call), so any caller triggers it.
4. **Why did the owner never choose?** No policy input exists: the RPC picked a
   survivor implicitly (leaving the earliest row active) and destroyed the rest
   silently; nothing is audited, and `guard_duplicate_base_license` only blocks
   *new* duplicates, so legacy rows reach the repair.
5. **Root cause.** **Destructive repair coupled to a read path, instead of an
   authenticated, explicit, audited owner action.**

**B — why was remote revocation ineffective?**

1. **Why did the revoked app keep running?** The enforcement path called the
   ordinary `close_app()`, which shows consent prompts the user can decline.
2. **Why did declining the prompt matter?** `close_app()` is deliberately
   cancelable (busy-task, unsaved-work and unprotected-sales prompts) — that is
   its purpose.
3. **Why was revocation routed through a consent-based action?** There is no
   mid-session enforcement state: licensing is checked only at startup, so once
   the app is running, the only "stop" mechanism is the close prompt.
4. **Why did the pre-check in `_finish_remote_entitlement_sync` not help?** It
   only decided *whether* to attempt the close; it still delegated the actual
   stop to a user-cancelable UI action.
5. **Root cause.** **Revocation enforcement delegated to a user-cancelable UI
   action instead of an enforced state.**

### Fixes

**A (server-side).** New migration
`supabase/migrations/20261126120000_entitlement_read_side_effect_free.sql`:

- `check_machine_entitlements` is **side-effect free**: duplicate keys are simply
  not returned; no licence is ever revoked by a read.
- New audit table `public.owner_license_actions` (`acted_at`, `machine_id`,
  `action`, `keep_license_id`, `revoked_license_ids`, `detail`).
- New owner-only RPC `owner_review_duplicate_machine_keys(machine_id, action,
  keep_license_id)`: `preview` lists duplicates without touching anything;
  `resolve` **requires** an explicit `keep_license_id` matching a listed key,
  advisory-locks the Machine ID, revokes the other unexpired base keys with a
  named reason (`Duplicate Machine ID: owner review (kept X)`), writes the audit
  row and returns the revoked ids. Guarded by `is_cafedesk_owner()` (errcode
  `42501`), granted to `authenticated` only, revoked from public/anon.

Both copies of `check_machine_entitlements` in `supabase_schema.sql` (original and
the request-UUID version) had their destructive repair blocks removed; the read
path now only aggregates active keys and enforces the request-UUID authorization.

**B (client-side).**

- `app.py`: `close_app(force=False)` — `force=True` skips every confirmation
  (busy task, unsaved work, unprotected sales) and closes immediately.
- `app.py`: new `_enforce_licence_revocation(reasons)` — sets the session
  revoked flag, locks the workspace, shows a **non-dismissable** notice whose
  `WM_DELETE_WINDOW` is a no-op and whose only action is "Quit Café Desk"
  (force-close); if no display is available it force-closes directly.
- `_finish_remote_entitlement_sync` now calls `_enforce_licence_revocation`
  instead of the cancelable `close_app()`.

### Validation

- **New SQL regressions** `tests/entitlement-read-safety.test.mjs` (PGlite, real
  SQL execution of the migration): read never revokes; authorized read returns
  only the request-linked key; owner preview lists without revoking; owner
  resolve keeps the chosen key, revokes the others and writes the audit row;
  resolve without a valid keep selection is refused; non-owners are refused —
  **7/7**.
- **New Python regressions** `LicenceRevocationEnforcementTests` (6): force-close
  skips confirmations; ordinary close still asks; the revocation callback
  enforces instead of cancelable close; enforcement locks + force-closes with no
  display; the notice is non-dismissable.
- **332 Python tests pass** (326 + 6 new; 1 pre-existing skip), **37 JavaScript
  tests pass** (30 + 7 new), version-sync and owner-digest checks pass,
  `git diff --check` clean.

### Remaining boundary for this section

The owner RPC is only as safe as its grant: it is granted to `authenticated` with
the `is_cafedesk_owner()` guard. The migration must be applied to the live Supabase
project before release (a deployment step, not a code step). Existing legacy
duplicate rows that triggered the old repair path are left untouched by design —
the owner resolves them explicitly via `preview`/`resolve`, which is the point of
the fix.

## Section 3 — Architectural recheck (COMPLETE)

A fresh architect-level recheck of the Section 3 work (re-read every artifact,
consulted current practice on entitlement revocation, audit-log immutability and
PostgreSQL overload/rate-limit behaviour) found three issues in the first pass of
the fixes and corrected them.

### Finding 1 — enforcement was not fail-closed at the gate

**Recheck observation.** `_licence_revoked` was set but never consulted; the
universal operation gate `require_staff()` (11 call sites) only checked
`staff_unlocked` (a PIN), so a user who knew the PIN could unlock and keep using
the app with a revoked licence.

**5 Whys**

1. Why can a revoked app keep being used after enforcement? → `require_staff()`
   unlocks via PIN despite the revoked session.
2. Why does it unlock? → It checks only `staff_unlocked` (UI/PIN state), not
   licence state.
3. Why isn't licence state consulted? → Revocation was recorded as a flag and a
   dialog, never enforced at the access-control gate.
4. Why only as flag + dialog? → The fix was designed at the UI layer instead of
   the deny path.
5. **Root cause.** **Enforcement implemented as a UI affordance instead of the
   deny path of the access gate** — the opposite of fail-closed practice
   (the deny path must be the default; unlock a narrow exception).

**Fix.** `require_staff()` now refuses while `_licence_revoked` is set and
re-applies enforcement; the PIN-unlock success path re-enforces immediately if
revocation landed while the dialog was open (and skips loading the saved AI
key); `_enforce_licence_revocation` no longer stacks dialogs.

### Finding 2 — the audit table was mutable

**Recheck observation.** Best practice for audit trails is append-only with
immutability triggers (UPDATE/DELETE **and** TRUNCATE — row triggers do not fire
for TRUNCATE), written in the same transaction as the change. The table had the
same-transaction write but no immutability controls and no actor identity.

**5 Whys**

1. Why could the audit history be silently altered? → No append-only trigger on
   `owner_license_actions`.
2. Why no trigger? → The table was added as a plain table.
3. Why a plain table? → The design focused on recording the action, not on
   protecting the record.
4. Why wasn't the record protected? → Append-only enforcement (triggers +
   least-privilege posture) was not applied to the audit table.
5. **Root cause.** **Audit integrity was assumed from "written in a transaction"
   instead of enforced with immutability controls.**

**Fix.** `owner_license_actions` gains `actor_email` (recorded from
`auth.jwt()` at resolve time) and `owner_license_actions_append_only()`
triggers rejecting UPDATE, DELETE and TRUNCATE; corrections are new events.

### Finding 3 — a dead, ambiguous overload in the consolidated schema

**Recheck observation.** `supabase_schema.sql` carried two
`check_machine_entitlements` definitions: a one-argument overload and the
two-argument one. The migration history shows the one-arg overload was already
dropped by `20261119200000_rpc_access_hardening.sql` — the schema copy was stale
**schema drift**. Two overloads make single-argument calls ambiguous (PostgreSQL
error 42725), so the one-arg overload was dead and uncallable.

**5 Whys**

1. Why was the legacy one-argument read still in the schema? → The consolidated
   schema file was not updated when the overload was dropped.
2. Why did nobody notice? → No test or check pinned the schema to what the
   migrations actually produce.
3. Why did the first-pass recheck try to *rate-limit* the one-arg read instead
   of removing it? → The recheck assumed the overload was live and chose to
   harden it rather than question its existence.
4. Why is a live-looking but dead overload dangerous? → It invites future calls
   that fail ambiguously, and it hides schema drift from reviewers.
5. **Root cause.** **The consolidated schema and the migrations had drifted, and
   the recheck trusted the schema's presentation of reality instead of the
   migration history.**

**Fix.** Removed the one-argument overload (and its grant) from the schema;
the surviving two-argument signature is the only one, matching the live database
and the app's call. A regression test asserts exactly one
`check_machine_entitlements` exists in both the schema and the database.

### Recheck validation

- SQL regressions expanded to **9** (PGlite, real SQL execution): read never
  revokes; authorized read returns only the request-linked key; owner preview
  lists without revoking; owner resolve keeps the chosen key, revokes the
  others, records the actor and writes the audit row; audit table rejects
  UPDATE/DELETE/TRUNCATE; exactly one `check_machine_entitlements` overload in
  schema and database; resolve without a valid keep selection is refused;
  non-owners are refused.
- Python enforcement tests expanded to **10**: force-close skips confirmations;
  ordinary close still asks; callback enforces instead of cancelable close;
  `require_staff` refuses while revoked even when unlocked; normal unlock still
  works when not revoked; revocation landing during PIN entry re-enforces and
  skips the saved-key load; enforcement does not stack dialogs; no-display
  path force-closes; the notice is non-dismissable.
- Full suite after the recheck: **336 Python tests OK (skipped=1), 39 JavaScript
  tests OK**; version-sync, owner-digest and `git diff --check` clean.

### Remaining boundary after the recheck

The one-argument overload removal is a schema-file correction; the live database
never had it (the earlier hardening migration dropped it). The audit triggers
protect against application-level and privileged rewrite except a superuser who
runs `ALTER TABLE ... DISABLE TRIGGER` — noted in the immutability guidance as
the residual risk, mitigated by least-privilege roles and off-box digest
anchoring if required.

## Section 1 — Architectural recheck (COMPLETE)

A fresh architect-level recheck of the Section 1 recovery barrier (re-read
`recovery_store.py`, traced every call site of `save`/`clear`/`invalidate` in
`app.py`, consulted current practice on cross-process locking and crash
consistency).

**What held.** The design is aligned with current practice: `fcntl.flock` /
`msvcrt.locking` are kernel-held and released automatically when the holder's
fd closes or the process dies — no stale-marker heuristic to get wrong. The
generation check in `save()` runs under the same cross-process lock that
`clear()`/`invalidate()` take, so a late worker cannot publish after a clear in
any process. The customer-switch path (`clear_customer`) correctly routes through
the strict `clear()`, not the best-effort `invalidate()`.

### Finding 1 — the lock-acquisition failure model conflated "busy" with "unavailable"

**Recheck observation.** On a filesystem that cannot enforce advisory locks
(some NFS/SMB/FUSE mounts), every `flock` attempt fails with
ENOLCK/EOPNOTSUPP/ENOSYS. The acquisition loop treated every `OSError` as "the
lock is held, wait and retry", so `clear()`/`save()` stalled the full 15-second
timeout before failing — a permanent condition handled as a transient one.

**5 Whys**

1. Why did clear/save stall for 15 s on some filesystems? → The acquisition loop
   retried on every `OSError`, including "locking not supported".
2. Why did it retry on unsupported-lock errors? → All `OSError`s were classified
   as "lock is busy".
3. Why were unsupported-lock errors classified as busy? → No errno
   classification in the acquisition loop.
4. Why no classification? → The loop was written for the normal "locked by a
   peer" case only.
5. **Root cause.** **The failure model conflated a transient condition (lock
   busy) with a permanent one (locking unavailable), so a permanent condition
   was handled as transient.**

**Fix.** `_FileLock` now fails fast with a clear message when the filesystem
reports locking as unavailable (ENOLCK/EOPNOTSUPP/ENOSYS on POSIX) — fail closed,
never silently degrade the isolation barrier.

### Recheck validation

- New regression `test_lock_fails_fast_on_a_filesystem_without_locking`: with
  `flock` forced to raise ENOLCK, the lock raises in under 2 s (not the 15 s
  timeout) with the explicit "cross-process locking" message.
- Cross-process barrier tests (10) still pass; **339 Python + 39 JavaScript
  tests** pass overall; version-sync, owner-digest and `git diff --check` clean.

### Remaining boundary after the recheck

The marker file is authoritative across processes; if it is deleted by an
external actor with config-folder write access, a fresh process falls back to
its in-memory mirror (0) when bumping. Restoring the marker from a protected
location is outside the threat model (the config folder is already 0o700,
owner-only); the barrier remains sound against the realistic failure modes
(crash, late worker, second instance).

## Section 2 — Architectural recheck (COMPLETE)

A fresh architect-level recheck of the Section 2 key-exposure gate (re-read
`logging_utils.py`, `services.py` AI paths, every `register_secret` call site
and the key StringVar wiring in `app.py`, consulted OWASP secrets-management
and logging guidance).

**What held.** The AI request path keeps the key only in the `Authorization`
header, blocks redirects, and redacts every constructed error message; the log
formatter redacts registered secrets and key-like patterns from messages *and*
tracebacks; the app has a single configured logger with no debug-level library
logging enabled.

### Finding 1 — the key was unregistered while sitting in the entry widget

**Recheck observation.** Registration happened only at the action call sites
(Test AI / Save key / Improve). A key typed or pasted into the masked entry
widget sits in memory *unregistered* until one of those actions runs — any
logging path that touched the key before registration would persist it
unredacted.

**5 Whys**

1. Why could the key reach a log while in the entry widget? → It is in memory
   but not in the redaction registry.
2. Why isn't it registered while in the widget? → Registration is triggered by
   explicit call sites, not by the key entering memory.
3. Why is registration call-site-driven? → `_set_key_memory` only runs on
   programmatic set/clear; user typing sets the StringVar directly.
4. Why does typing bypass registration? → No observation of the StringVar's
   value changes.
5. **Root cause.** **Secret registration was coupled to action call sites
   instead of to the secret entering memory.**

**Fix.** A `trace_add('write', ...)` on the key StringVar keeps the registry in
step with whatever value lands in the widget — typing, pasting, programmatic
set or clear. `_set_key_memory` now only writes the var; `_sync_key_registration`
is the single sync point. The explicit call-site registrations remain as
defence in depth.

### Finding 2 — the stored error note was not redacted

**Recheck observation.** `record_last_error` stored the raw exception text in
`_last_gui_error`, which the Help screen displays. Redaction was applied only
at the log sink, not at the storage/display boundary.

**5 Whys**

1. Why could the key be displayed in the Help screen? → `_last_gui_error` holds
   the raw exception text.
2. Why is the raw text stored? → The note is built and stored before logging
   (which redacts).
3. Why is the stored copy not redacted? → Redaction was applied only at the log
   boundary.
4. Why only at the log boundary? → Storage was treated as internal
   bookkeeping, not as a display/persistence boundary.
5. **Root cause.** **Redaction was applied at the log sink instead of at every
   boundary where the text is stored or displayed.**

**Fix.** `record_last_error` stores the redacted note (the log line keeps the
original, which the formatter redacts again).

### Finding 3 — library/root logger output bypassed the redacting formatter

**Recheck observation.** Third-party loggers propagate to the root logger, which
had no handler; Python's `logging.lastResort` prints their WARNING+ records to
stderr **without** the redacting formatter. One future dependency that logs
request details could persist a key outside the redaction boundary.

**5 Whys**

1. Why could a library log the key unredacted? → Library loggers propagate to
   root, which had no redacting handler.
2. Why does root have no redacting handler? → Only the `cafedesk` logger was
   configured.
3. Why only `cafedesk`? → The configuration assumed all app logging goes through
   it.
4. Why is that assumption risky? → Any library that logs request details would
   print the key via `lastResort`.
5. **Root cause.** **Redaction coverage was scoped to the app's own logger
   instead of to every log sink that could receive the key.**

**Fix.** A redacting stream handler is attached once to the root logger (flagged
`_cafedesk_redacting`); the app logger keeps `propagate = False`, so app records
are not printed twice.

### Recheck validation

- New regressions: `test_lock_fails_fast_on_a_filesystem_without_locking`
  (Section 1); `test_record_last_error_stores_a_redacted_note`,
  `test_root_logger_has_a_redacting_handler`, and the rewritten
  `test_set_key_memory_sets_var_and_trace_syncs_registry` (Section 2).
- **339 Python tests OK (skipped=1), 39 JavaScript tests OK**; version-sync,
  owner-digest and `git diff --check` all clean.

### Remaining boundary after the recheck

The redactor covers the exact registered key and standard provider key shapes.
A non-standard secret that is never registered and never matches a key-like
pattern could still appear in a log record if some future code path puts it in
an exception message — the trace registration and the root handler close the
realistic paths; rotation remains the final control if a key is ever confirmed
exposed.

## Final architect pass over all three gates (COMPLETE)

One more full pass with the same rules: re-read every artifact from scratch,
consulted the web for the specific patterns under review (PostgreSQL advisory-lock
keyspace practice; percent-encoded secret bypass — a well-documented redaction
failure class in several real projects), applied 5 Whys to each new finding, and
fixed them.

### Section 1 — finding: the invalidate fallback and the save check were never connected

**Recheck observation.** `invalidate()`'s documented in-memory fallback bumps
only `self._generation` when the cross-process lock is unavailable. But
`save()` validates the caller's generation against the **marker file**, falling
back to the mirror only when the marker is unreadable. So after a fallback
invalidate, an abandoned recovery worker's snapshot (captured before the
fallback) still matched the stale marker and published — the fallback's bump
guarded nothing on the save path.

**5 Whys**

1. Why can an abandoned save still publish after the watchdog invalidates? → The
   fallback bumps only the mirror, and `save()` reads only the marker.
2. Why does `save()` read only the marker? → The marker was treated as the sole
   authoritative generation; the mirror as a read-fallback only.
3. Why was the mirror only a read-fallback? → The fallback path was written as
   "degraded mode" without a corresponding check in `save()`.
4. Why was there no corresponding check? → The fallback was assumed to guard
   "this process", but nothing on the save path consulted it.
5. **Root cause.** **The fallback changed the state the save path reads without
   changing the save path's read — the two halves of the fallback were never
   connected.**

**Fix.** `save()` now also rejects when the caller's generation is below the
in-process mirror (`generation < self._generation`). The mirror only ever gets
ahead of the marker via the fallback, so this check rejects exactly the stale
saves the fallback was meant to block; normal locked invalidate/clear keep
mirror == marker, so legitimate saves are unaffected. Recovery resumes at the
next successful locked invalidate/clear.

### Section 2 — finding: a percent-encoded key in a custom URL bypassed the rejection

**Recheck observation.** `detect_ai_provider` rejects a custom URL whose raw
text contains the key or a key-like token. A key percent-encoded into the URL
(e.g. `?key=sk%2Dtest%2Dabc...`) matches neither the exact-key literal nor the
key-pattern regex, so it passed — and the URL (with the key) would then reach
error messages and logs, where the same raw-text redaction would miss it again.

**5 Whys**

1. Why can a key-bearing URL pass the rejection check? → The check compares the
   raw URL text; an encoded key does not match.
2. Why doesn't the encoded form match? → The exact match and the pattern regex
   both operate on raw text; encoding breaks the literal shape.
3. Why is the raw text the only form checked? → The check was written for the
   literal, common case (a URL copied with the key in it).
4. Why is that insufficient? → URL encoding is a standard, trivially available
   transformation; a key placed in a query string can be encoded by the user or
   a tool.
5. **Root cause.** **The secret-in-URL check inspected one textual form of the
   URL instead of every form the key can take in it.**

**Fix.** The rejection now checks both the raw URL and its percent-decoded form
(`urllib.parse.unquote`). The web confirmed this is the correct remediation:
percent-encoded secret bypass is a documented redaction failure class, and the
standard fix is to normalize (decode) before matching so both forms are covered.

### Section 3 — hardening: enforcement must not propagate a dead-shell TclError

**Recheck observation.** `_enforce_licence_revocation` called `lock_staff()` and
`status.set(...)` *outside* the `TclError` guard. If the widget shell is already
gone (shutdown race), the exception propagated out of the enforcement path
instead of force-closing — the enforcement state was applied but the method did
not complete its contract.

**Fix.** The lock/status steps are inside the guard: a dead shell now force-closes
immediately. Enforcement can never be interrupted by a UI-state exception.

**Also verified against the web (no change needed).** The owner RPC's
`pg_advisory_xact_lock(hashtextextended(v_mid, 0))` is the correct pattern:
transaction-level locks auto-release at commit/rollback, and `hashtextextended`
hashes into the 64-bit keyspace (the 32-bit `hashtext` is the collision-prone
choice). A theoretical collision between two Machine IDs would only serialise
unrelated resolves — contention, not corruption — and the Machine-ID keyspace is
small. The RPC's keep-selection is re-validated against a fresh snapshot under
the lock, and the duplicate-guard trigger prevents new duplicates from appearing
during the resolve window.

### Final-pass validation

- New regressions: `test_save_rejects_generation_below_the_inprocess_mirror`
  (Section 1); `test_percent_encoded_key_in_custom_url_is_rejected` (Section 2);
  `test_enforcement_force_closes_when_the_shell_is_gone` (Section 3).
- **342 Python tests OK (skipped=1), 39 JavaScript tests OK**; version-sync,
  owner-digest and `git diff --check` all clean.

### Remaining boundaries (final)

- Section 1: the marker is authoritative; external deletion of the marker file
  (requires config-folder write access) makes a fresh process fall back to its
  in-memory mirror — inside the existing threat model (the config folder is
  0o700, owner-only).
- Section 2: the redactor covers the registered key and standard provider key
  shapes in both raw and decoded URL forms; a non-standard secret never
  registered and never pattern-matching could still appear if a future code path
  puts it in an exception message — rotation remains the final control.
- Section 3: the owner RPC is granted to `authenticated` behind the
  `is_cafedesk_owner()` digest guard; the migration must be applied to the live
  Supabase project before release. Remote revocation takes effect at the next
  10-minute entitlement sync (the documented recheck interval).

## Follow-up — Custom AI endpoint hostname validation (COMPLETE)

**Risk addressed:** a custom OpenAI-compatible endpoint URL bypassing the HTTPS
requirement via string-prefix spoofing, authority confusion or parser
differentials — transmitting the bearer API key and customer draft over
cleartext HTTP to a non-loopback server — or triggering provider-specific
model/header/payload mutations via URL substring collisions, or persisting an
unvalidated/secret-bearing `custom_url` into `ai-key.json`.

### Five whys: why could custom AI endpoint validation be bypassed?

1. **Why could a custom endpoint send the API key and customer text over
   cleartext HTTP to an external server (e.g. `http://localhost.evil.com/v1`,
   `http://127.0.0.1.evil.com/v1`, `http://localhost@evil.com/v1`, or
   `http://evil.com\@localhost/v1`), accept hostless `https:///v1`, or trigger
   wrong-provider headers/models?**
   `services.detect_ai_provider` checked
   `url.startswith(('https://', 'http://localhost', 'http://127.0.0.1'))` and
   `normalize_ai_model`/`call_ai_chat` checked `'api.groq.com' in endpoint_url`
   instead of parsing and validating the URL's authority and hostname.
2. **Why did prefix and substring checks accept hostile or malformed URLs?**
   In URL syntax, the characters following `http://localhost` or
   `http://127.0.0.1` need not end the hostname: `.evil.com` extends it into an
   external domain, `.10` extends the IPv4 octet (`127.0.0.10`), `@evil.com`
   demotes `localhost` to URL userinfo while making `evil.com` the destination
   host, `https:///v1` has a scheme prefix with no host at all, and a provider
   domain in a subdomain suffix or query parameter matches `' in endpoint_url'`.
3. **Why wasn't a plain `urllib.parse.urlsplit(url).hostname` check used — and
   why is naive `urlsplit` alone still insufficient?**
   Validation was originally written as a UI format hint ("starts with
   `https://`") rather than a network trust-boundary parser. Moreover, naive
   `urllib.parse.urlsplit` disagrees with `urllib3`/`requests` (WHATWG URL
   parsing) on backslashes (`http://evil.com\@localhost/v1` yields `urlsplit`
   hostname `'localhost'` while `urllib3` connects to `'evil.com'`), allows
   embedded credentials, and accepts malformed DNS labels, non-canonical IPv4
   notations (`0177.0.0.1`, `127.1`, `2130706433`, `0x7f000001`), and
   unspecified/link-local/metadata/mapped IPs (`0.0.0.0`, `::`,
   `169.254.169.254`, `::ffff:...`).
4. **Why could an invalid or secret-bearing `custom_url` also reach disk or
   partially mutate in-memory UI state?**
   Validation ran only inside `detect_ai_provider` when `Custom
   OpenAI-compatible URL` was active, not in `ai_key_store.save_key` (which
   writes a plaintext display copy of `custom_url` in `ai-key.json`) and only
   *after* `_load_saved_ai_key` had already populated the in-memory key,
   provider and model. In addition, the percent-decoded secret check compared
   `redact_secrets(unquote(url), key) != url` (decoded vs raw), falsely
   rejecting benign percent-encoding such as `%20` while missing
   double-encoded keys.
5. **Why did tests not catch this?**
   All prior custom-URL tests used a single clean host (`https://example.com`)
   to exercise query-key redaction; none tested adversarial authorities,
   parser differentials, provider-substring collisions or `save_key` URL
   persistence.

**Root cause:** URL trust decisions (cleartext loopback exemption, host
validity, secret-free authority, and provider identification) were evaluated on
**raw unparsed URL strings** at request time instead of **strict,
canonicalized, parser-consistent URL/hostname validation** across both request
and persistence boundaries.

### Fix (`ai_safety.py`, `services.py`, `ai_key_store.py`, `app.py`)

- `ai_safety.validate_custom_ai_url`:
  - Rejects empty/oversized URLs, whitespace, ASCII control characters,
    backslashes (`\`), userinfo/credentials (`@`), fragments (`#`), empty or
    out-of-range ports, percent-encoded hostnames, and embedded keys/tokens
    across up to four percent-decoding passes (fixing the `unquote(url) != url`
    false positive on clean `%20` paths).
  - Permits cleartext `http://` **only** for exact local loopback hosts
    (`localhost`, `127.0.0.1`, `[::1]`).
  - Requires `https://` for every non-loopback endpoint, validating multi-label
    RFC 1123 / IDNA 2008 hostnames (blocking NFKC/fullwidth-dot confusables,
    single-label/malformed hosts, and non-canonical numeric/octal/hex/shorthand
    IPv4 literals via a strict letter-initial TLD rule) or valid unicast IP
    literals (blocking unspecified, link-local/cloud-metadata, multicast,
    reserved, non-`127.0.0.1` loopback, and IPv4-mapped IPv6 addresses).
  - Expands `/v1` on the parsed path component only (never corrupting query
    parameters) and rebuilds the canonical URL via `urlunsplit` so `urllib3`
    receives the exact validated `(scheme, host, port)`.
- `services.py`: `detect_ai_provider` delegates custom URL validation to
  `ai_safety.validate_custom_ai_url(..., expand_v1=True)`; `normalize_ai_model`
  and `call_ai_chat` match `ai_safety.endpoint_hostname(endpoint)` instead of
  raw URL substrings.
- `ai_key_store.py` & `app.py`: `save_key` validates and canonicalizes
  `custom_url` before writing `ai-key.json`; `save_key_now` validates before
  prompting for the PIN and clears stale hidden custom URLs when a built-in
  provider is selected; `_load_saved_ai_key` validates before loading the key
  into memory or updating UI variables.

### Validation

- **8 new regression test methods** (covering 70+ valid and adversarial URL
  sub-cases, `urllib3` host parity, double-encoded keys, provider-substring
  non-interference, `save_key` rejection of bad/secret URLs, and fail-closed
  `_load_saved_ai_key` state ordering) in `tests/test_ai_safety.py` and
  `tests/test_ai_key_store.py`.
- **350 Python tests OK (skipped=1), 39 JavaScript tests OK**; version-sync,
  owner-digest and `git diff --check` all clean.
