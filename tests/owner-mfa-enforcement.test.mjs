// Step 2 security gate: server-enforced MFA (AAL2) for all owner operations
// across PostgreSQL RLS/RPCs, owner-admin & owner-resign Edge Functions, and
// the owner console TOTP enrollment / step-up flow.
import { PGlite } from '@electric-sql/pglite';
import { before, after, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const OWNER_EMAIL = 'owner@example.com';
const OWNER_USER_ID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const OWNER_DIGEST = '72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc';
const MID = 'CD-1234-5678-90AB';

const schema = await readFile(new URL('../supabase_schema.sql', import.meta.url), 'utf8');
const templatesMigration = await readFile(
  new URL('../supabase/migrations/20261119220000_online_templates.sql', import.meta.url),
  'utf8',
);
const mfaMigrationPath = new URL('../supabase/migrations/20261126160000_owner_mfa_aal2_enforcement.sql', import.meta.url);

function makeJwt(payload) {
  const b64u = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  return `${b64u({ alg: 'HS256', typ: 'JWT' })}.${b64u(payload)}.sig`;
}

function extractTable(name) {
  const m = schema.match(new RegExp(`create table if not exists public\\.${name} \\([\\s\\S]*?\\n\\);`));
  assert.ok(m, `table ${name} in supabase_schema.sql`);
  return m[0];
}

function extractFunction(name) {
  const matches = [...schema.matchAll(new RegExp(`create or replace function public\\.${name}\\([\\s\\S]*?\\n\\$\\$;`, 'g'))];
  assert.ok(matches.length > 0, `function ${name} in supabase_schema.sql`);
  return matches.at(-1)[0];
}

let db;

before(async () => {
  assert.ok(fs.existsSync(mfaMigrationPath), 'MFA migration file 20261126160000_owner_mfa_aal2_enforcement.sql must exist');
  const mfaMigration = await readFile(mfaMigrationPath, 'utf8');

  db = new PGlite();
  await db.exec('create role anon; create role authenticated; create role service_role bypassrls;');
  await db.exec('grant usage on schema public to anon, authenticated, service_role;');
  await db.exec(`create schema auth;
    create function auth.jwt() returns jsonb language plpgsql stable as $$
    declare
      v_role_meta text := nullif(current_setting('app.jwt_cafedesk_role', true), '');
    begin
      return jsonb_build_object(
        'sub', coalesce(nullif(current_setting('app.jwt_sub', true), ''), '${OWNER_USER_ID}'),
        'email', coalesce(current_setting('app.jwt_email', true), ''),
        'aal', coalesce(current_setting('app.jwt_aal', true), ''),
        'amr', coalesce(nullif(current_setting('app.jwt_amr', true), ''), '[{"method":"totp"}]')::jsonb,
        'role', coalesce(nullif(current_setting('app.jwt_role', true), ''), 'authenticated'),
        'app_metadata', case
          when v_role_meta is null then '{}'::jsonb
          else jsonb_build_object('cafedesk_role', v_role_meta)
        end
      );
    end;
    $$;
    create function auth.role() returns text language sql stable as $$
      select coalesce(nullif(current_setting('app.jwt_role', true), ''), 'authenticated');
    $$;
    grant usage on schema auth to anon, authenticated, service_role;
    grant execute on all functions in schema auth to anon, authenticated, service_role;`);

  await db.exec(`create or replace function digest(data text, type text) returns bytea language sql immutable as $$
      select case when data = 'CAFE-DESK-OWNER-V1|${OWNER_EMAIL}'
                   then decode('${OWNER_DIGEST}', 'hex')
                   else decode(repeat('00', 32), 'hex') end
    $$;`);

  for (const name of [
    'app_releases',
    'app_announcements',
    'shops',
    'issued_machine_keys',
    'activation_requests',
    'shop_sync_profiles',
    'shop_sync_sales',
    'station_heartbeats',
    'station_online_daily',
    'workspace_tool_visits',
    'machine_rpc_rate_limits',
    'owner_license_actions',
  ]) {
    await db.exec(extractTable(name));
    await db.exec(`alter table public.${name} enable row level security;`);
  }
  await db.exec('alter table public.issued_machine_keys add column if not exists activation_request_id uuid;');

  for (const fn of [
    'cafedesk_owner_digest',
    'is_cafedesk_owner',
    'owner_license_actions_append_only',
    'owner_review_duplicate_machine_keys',
    'owner_register_shop_sync_profile',
    'owner_purge_machine_records',
  ]) {
    await db.exec(extractFunction(fn));
  }
  await db.exec(templatesMigration);

  // Install owner RLS policies from supabase_schema.sql for table-level RLS verification.
  await db.exec(`
    grant select, insert, update, delete on all tables in schema public to authenticated, service_role;
    grant select on public.app_releases, public.app_announcements, public.templates to anon;
    grant usage, select on all sequences in schema public to authenticated, service_role;

    create policy "Owner full access to issued_machine_keys"
      on public.issued_machine_keys for all to authenticated
      using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());
    create policy "Owner write access to app_releases"
      on public.app_releases for all to authenticated
      using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());
    create policy "Owner manage app announcements"
      on public.app_announcements for all to authenticated
      using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());
    create policy "Owner read access to shop_sync_sales"
      on public.shop_sync_sales for select to authenticated
      using (public.is_cafedesk_owner());
  `);

  await db.exec(`create schema storage;
    create table storage.objects (id text primary key, bucket_id text not null);
    alter table storage.objects enable row level security;
    grant usage on schema storage to authenticated;
    grant select, delete on storage.objects to authenticated;
    create policy test_storage_read on storage.objects for select to authenticated using (true);
  `);
  await db.exec(mfaMigration);
  await db.exec(await readFile(new URL('../supabase/migrations/20261126180000_duplicate_repair_lock_order.sql', import.meta.url), 'utf8'));
  await db.exec(`
    grant select on public.owner_audit_log to authenticated, service_role;
    grant execute on function public.cafedesk_owner_digest() to anon, authenticated;
    grant execute on function public.is_cafedesk_owner() to anon, authenticated;
    grant execute on function public.owner_review_duplicate_machine_keys(text, text, text) to authenticated;
    grant execute on function public.owner_register_shop_sync_profile(text, text, text) to authenticated;
    grant execute on function public.owner_purge_machine_records(text) to authenticated;
  `);
});

after(async () => {
  await db?.close();
});

beforeEach(async () => {
  await db.exec('reset role; truncate storage.objects;');
  await db.exec('alter table public.owner_license_actions disable trigger all;');
  await db.exec('alter table public.owner_audit_log disable trigger all;');
  await db.exec(`truncate
    public.issued_machine_keys,
    public.activation_requests,
    public.app_releases,
    public.app_announcements,
    public.shop_sync_profiles,
    public.shop_sync_sales,
    public.templates,
    public.owner_license_actions,
    public.owner_audit_log;`);
  await db.exec('alter table public.owner_license_actions enable trigger all;');
  await db.exec('alter table public.owner_audit_log enable trigger all;');
  await db.exec(`
    set app.jwt_sub = '${OWNER_USER_ID}';
    set app.jwt_email = '${OWNER_EMAIL}';
    set app.jwt_aal = 'aal2';
    set app.jwt_role = 'authenticated';
    set app.jwt_cafedesk_role = '';
    set app.jwt_amr = '[{"method":"totp"}]';
  `);
});

test('SQL is_cafedesk_owner requires verified owner digest AND AAL2 assurance', async () => {
  const check = async () => (await db.query('select public.is_cafedesk_owner() as ok')).rows[0].ok;

  // Owner + aal2 -> true
  assert.equal(await check(), true);

  await db.exec(`set app.jwt_amr = '[{"method":"password"}]';`);
  assert.equal(await check(), false, 'AAL2 with password-only AMR is rejected');
  await db.exec(`set app.jwt_amr = '[{"method":"totp"}]';`);

  // Owner + aal1 -> false (password/OTP alone must never grant owner privilege)
  await db.exec("set app.jwt_aal = 'aal1';");
  assert.equal(await check(), false);

  // Owner + missing aal -> false
  await db.exec("set app.jwt_aal = '';");
  assert.equal(await check(), false);

  // Non-owner + aal2 -> false
  await db.exec("set app.jwt_email = 'intruder@example.com'; set app.jwt_aal = 'aal2';");
  assert.equal(await check(), false);

  // Owner + aal2 but explicit non-owner app_metadata role -> false
  await db.exec(`set app.jwt_email = '${OWNER_EMAIL}'; set app.jwt_aal = 'aal2'; set app.jwt_cafedesk_role = 'viewer';`);
  assert.equal(await check(), false);

  // Owner + aal2 + explicit 'owner' app_metadata role -> true
  await db.exec("set app.jwt_cafedesk_role = 'owner';");
  assert.equal(await check(), true);
});

test('PostgreSQL RLS blocks AAL1 owner reads and writes across sensitive tables, allows AAL2, and audits mutations', async () => {
  // Seed sensitive rows as service_role
  await db.exec(`
    set app.jwt_role = 'service_role';
    insert into public.issued_machine_keys (license_id, machine_id, customer, key_type, token, issued_at, expires_at)
      values ('LIC-MFA-1', '${MID}', 'Durban Café', 'license', 'tok-1', '2026-01-01', '2027-01-01');
    insert into public.shop_sync_sales (shop_id, sale_id, created_at, business, currency, total_cents, items_json)
      values ('SHOP-1', 'S-1', '2026-11-26T10:00:00Z', 'Durban Café', 'R', 2500, '[{"name":"Print","qty":1,"price":"25.00"}]'::jsonb);
  `);

  // Switch to authenticated owner at AAL1
  await db.exec(`set role authenticated; set app.jwt_role = 'authenticated'; set app.jwt_email = '${OWNER_EMAIL}'; set app.jwt_aal = 'aal1';`);

  const keysAal1 = await db.query('select license_id from public.issued_machine_keys');
  assert.equal(keysAal1.rows.length, 0, 'AAL1 owner must not read issued_machine_keys');

  const salesAal1 = await db.query('select sale_id from public.shop_sync_sales');
  assert.equal(salesAal1.rows.length, 0, 'AAL1 owner must not read shop_sync_sales');

  await assert.rejects(
    db.exec("insert into public.app_releases (version, download_url, sha256) values ('0.9.0', 'https://example.com/a.exe', repeat('a', 64))"),
    /row-level security/,
  );
  await assert.rejects(
    db.exec("insert into public.templates (slug, category, name) values ('mfa-test', 'cv', 'MFA Test')"),
    /row-level security/,
  );

  // Upgrade to AAL2 -> reads and writes succeed and trigger append-only owner_audit_log records
  await db.exec("set app.jwt_aal = 'aal2';");
  const keysAal2 = await db.query('select license_id from public.issued_machine_keys');
  assert.equal(keysAal2.rows.length, 1);

  await db.exec("insert into public.app_announcements (title, message, kind, active) values ('Notice', 'Hello', 'info', true)");
  await db.exec("insert into public.templates (slug, category, name, is_active) values ('mfa-cv', 'cv', 'MFA CV', false)");

  const audits = await db.query(
    'select actor_user_id, actor_email, actor_aal, action, target_type from public.owner_audit_log order by id',
  );
  assert.equal(audits.rows.length, 2);
  assert.equal(audits.rows[0].actor_user_id, OWNER_USER_ID);
  assert.equal(audits.rows[0].actor_email, OWNER_EMAIL);
  assert.equal(audits.rows[0].actor_aal, 'aal2');
  assert.deepEqual(audits.rows.map((r) => r.target_type), ['app_announcements', 'templates']);

  // Inactive draft template is visible to AAL2 owner, invisible when downgraded to AAL1
  assert.equal((await db.query("select slug from public.templates where slug = 'mfa-cv'")).rows.length, 1);
  await db.exec("set app.jwt_aal = 'aal1';");
  assert.equal((await db.query("select slug from public.templates where slug = 'mfa-cv'")).rows.length, 0);
  assert.equal((await db.query('select id from public.owner_audit_log')).rows.length, 0, 'AAL1 must not read owner_audit_log');
});

test('PostgreSQL owner SECURITY DEFINER RPCs reject AAL1, succeed at AAL2, and write immutable audit logs', async () => {
  await db.exec(`
    set app.jwt_role = 'service_role';
    insert into public.issued_machine_keys (license_id, machine_id, customer, key_type, token, issued_at, expires_at)
      values ('LIC-1', '${MID}', 'Shop A', 'license', 'tok-1', '2026-01-01', '2027-01-01'),
             ('LIC-2', '${MID}', 'Shop A', 'license', 'tok-2', '2026-01-01', '2027-01-01');
  `);

  await db.exec(`set role authenticated; set app.jwt_role = 'authenticated'; set app.jwt_email = '${OWNER_EMAIL}'; set app.jwt_aal = 'aal1';`);
  await assert.rejects(
    db.query("select public.owner_review_duplicate_machine_keys($1, 'preview', '')", [MID]),
    /MFA \(AAL2\)/,
  );
  await assert.rejects(
    db.query("select public.owner_register_shop_sync_profile('SHOPMFA', repeat('b', 64), 'MFA Café')"),
    /MFA \(AAL2\)/,
  );
  await assert.rejects(
    db.query('select public.owner_purge_machine_records($1)', [MID]),
    /MFA \(AAL2\)/,
  );

  // Upgrade to AAL2 -> all three RPCs succeed and audit records are append-only
  await db.exec("set app.jwt_aal = 'aal2';");
  const preview = (await db.query("select public.owner_review_duplicate_machine_keys($1, 'preview', '') as r", [MID])).rows[0].r;
  assert.equal(preview.ok, true);
  const reg = (await db.query("select public.owner_register_shop_sync_profile('SHOPMFA', repeat('b', 64), 'MFA Café') as r")).rows[0].r;
  assert.equal(reg.ok, true);

  const purge = (await db.query('select public.owner_purge_machine_records($1) as r', [MID])).rows[0].r;
  assert.equal(purge.ok, true);
  assert.equal(purge.deleted.issued_machine_keys, 2);

  const audits = await db.query('select action, target_id, actor_aal from public.owner_audit_log order by id');
  assert.deepEqual(
    audits.rows.map((r) => [r.action, r.target_id, r.actor_aal]),
    [
      ['register_shop_sync_profile', 'SHOPMFA', 'aal2'],
      ['delete_issued_machine_keys', 'LIC-1', 'aal2'],
      ['delete_issued_machine_keys', 'LIC-2', 'aal2'],
      ['purge_machine_records', MID, 'aal2'],
    ],
  );

  // Append-only enforcement on owner_audit_log even as superuser / table owner
  await db.exec('reset role;');
  await assert.rejects(db.exec("update public.owner_audit_log set action = 'forged'"), /append-only/);
  await assert.rejects(db.exec('delete from public.owner_audit_log'), /append-only/);
  await assert.rejects(db.exec('truncate public.owner_audit_log'), /append-only/);
});

test('Edge Function owner MFA gate rejects missing token, AAL1, unverified factors, and plaintext emails', async () => {
  const adminSrc = await readFile(new URL('../supabase/functions/owner-admin/index.ts', import.meta.url), 'utf8');
  const resignSrc = await readFile(new URL('../supabase/functions/owner-resign/index.ts', import.meta.url), 'utf8');
  assert.ok(!adminSrc.toLowerCase().includes('njwayelodlamini'), 'owner-admin must not contain plaintext owner email');
  assert.ok(!resignSrc.toLowerCase().includes('njwayelodlamini'), 'owner-resign must not contain plaintext owner email');
  assert.match(adminSrc, /verifyOwnerMfaRequest/);
  assert.match(resignSrc, /verifyOwnerMfaRequest/);

  const { verifyOwnerMfaRequest } = await import('../supabase/functions/_shared/owner-auth.mjs');

  const realOwnerEmail = 'njwayelodlamini@gmail.com';
  const mockAdmin = (userResult) => ({
    auth: {
      getUser: async () => userResult,
    },
  });

  // 1. Missing Bearer header -> 401
  const rMissing = await verifyOwnerMfaRequest({ authHeader: '', adminClient: mockAdmin({ data: { user: null }, error: null }) });
  assert.equal(rMissing.ok, false);
  assert.equal(rMissing.status, 401);

  // 2. Real owner with enrolled verified TOTP factor, BUT current JWT is only AAL1 -> 403 MFA_REQUIRED
  const aal1Jwt = makeJwt({
    sub: OWNER_USER_ID,
    email: realOwnerEmail,
    role: 'authenticated',
    aal: 'aal1',
    amr: [{ method: 'password', timestamp: 1700000000 }],
  });
  const ownerWithVerifiedFactor = {
    id: OWNER_USER_ID,
    email: realOwnerEmail,
    factors: [{ id: 'f-1', factor_type: 'totp', status: 'verified' }],
    app_metadata: {},
  };
  const rAal1 = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${aal1Jwt}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
  });
  assert.equal(rAal1.ok, false);
  assert.equal(rAal1.status, 403);
  assert.equal(rAal1.code, 'MFA_REQUIRED');

  // 3. JWT claims aal2, but user has NO verified MFA factors -> 403 MFA_REQUIRED
  const aal2Jwt = makeJwt({
    sub: OWNER_USER_ID,
    email: realOwnerEmail,
    role: 'authenticated',
    aal: 'aal2',
    amr: [
      { method: 'password', timestamp: 1700000000 },
      { method: 'totp', timestamp: 1700000010 },
    ],
  });
  const rUnverifiedFactor = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${aal2Jwt}`,
    adminClient: mockAdmin({
      data: {
        user: { ...ownerWithVerifiedFactor, factors: [{ id: 'f-1', factor_type: 'totp', status: 'unverified' }] },
      },
      error: null,
    }),
  });
  assert.equal(rUnverifiedFactor.ok, false);
  assert.equal(rUnverifiedFactor.status, 403);
  assert.equal(rUnverifiedFactor.code, 'MFA_REQUIRED');

  // 4. JWT claims aal2, but AMR contains only password (no MFA method) -> 403 MFA_REQUIRED
  const passwordOnlyAmrJwt = makeJwt({
    sub: OWNER_USER_ID,
    email: realOwnerEmail,
    role: 'authenticated',
    aal: 'aal2',
    amr: [{ method: 'password', timestamp: 1700000000 }],
  });
  const rBadAmr = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${passwordOnlyAmrJwt}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
  });
  assert.equal(rBadAmr.ok, false);
  assert.equal(rBadAmr.status, 403);
  assert.equal(rBadAmr.code, 'MFA_REQUIRED');

  for (const amr of [undefined, null, [], 'totp', [{ method: 'constructor' }]]) {
    const result = await verifyOwnerMfaRequest({
      authHeader: `Bearer ${makeJwt({ sub: OWNER_USER_ID, aal: 'aal2', amr })}`,
      adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
    });
    assert.equal(result.code, 'MFA_REQUIRED', 'missing or invalid AMR fails closed');
  }
  const noFactors = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${aal2Jwt}`,
    adminClient: mockAdmin({ data: { user: { ...ownerWithVerifiedFactor, factors: undefined } }, error: null }),
  });
  assert.equal(noFactors.code, 'MFA_REQUIRED');
  const expired = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${makeJwt({ sub: OWNER_USER_ID, aal: 'aal2', exp: 1, amr: [{ method: 'totp' }] })}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
  });
  assert.equal(expired.status, 401);
  const wrongSub = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${makeJwt({ sub: 'other', aal: 'aal2', amr: [{ method: 'totp' }] })}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
  });
  assert.equal(wrongSub.code, 'INVALID_CLAIMS');

  // 5. Pinned CAFEDESK_OWNER_USER_ID mismatch -> 403 FORBIDDEN_NOT_OWNER
  const rWrongUid = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${aal2Jwt}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
    configuredOwnerUserId: '11111111-2222-4333-8444-555555555555',
  });
  assert.equal(rWrongUid.ok, false);
  assert.equal(rWrongUid.status, 403);
  assert.equal(rWrongUid.code, 'FORBIDDEN_NOT_OWNER');

  // 6. Non-owner with AAL2 + verified TOTP -> 403 FORBIDDEN_NOT_OWNER
  const nonOwnerJwt = makeJwt({
    sub: '99999999-9999-4999-8999-999999999999',
    email: 'other@example.com',
    role: 'authenticated',
    aal: 'aal2',
    amr: [{ method: 'totp', timestamp: 1700000010 }],
  });
  const rNonOwner = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${nonOwnerJwt}`,
    adminClient: mockAdmin({
      data: {
        user: {
          id: '99999999-9999-4999-8999-999999999999',
          email: 'other@example.com',
          factors: [{ id: 'f-9', factor_type: 'totp', status: 'verified' }],
        },
      },
      error: null,
    }),
  });
  assert.equal(rNonOwner.ok, false);
  assert.equal(rNonOwner.status, 403);
  assert.equal(rNonOwner.code, 'FORBIDDEN_NOT_OWNER');

  // 7. Verified owner + AAL2 + verified TOTP factor + matching pinned UUID -> 200 OK
  const rOk = await verifyOwnerMfaRequest({
    authHeader: `Bearer ${aal2Jwt}`,
    adminClient: mockAdmin({ data: { user: ownerWithVerifiedFactor }, error: null }),
    configuredOwnerUserId: OWNER_USER_ID,
  });
  assert.equal(rOk.ok, true);
  assert.equal(rOk.actor.userId, OWNER_USER_ID);
  assert.equal(rOk.actor.aal, 'aal2');
});

test('Owner dashboard gates console behind AAL2 MFA challenge/enrollment and blocks AAL1 API calls', async () => {
  const ownerAuthSrc = await readFile(new URL('../owner-auth.js', import.meta.url), 'utf8');
  const dashHtml = await readFile(new URL('../owner-dashboard.html', import.meta.url), 'utf8');

  const authWin = { crypto: globalThis.crypto, TextEncoder };
  vm.createContext(authWin);
  vm.runInContext(ownerAuthSrc, authWin);

  const aal1Token = makeJwt({ sub: OWNER_USER_ID, aal: 'aal1' });
  const aal2Token = makeJwt({ sub: OWNER_USER_ID, aal: 'aal2', amr: [{ method: 'totp' }] });
  assert.equal(authWin.CafeDeskOwner.parseJwtAal(aal1Token), 'aal1');
  assert.equal(authWin.CafeDeskOwner.parseJwtAal(aal2Token), 'aal2');
  for (const method of ['password', 'constructor', '__proto__', 'toString']) {
    assert.equal(authWin.CafeDeskOwner.parseJwtAal(makeJwt({ aal: 'aal2', amr: [{ method }] })), 'aal1');
  }
  assert.equal(authWin.CafeDeskOwner.parseJwtMfaTimestampMs(makeJwt({ amr: [{ method: 'password', timestamp: 200 }, { method: 'totp', timestamp: 100 }] })), 100000);
  assert.equal(authWin.CafeDeskOwner.hasMfaAssurance({ access_token: aal1Token }, { currentLevel: 'aal1' }), false);
  assert.equal(authWin.CafeDeskOwner.hasMfaAssurance({ access_token: aal1Token }, { currentLevel: 'aal2' }), false);
  assert.equal(authWin.CafeDeskOwner.hasMfaAssurance({ access_token: aal2Token }, { currentLevel: 'aal2' }), true);

  function extractFn(name) {
    const match = new RegExp('(?:async )?function ' + name + '\\(').exec(dashHtml);
    assert.ok(match, `Missing ${name} in owner-dashboard.html`);
    const start = match.index;
    const next = /\n[ \t]*(?:async )?function \w+\(/.exec(dashHtml.slice(start + 1));
    return dashHtml.slice(start, next ? start + 1 + next.index : undefined).replace(/<\/script>[\s\S]*$/, '');
  }

  function makeDomEl() {
    const classes = new Set(['hidden']);
    return {
      textContent: '',
      value: '',
      src: '',
      disabled: false,
      hidden: false,
      style: {},
      classList: {
        add: (...names) => names.forEach((n) => classes.add(n)),
        remove: (...names) => names.forEach((n) => classes.delete(n)),
        toggle: (n, force) => (force === undefined ? (classes.has(n) ? classes.delete(n) : classes.add(n)) : force ? classes.add(n) : classes.delete(n)),
        contains: (n) => classes.has(n),
      },
    };
  }

  const els = new Proxy({}, {
    get(target, prop) {
      if (!(prop in target)) target[prop] = makeDomEl();
      return target[prop];
    },
  });

  let refreshCount = 0;
  let fetchCalls = 0;
  let currentLevel = 'aal1';
  let currentToken = aal1Token;
  let factors = [{ id: 'factor-totp-1', factor_type: 'totp', status: 'verified', friendly_name: 'Phone' }];

  const ctx = {
    $: (sel) => els[sel],
    $$: () => [],
    document: { getElementById: (id) => els['#' + id] },
    window: { CafeDeskOwner: authWin.CafeDeskOwner, location: { href: 'https://example.com/dashboard' }, scrollTo() {} },
    SUPABASE_URL: 'https://example.supabase.co',
    SUPABASE_ANON_KEY: 'anon',
    MFA_STEP_UP_MAX_AGE_MS: 15 * 60 * 1000,
    openMfaModal: async () => {},
    liveTimer: null,
    clearInterval: () => {},
    state: { email: '', mfaVerifiedAt: Date.now(), mfaFactorId: '', mfaEnrollFactorId: '' },
    sb: {
      auth: {
        signOut: async () => {},
        getSession: async () => ({
          data: { session: { access_token: currentToken, user: { id: OWNER_USER_ID, email: 'njwayelodlamini@gmail.com' } } },
        }),
        mfa: {
          getAuthenticatorAssuranceLevel: async () => ({ data: { currentLevel, nextLevel: 'aal2' }, error: null }),
          listFactors: async () => ({ data: { totp: factors, all: factors }, error: null }),
          enroll: async () => ({
            data: { id: 'new-factor-1', totp: { qr_code: 'data:image/svg+xml;utf-8,<svg></svg>', secret: 'JBSWY3DPEHPK3PXP' } },
            error: null,
          }),
        },
      },
    },
    fetch: async () => {
      fetchCalls += 1;
      return { ok: true, json: async () => ({ ok: true }) };
    },
    showAccessDenied: () => {},
    switchView: () => {},
    refreshAll: () => { refreshCount += 1; },
    startLive: () => {},
    toast: () => {},
  };

  vm.createContext(ctx);
  vm.runInContext(
    ['inspectOwnerMfaState', 'showMfaGate', 'startMfaEnrollment', 'ensureOwnerAal2Session', 'ensureFreshOwnerStepUp', 'callOwnerAdmin', 'callOwnerResign', 'applySession']
      .map(extractFn)
      .join('\n'),
    ctx,
  );

  // 1) Owner at AAL1 with verified factor -> #appView stays hidden, #mfaView opens in challenge mode, refreshAll NEVER runs
  await ctx.applySession({ access_token: aal1Token, user: { id: OWNER_USER_ID, email: 'njwayelodlamini@gmail.com' } });
  assert.equal(els['#appView'].classList.contains('hidden'), true);
  assert.equal(els['#mfaView'].classList.contains('hidden'), false);
  assert.equal(els['#mfaChallengeSection'].classList.contains('hidden'), false);
  assert.equal(refreshCount, 0);

  // 2) Calling callOwnerAdmin or callOwnerResign while at AAL1 refuses before network fetch
  await assert.rejects(ctx.callOwnerAdmin({ action: 'set_revoked', license_id: 'ABC', revoked: true }), /MFA|6-digit/i);
  await assert.rejects(ctx.callOwnerResign({ license_id: 'ABC' }), /MFA|6-digit/i);
  assert.equal(fetchCalls, 0, 'No Edge Function network request may fire at AAL1');

  // 3) Owner at AAL1 with NO verified factors -> opens enrollment mode and populates QR image src safely
  factors = [];
  await ctx.applySession({ access_token: aal1Token, user: { id: OWNER_USER_ID, email: 'njwayelodlamini@gmail.com' } });
  assert.equal(els['#appView'].classList.contains('hidden'), true);
  assert.equal(els['#mfaEnrollSection'].classList.contains('hidden'), false);
  assert.match(els['#mfaQrImg'].src, /^data:image\/svg\+xml/);
  assert.equal(els['#mfaSecretCode'].textContent, 'JBSWY3DPEHPK3PXP');
  assert.equal(refreshCount, 0);

  // 4) Owner upgraded to AAL2 -> #mfaView hides, #appView opens, refreshAll runs, and callOwnerAdmin succeeds
  factors = [{ id: 'factor-totp-1', factor_type: 'totp', status: 'verified' }];
  currentLevel = 'aal2';
  currentToken = aal2Token;
  await ctx.applySession({ access_token: aal2Token, user: { id: OWNER_USER_ID, email: 'njwayelodlamini@gmail.com' } });
  assert.equal(els['#mfaView'].classList.contains('hidden'), true);
  assert.equal(els['#appView'].classList.contains('hidden'), false);
  assert.equal(refreshCount, 1);

  await ctx.callOwnerAdmin({ action: 'set_revoked', license_id: 'ABC', revoked: true });
  assert.equal(fetchCalls, 1);

  // Reloading/applying an old session must not fabricate a fresh verification time.
  for (const verifiedAt of [Date.now() - 16 * 60 * 1000, 0]) {
    ctx.state.mfaVerifiedAt = verifiedAt;
    await ctx.applySession({ access_token: aal2Token, user: { id: OWNER_USER_ID, email: 'njwayelodlamini@gmail.com' } });
    assert.equal(ctx.state.mfaVerifiedAt, verifiedAt);
    await assert.rejects(ctx.callOwnerResign({ license_id: 'ABC' }), /fresh 6-digit MFA/);
    assert.equal(fetchCalls, 1, 'Stale or unknown verification time cannot reach resign');
  }
  ctx.state.mfaVerifiedAt = Date.now();
  await ctx.callOwnerResign({ license_id: 'ABC' });
  assert.equal(fetchCalls, 2);
  await ctx.applySession(null);
  assert.equal(ctx.state.mfaVerifiedAt, 0);
  assert.equal(els['#mfaSecretCode'].textContent, '');
  assert.equal(els['#mfaQrImg'].src, '');
});


test('Edge audit helper never silently accepts missing schema or failed writes', async () => {
  const { recordOwnerAudit } = await import('../supabase/functions/_shared/owner-auth.mjs');
  const actor = { userId: OWNER_USER_ID, email: OWNER_EMAIL, aal: 'aal2' };
  const rows = [];
  await recordOwnerAudit({ from: (table) => ({ insert: async (row) => {
    assert.equal(table, 'owner_audit_log');
    rows.push(row);
    return { error: null };
  } }) }, actor, { action: 'set_revoked', targetType: 'issued_machine_keys', targetId: 'LIC-1' });
  assert.equal(rows[0].actor_aal, 'aal2');
  assert.equal(rows[0].target_id, 'LIC-1');
  for (const message of ['owner_audit_log does not exist', 'permission denied']) {
    await assert.rejects(recordOwnerAudit({ from: () => ({ insert: async () => ({ error: { message } }) }) }, actor, {}), /Audit log write failed/);
  }
  await assert.rejects(recordOwnerAudit(null, actor, {}), /required/);
});


test('Release storage deletion requires owner AAL2 and is restricted to release bucket', async () => {
  await db.exec("insert into storage.objects values ('release', 'cafedesk-releases'), ('other', 'private'); set role authenticated; set app.jwt_aal = 'aal1';");
  assert.equal((await db.query('delete from storage.objects returning id')).rows.length, 0);
  await db.exec("set app.jwt_aal = 'aal2'; set app.jwt_email = 'other@example.com';");
  assert.equal((await db.query('delete from storage.objects returning id')).rows.length, 0);
  await db.exec(`set app.jwt_email = '${OWNER_EMAIL}';`);
  assert.deepEqual((await db.query('delete from storage.objects returning id')).rows, [{ id: 'release' }]);
});

test('Audit table allows service inserts, not browser forgeries or service mutations', async () => {
  await db.exec('set role authenticated;');
  await assert.rejects(db.exec("insert into public.owner_audit_log(action) values ('forged')"), /permission denied|row-level security/);
  await db.exec('reset role; set role service_role;');
  await db.exec("insert into public.owner_audit_log(action) values ('service_action')");
  assert.equal((await db.query('select action from public.owner_audit_log')).rows[0].action, 'service_action');
  await assert.rejects(db.exec("update public.owner_audit_log set action = 'forged'"), /permission denied|append-only/);
});
