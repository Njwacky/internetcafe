// Real PostgreSQL execution via PGlite (no live Supabase project).
// Section 3 gate: entitlement reads must be side-effect free and duplicate-key
// repair must be owner-only, previewable, audited and explicit.
import { PGlite } from '@electric-sql/pglite';
import { before, after, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let db;
const schema = await readFile(new URL('../supabase_schema.sql', import.meta.url), 'utf8');
const migration = await readFile(
  new URL('../supabase/migrations/20261126120000_entitlement_read_side_effect_free.sql', import.meta.url),
  'utf8');

const MID = 'CD-1234-5678-90AB';
const REQ_ID = '11111111-1111-1111-1111-111111111111';
const OWNER_EMAIL = 'owner@example.com';
const OWNER_DIGEST = '72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc';

function extractTable(name) {
  const m = schema.match(new RegExp(`create table if not exists public\\.${name} \\([\\s\\S]*?\\n\\);`));
  assert.ok(m, `table ${name} in schema`);
  return m[0];
}

function extractFunction(name) {
  const m = schema.match(new RegExp(`create or replace function public\\.${name}\\([\\s\\S]*?\\n\\$\\$;`));
  assert.ok(m, `function ${name} in schema`);
  return m[0];
}

async function seedKey(licenseId, { linkedRequest = null } = {}) {
  const hexTail = licenseId.replace(/[^0-9a-f]/gi, '').toLowerCase().padEnd(12, '0').slice(0, 12);
  await db.exec(`insert into public.issued_machine_keys
      (id, license_id, machine_id, customer, key_type, token, issued_at, expires_at, activation_request_id)
    values ('00000000-0000-0000-0000-${hexTail}'::uuid,
            '${licenseId}', '${MID}', 'Test Shop', 'license', 'tok-${licenseId}',
            '2026-01-01', '2027-01-01', ${linkedRequest ? `'${linkedRequest}'::uuid` : 'null'})`);
}

async function rpc(name, args) {
  const result = await db.query(`select public.${name}(${args.map((_, i) => `$${i + 1}`).join(', ')}) as result`, args);
  return result.rows[0].result;
}

async function keyStates() {
  const res = await db.query(
    "select license_id, revoked from public.issued_machine_keys where machine_id = $1 order by license_id", [MID]);
  return res.rows.map(r => [r.license_id, r.revoked]);
}

before(async () => {
  db = new PGlite();
  await db.exec('create role anon; create role authenticated;');
  // auth.jwt() stub reading session GUCs so tests can exercise owner identity and MFA (AAL1/AAL2).
  await db.exec(`create schema auth;
    create function auth.jwt() returns jsonb language plpgsql stable as $$
    begin
      return jsonb_build_object(
        'email', current_setting('app.jwt_email', true),
        'aal', coalesce(current_setting('app.jwt_aal', true), '')
      );
    end;
    $$;`);
  // pgcrypto digest() stub: the real owner digest only for the test owner email.
  await db.exec(`create or replace function digest(data text, type text) returns bytea language sql immutable as $$
      select case when data = 'CAFE-DESK-OWNER-V1|${OWNER_EMAIL}'
                   then decode('${OWNER_DIGEST}', 'hex')
                   else decode(repeat('00', 32), 'hex') end
    $$;`);
  for (const name of ['issued_machine_keys', 'activation_requests', 'machine_rpc_rate_limits', 'owner_license_actions', 'owner_audit_log']) {
    await db.exec(extractTable(name));
    await db.exec(`alter table public.${name} enable row level security;`);
  }
  await db.exec('alter table public.issued_machine_keys add column if not exists activation_request_id uuid;');
  for (const name of ['enforce_machine_rpc_rate_limit', 'cafedesk_owner_digest', 'is_cafedesk_owner']) {
    await db.exec(extractFunction(name));
  }
  await db.exec(migration);
  // Exercise the latest repair implementation, not only its historical version.
  await db.exec(await readFile(new URL('../supabase/migrations/20261126180000_duplicate_repair_lock_order.sql', import.meta.url), 'utf8'));
});

after(async () => { await db?.close(); });

beforeEach(async () => {
  // The audit table is append-only in production; tests reset it as an operator
  // would (disable triggers, truncate, re-enable).
  await db.exec('alter table public.owner_license_actions disable trigger all;');
  await db.exec(`truncate public.issued_machine_keys, public.activation_requests, public.owner_license_actions, public.owner_audit_log;`);
  await db.exec('alter table public.owner_license_actions enable trigger all;');
  await db.exec(`set app.jwt_email = '${OWNER_EMAIL}'; set app.jwt_aal = 'aal2';`);
});

test('consolidated schema dropped the destructive read-time revocation', () => {
  assert.doesNotMatch(schema, /revoke_reason = 'Duplicate Machine ID: contact the owner for review'/);
  assert.match(schema, /owner_review_duplicate_machine_keys/);
  assert.match(schema, /No destructive repair here/);
  assert.match(migration, /create or replace function public\.owner_review_duplicate_machine_keys/);
  // The public read function must not perform the destructive update (the owner
  // resolve path legitimately contains one, so scope the check to the read RPC).
  const readFn = migration.match(/create or replace function public\.check_machine_entitlements\([\s\S]*?\n\$\$;/);
  assert.ok(readFn, 'check_machine_entitlements in migration');
  assert.doesNotMatch(readFn[0], /set revoked\s*=\s*true/);
});

test('a public entitlement read never revokes duplicate keys', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  const res = await rpc('check_machine_entitlements', [MID, null]);
  assert.equal(res.ok, true);
  // Both duplicates remain active: the read is side-effect free.
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('an authorized read returns only the request-linked key and revokes nothing', async () => {
  await db.exec(`insert into public.activation_requests(id, machine_id, status)
    values ('${REQ_ID}'::uuid, '${MID}', 'approved')`);
  await seedKey('LIC-A', { linkedRequest: REQ_ID });
  await seedKey('LIC-B'); // duplicate, not linked to the approved request
  const res = await rpc('check_machine_entitlements', [MID, REQ_ID]);
  assert.equal(res.ok, true);
  assert.deepEqual(res.active_keys.map(k => k.license_id), ['LIC-A']);
  assert.deepEqual(res.revoked_keys, []);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('owner preview lists duplicates without revoking anything', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  const res = await rpc('owner_review_duplicate_machine_keys', [MID, 'preview', '']);
  assert.equal(res.ok, true);
  assert.equal(res.action, 'none');
  assert.equal(res.duplicate_keys.length, 2);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('owner resolve keeps the chosen licence, revokes the others and audits it', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  const res = await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  assert.equal(res.ok, true);
  assert.equal(res.action, 'resolved');
  assert.equal(res.keep_license_id, 'LIC-A');
  assert.deepEqual(res.revoked_license_ids, ['LIC-B']);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', true]]);
  const audit = await db.query(
    "select actor_email, action, keep_license_id, revoked_license_ids from public.owner_license_actions where machine_id = $1", [MID]);
  assert.equal(audit.rows.length, 1);
  assert.equal(audit.rows[0].action, 'resolve_duplicates');
  assert.equal(audit.rows[0].actor_email, OWNER_EMAIL);
  assert.equal(audit.rows[0].keep_license_id, 'LIC-A');
  assert.deepEqual(JSON.parse(audit.rows[0].revoked_license_ids), ['LIC-B']);
});

test('the audit table is append-only: update, delete and truncate are rejected', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  await assert.rejects(
    db.exec("update public.owner_license_actions set action = 'tampered'"), /append-only/);
  await assert.rejects(
    db.exec("delete from public.owner_license_actions"), /append-only/);
  await assert.rejects(
    db.exec("truncate public.owner_license_actions"), /append-only/);
  const audit = await db.query("select count(*) as n from public.owner_license_actions");
  assert.equal(Number(audit.rows[0].n), 1);
});

test('no ambiguous check_machine_entitlements overload exists', async () => {
  // The legacy one-argument overload was dropped by an earlier migration; the
  // consolidated schema must not reintroduce it (single-argument calls would be
  // ambiguous between the two signatures).
  const defs = schema.match(/create (or replace )?function public\.check_machine_entitlements/g) || [];
  assert.equal(defs.length, 1, 'exactly one check_machine_entitlements in schema');
  const live = await db.query(
    "select count(*) as n from pg_proc p join pg_namespace n on n.oid = p.pronamespace " +
    "where n.nspname = 'public' and p.proname = 'check_machine_entitlements'");
  assert.equal(Number(live.rows[0].n), 1, 'exactly one check_machine_entitlements in the database');
  // The surviving signature is the two-argument one the app calls.
  const res = await rpc('check_machine_entitlements', [MID, null]);
  assert.equal(res.ok, true);
});

test('owner resolve without a valid keep selection is refused', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', '']), /p_keep_license_id/);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-ZZZ']), /p_keep_license_id/);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('a non-owner cannot review or resolve duplicate keys', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await db.exec(`set app.jwt_email = 'someone-else@example.com';`);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'preview', '']), /Only the Café Desk owner/);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']), /Only the Café Desk owner/);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('an owner session at AAL1 (without MFA) cannot review or resolve duplicate keys', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await db.exec(`set app.jwt_email = '${OWNER_EMAIL}'; set app.jwt_aal = 'aal1';`);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'preview', '']), /Only the Café Desk owner/);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']), /Only the Café Desk owner/);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', false]]);
});

test('retrying the same keeper is a no-op without a second repair audit', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  const retry = await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  assert.equal(retry.action, 'none');
  assert.equal(retry.keep_license_id, 'LIC-A');
  assert.deepEqual(retry.revoked_license_ids, []);
  assert.equal(Number((await db.query('select count(*) as n from public.owner_license_actions')).rows[0].n), 1);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', true]]);
});

test('a stale conflicting keeper is rejected even when only one active key remains', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-B']), /p_keep_license_id/);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', true]]);
});

test('resolve validates the keeper with zero or one active candidates', async () => {
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-Z']), /p_keep_license_id/);
  await seedKey('LIC-A');
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', '']), /p_keep_license_id/);
  await assert.rejects(rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-Z']), /p_keep_license_id/);
  assert.equal((await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A'])).action, 'none');
});

test('repair does not revoke add-ons, expired keys or alter previously paused keys', async () => {
  for (const lid of ['LIC-A', 'LIC-B', 'LIC-C', 'LIC-D', 'LIC-E']) await seedKey(lid);
  await db.exec("update public.issued_machine_keys set key_type = 'addon' where license_id = 'LIC-C'");
  await db.exec("update public.issued_machine_keys set expires_at = current_date - 1 where license_id = 'LIC-D'");
  await db.exec("update public.issued_machine_keys set revoked = true, revoke_reason = 'Manual pause' where license_id = 'LIC-E'");
  const result = await rpc('owner_review_duplicate_machine_keys', [MID, 'resolve', 'LIC-A']);
  assert.deepEqual(result.revoked_license_ids, ['LIC-B']);
  assert.deepEqual(await keyStates(), [['LIC-A', false], ['LIC-B', true], ['LIC-C', false], ['LIC-D', false], ['LIC-E', true]]);
  assert.equal((await db.query("select revoke_reason from public.issued_machine_keys where license_id = 'LIC-E'")).rows[0].revoke_reason, 'Manual pause');
});

test('machine and keeper input normalization is shared with issuance lock identity', async () => {
  await seedKey('LIC-A');
  await seedKey('LIC-B');
  const result = await rpc('owner_review_duplicate_machine_keys', [`  ${MID.toLowerCase()} `, ' RESOLVE ', ' lic-a ']);
  assert.equal(result.machine_id, MID);
  assert.equal(result.keep_license_id, 'LIC-A');
  assert.deepEqual(result.revoked_license_ids, ['LIC-B']);
});
