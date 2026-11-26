"""Real multi-connection PostgreSQL tests; PGlite cannot test lock contention.

Set CAFEDESK_TEST_POSTGRES_DSN to a LOCAL disposable PostgreSQL server (CREATEDB
required). Each run creates/drops its own random database, never an existing DB.
CI supplies a PostgreSQL service and runs these tests as a mandatory gate.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import time
import unittest
import uuid

DSN = os.environ.get('CAFEDESK_TEST_POSTGRES_DSN', '')
ROOT = Path(__file__).resolve().parents[1]
MID = 'CD-1234-5678-90AB'
OTHER_MID = 'CD-AAAA-BBBB-CCCC'
OWNER = 'owner@example.com'
MIGRATION = ROOT / 'supabase/migrations/20261126180000_duplicate_repair_lock_order.sql'


class DuplicateRepairMigrationTests(unittest.TestCase):
    def test_latest_schema_matches_migration_and_locks_before_candidate_read(self):
        migration = MIGRATION.read_text(encoding='utf-8')
        schema = (ROOT / 'supabase_schema.sql').read_text(encoding='utf-8')
        self.assertIn(migration.strip(), schema)
        functions = re.findall(
            r'create or replace function public\.owner_review_duplicate_machine_keys\([\s\S]*?\n\$\$;', schema)
        self.assertIn(functions[-1], migration)
        self.assertLess(migration.index('pg_advisory_xact_lock(hashtextextended(v_mid, 0))'),
                        migration.index('into v_keys'))
        self.assertIn('for update nowait', migration)
        self.assertNotIn("hashtext('cafedesk-dup:'", migration)


@unittest.skipUnless(DSN, 'set CAFEDESK_TEST_POSTGRES_DSN for real PostgreSQL concurrency tests')
class DuplicateRepairConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import errors are failures when the test was explicitly requested.
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        cls.pg = psycopg
        options = conninfo_to_dict(DSN)
        host = options.get('host', '')
        if not (host in ('localhost', '127.0.0.1', '::1') or host.startswith('/')):
            raise ValueError('Tests require an explicit loopback or Unix-socket PostgreSQL host')
        if options.get('hostaddr', '') not in ('', '127.0.0.1', '::1'):
            raise ValueError('Non-local hostaddr is not allowed')
        cls.database = 'cafedesk_lock_test_' + uuid.uuid4().hex
        cls.admin = psycopg.connect(DSN, autocommit=True, connect_timeout=5)
        cls.addClassCleanup(cls.admin.close)
        cls.admin.execute(sql.SQL('create database {}').format(sql.Identifier(cls.database)))
        cls.addClassCleanup(lambda: cls.admin.execute(
            sql.SQL('drop database {} with (force)').format(sql.Identifier(cls.database))))
        cls.dsn = make_conninfo(DSN, dbname=cls.database)
        with psycopg.connect(cls.dsn, autocommit=True) as db:
            for role in ('anon', 'authenticated', 'service_role'):
                if not db.execute('select 1 from pg_roles where rolname = %s', (role,)).fetchone():
                    db.execute(sql.SQL('create role {}').format(sql.Identifier(role)))
            db.execute('''
                create schema auth;
                create function auth.jwt() returns jsonb language sql stable as $$
                    select current_setting('request.jwt.claims', true)::jsonb;
                $$;
                create function auth.role() returns text language sql stable as $$
                    select auth.jwt()->>'role';
                $$;
                create schema storage;
                create table storage.buckets(id text primary key, name text, public boolean);
                create table storage.objects(id uuid primary key, bucket_id text);
            ''')
            # Exercise actual production schema, issuance trigger, signature RPC,
            # audit triggers and subsequent migrations, not simplified lock mocks.
            schema = (ROOT / 'supabase_schema.sql').read_text(encoding='utf-8')
            if not db.execute("select 1 from pg_available_extensions where name = 'pgcrypto'").fetchone():
                # The optional local pgserver wheel omits extensions. Use native
                # PostgreSQL SHA-256 for the digest's sole supported algorithm;
                # CI's stock PostgreSQL service uses the actual pgcrypto extension.
                schema = schema.replace('create extension if not exists pgcrypto;', '')
                db.execute('''create function public.digest(text, text) returns bytea
                    language sql immutable as $$ select case when $2 = 'sha256'
                    then sha256(convert_to($1, 'UTF8')) else null end; $$;''')
            db.execute(schema)
            db.execute((ROOT / 'supabase/migrations/20261119220000_online_templates.sql').read_text(encoding='utf-8'))
            db.execute((ROOT / 'supabase/migrations/20261126160000_owner_mfa_aal2_enforcement.sql').read_text(encoding='utf-8'))
            db.execute(MIGRATION.read_text(encoding='utf-8'))
            # Only the owner digest is adapted to the synthetic test account.
            db.execute('''create or replace function public.cafedesk_owner_digest()
                returns text language sql immutable as $$
                select encode(sha256(convert_to('CAFE-DESK-OWNER-V1|owner@example.com', 'UTF8')), 'hex');
                $$;''')
            db.execute('grant usage on schema auth, public to authenticated;')
            db.execute('grant select, insert, update, delete on public.issued_machine_keys to authenticated;')
            db.execute('grant select on public.owner_license_actions to authenticated;')

    def setUp(self):
        self.db = self.connect()
        self.addCleanup(self.db.close)
        self.db.execute('''
            alter table public.owner_license_actions disable trigger all;
            alter table public.owner_audit_log disable trigger all;
            truncate public.issued_machine_keys, public.owner_license_actions, public.owner_audit_log;
            alter table public.owner_license_actions enable trigger all;
            alter table public.owner_audit_log enable trigger all;
        ''')
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        # Close the holding connection before joining any waiting worker on failure.
        self.addCleanup(self.pool.shutdown, wait=True, cancel_futures=True)
        self.addCleanup(self.db.close)

    def connect(self, owner=False):
        conn = self.pg.connect(self.dsn, autocommit=True, connect_timeout=5)
        conn.execute("set statement_timeout = '8s'; set lock_timeout = '6s';")
        conn.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({
            'sub': 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', 'email': OWNER,
            'role': 'authenticated' if owner else 'service_role',
            'aal': 'aal2', 'amr': [{'method': 'totp'}],
        }),))
        if owner:
            conn.execute('set role authenticated;')
        return conn

    def seed(self, *licenses, mid=MID):
        # Administrative import simulates legacy duplicates that the ordinary
        # production issuance guard correctly refuses to create today.
        self.db.execute('alter table public.issued_machine_keys disable trigger guard_duplicate_base_license;')
        try:
            for lid in licenses:
                self.db.execute('''insert into public.issued_machine_keys
                    (license_id, machine_id, customer, key_type, token, issued_at, expires_at)
                    values (%s, %s, 'Test Cafe', 'license', 'test-token', current_date, current_date + 365)''', (lid, mid))
        finally:
            self.db.execute('alter table public.issued_machine_keys enable trigger guard_duplicate_base_license;')

    def repair(self, conn, keep='LIC-A', action='resolve', mid=MID):
        return conn.execute('select public.owner_review_duplicate_machine_keys(%s, %s, %s)',
                            (mid, action, keep)).fetchone()[0]

    def start_query(self, query):
        pid = concurrent.futures.Future()

        def run():
            with self.connect(owner=True) as conn:
                pid.set_result(conn.info.backend_pid)
                return query(conn)

        result = self.pool.submit(run)
        return result, pid.result(timeout=5)

    def assert_blocked(self, result, pid, blocker, locktype='advisory'):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if result.done():
                self.fail(f'Request completed instead of waiting for {locktype}: {result.result()}')
            row = self.db.execute('''select %s = any(pg_blocking_pids(%s)), exists (
                select 1 from pg_locks where pid = %s and not granted and locktype = %s
            )''', (blocker, pid, pid, locktype)).fetchone()
            if row == (True, True):
                return
            time.sleep(0.01)
        self.fail(f'Backend {pid} never waited on {locktype} held by {blocker}')

    def states(self):
        return self.db.execute('select license_id, revoked from public.issued_machine_keys where machine_id = %s order by license_id', (MID,)).fetchall()

    def audit_count(self):
        return self.db.execute("select count(*) from public.owner_audit_log where action = 'resolve_duplicate_machine_keys'").fetchone()[0]

    def hold_machine(self):
        self.db.execute('begin;')
        self.db.execute('select pg_advisory_xact_lock(hashtextextended(%s, 0))', (MID,))

    def test_repair_waits_on_issuance_lock_before_reading_or_early_return(self):
        self.seed('LIC-A')
        self.hold_machine()
        result, pid = self.start_query(lambda c: self.repair(c))
        self.assert_blocked(result, pid, self.db.info.backend_pid)
        # A serializable result must be computed after the holder commits.
        self.db.execute("update public.issued_machine_keys set revoked = true where license_id = 'LIC-A'")
        self.db.execute('commit;')
        with self.assertRaisesRegex(self.pg.errors.InvalidParameterValue, 'p_keep_license_id'):
            result.result(timeout=5)
        self.assertEqual(self.audit_count(), 0)

    def test_concurrent_same_keeper_is_serialized_and_retry_is_noop(self):
        self.seed('LIC-A', 'LIC-B')
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.assertEqual(self.repair(first)['action'], 'resolved')
            result, pid = self.start_query(lambda c: self.repair(c))
            self.assert_blocked(result, pid, first.info.backend_pid)
            first.execute('commit;')
        self.assertEqual(result.result(timeout=5)['action'], 'none')
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', True)])
        self.assertEqual(self.audit_count(), 1)

    def test_concurrent_conflicting_keeper_is_rejected_after_wait(self):
        self.seed('LIC-A', 'LIC-B')
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.repair(first)
            result, pid = self.start_query(lambda c: self.repair(c, 'LIC-B'))
            self.assert_blocked(result, pid, first.info.backend_pid)
            first.execute('commit;')
        with self.assertRaisesRegex(self.pg.errors.InvalidParameterValue, 'p_keep_license_id'):
            result.result(timeout=5)
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', True)])
        self.assertEqual(self.audit_count(), 1)

    def test_rolled_back_repair_releases_lock_and_waiter_revalidates(self):
        self.seed('LIC-A', 'LIC-B')
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.repair(first)
            result, pid = self.start_query(lambda c: self.repair(c, 'LIC-B'))
            self.assert_blocked(result, pid, first.info.backend_pid)
            first.execute('rollback;')
        self.assertEqual(result.result(timeout=5)['revoked_license_ids'], ['LIC-A'])
        self.assertEqual(self.states(), [('LIC-A', True), ('LIC-B', False)])
        self.assertEqual(self.audit_count(), 1)
        audit = self.db.execute("""select detail from public.owner_audit_log
            where action = 'resolve_duplicate_machine_keys'""").fetchone()[0]
        self.assertEqual(audit['keep_license_id'], 'LIC-B')
        self.assertEqual(audit['revoked_license_ids'], ['LIC-A'])

    def test_repeatable_read_stale_snapshot_aborts_instead_of_using_old_keeper(self):
        self.seed('LIC-A', 'LIC-B')
        self.hold_machine()
        self.db.execute("update public.issued_machine_keys set revoked = true where license_id = 'LIC-A'")

        def repeatable(conn):
            conn.execute('begin isolation level repeatable read;')
            conn.execute('select count(*) from public.issued_machine_keys').fetchone()
            return self.repair(conn)

        result, pid = self.start_query(repeatable)
        self.assert_blocked(result, pid, self.db.info.backend_pid)
        self.db.execute('commit;')
        with self.assertRaises(self.pg.errors.SerializationFailure):
            result.result(timeout=5)
        self.assertEqual(self.states(), [('LIC-A', True), ('LIC-B', False)])
        self.assertEqual(self.audit_count(), 0)

    def test_new_issuance_waits_for_repair_and_cannot_reintroduce_overlap(self):
        self.seed('LIC-A', 'LIC-B')
        self.seed('LIC-C', 'LIC-D', mid=OTHER_MID)
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.repair(first)
            result, pid = self.start_query(lambda c: c.execute('''insert into public.issued_machine_keys
                (license_id, machine_id, customer, key_type, token, issued_at, expires_at)
                values ('LIC-NEW', %s, 'Test Cafe', 'license', 'token', current_date, current_date+365)''', (MID,)))
            self.assert_blocked(result, pid, first.info.backend_pid)
            # Other machines must not be serialized behind this repair.
            other, _ = self.start_query(lambda c: self.repair(c, 'LIC-C', 'resolve', OTHER_MID))
            self.assertEqual(other.result(timeout=3)['revoked_license_ids'], ['LIC-D'])
            first.execute('commit;')
        with self.assertRaisesRegex(self.pg.errors.RaiseException, 'unexpired base key'):
            result.result(timeout=5)
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', True)])

    def test_resign_uses_same_machine_lock_as_repair(self):
        self.seed('LIC-A', 'LIC-B')
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.repair(first)

            # Publish the PID of the connection that actually calls the service RPC.
            pid_ready = concurrent.futures.Future()
            def call_resign():
                with self.connect() as service:
                    pid_ready.set_result(service.info.backend_pid)
                    return service.execute('''select public.replace_license_signature(
                        'LIC-A', repeat('0',64), 'new-token', %s, 'Test Cafe', current_date,
                        current_date+365, 'license', '', null, null, '', '[]'::jsonb)''', (MID,)).fetchone()[0]
            result = self.pool.submit(call_resign)
            self.assert_blocked(result, pid_ready.result(timeout=5), first.info.backend_pid)
            first.execute('commit;')
        # Repair does not delete paused duplicates; existing re-sign guard remains fail-closed.
        self.assertFalse(result.result(timeout=5))

    def test_keeper_paused_while_waiting_is_not_used_to_revoke_remaining_key(self):
        self.seed('LIC-A', 'LIC-B')
        self.hold_machine()
        self.db.execute("update public.issued_machine_keys set revoked = true where license_id = 'LIC-A'")
        result, pid = self.start_query(lambda c: self.repair(c))
        self.assert_blocked(result, pid, self.db.info.backend_pid)
        self.db.execute('commit;')
        with self.assertRaisesRegex(self.pg.errors.InvalidParameterValue, 'p_keep_license_id'):
            result.result(timeout=5)
        self.assertEqual(self.states(), [('LIC-A', True), ('LIC-B', False)])
        self.assertEqual(self.audit_count(), 0)

    def test_busy_keeper_row_fails_closed_without_waiting_or_partial_repair(self):
        self.seed('LIC-A', 'LIC-B')
        self.db.execute('begin;')
        self.db.execute("select 1 from public.issued_machine_keys where license_id = 'LIC-A' for update")
        result, _ = self.start_query(lambda c: self.repair(c))
        with self.assertRaises(self.pg.errors.LockNotAvailable):
            result.result(timeout=3)
        self.db.execute('rollback;')
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', False)])
        self.assertEqual(self.audit_count(), 0)
        # The failed transaction must release its machine lock for a safe retry.
        with self.connect(owner=True) as owner:
            self.assertEqual(self.repair(owner)['action'], 'resolved')

    def test_row_then_trigger_lock_inversion_aborts_repair_not_deadlocks(self):
        self.seed('LIC-A', 'LIC-B')
        self.hold_machine()
        # This production BEFORE trigger asks for the machine lock *after* its
        # UPDATE has acquired a row lock. Repair must not wait on that row.
        result, pid = self.start_query(lambda c: c.execute(
            "update public.issued_machine_keys set expires_at = expires_at + 1 where license_id = 'LIC-A'"))
        self.assert_blocked(result, pid, self.db.info.backend_pid)
        with self.assertRaises(self.pg.errors.LockNotAvailable):
            self.repair(self.db)
        self.db.execute('rollback;')
        with self.assertRaisesRegex(self.pg.errors.RaiseException, 'unexpired base key'):
            result.result(timeout=5)
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', False)])
        self.assertEqual(self.audit_count(), 0)

    def test_keeper_row_stays_locked_until_repair_transaction_ends(self):
        self.seed('LIC-A', 'LIC-B')
        with self.connect(owner=True) as first:
            first.execute('begin;')
            self.repair(first)
            result, pid = self.start_query(lambda c: c.execute(
                "update public.issued_machine_keys set revoked = true where license_id = 'LIC-A'"))
            self.assert_blocked(result, pid, first.info.backend_pid, 'transactionid')
            first.execute('commit;')
        result.result(timeout=5)
        # A deliberate later pause is allowed; it did not change the repair's keeper mid-flight.
        self.assertEqual(self.audit_count(), 1)

    def test_preview_is_read_only_and_does_not_take_machine_lock(self):
        self.seed('LIC-A', 'LIC-B')
        self.hold_machine()
        result, _ = self.start_query(lambda c: self.repair(c, '', 'preview'))
        self.assertEqual(len(result.result(timeout=3)['duplicate_keys']), 2)
        self.db.execute('rollback;')
        self.assertEqual(self.audit_count(), 0)
        self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', False)])

    def test_ambiguous_normalized_keeper_is_rejected(self):
        self.seed('LIC-A', 'lic-a', 'LIC-B')
        with self.connect(owner=True) as owner:
            with self.assertRaisesRegex(self.pg.errors.InvalidParameterValue, 'p_keep_license_id'):
                self.repair(owner)
        self.assertEqual([revoked for _, revoked in self.states()], [False, False, False])
        self.assertEqual(self.audit_count(), 0)

    def test_failed_audit_rolls_back_repair_and_all_audit_rows(self):
        self.seed('LIC-A', 'LIC-B')
        self.db.execute('''create function public.fail_test_audit() returns trigger language plpgsql as $$
            begin raise exception 'test audit unavailable'; end; $$;
            create trigger fail_test_audit before insert on public.owner_audit_log
            for each row execute function public.fail_test_audit();''')
        try:
            with self.connect(owner=True) as owner:
                with self.assertRaisesRegex(self.pg.errors.RaiseException, 'test audit unavailable'):
                    self.repair(owner)
            self.assertEqual(self.states(), [('LIC-A', False), ('LIC-B', False)])
            self.assertEqual(self.audit_count(), 0)
            self.assertEqual(self.db.execute('select count(*) from public.owner_license_actions').fetchone()[0], 0)
        finally:
            self.db.execute('drop trigger fail_test_audit on public.owner_audit_log; drop function public.fail_test_audit();')


if __name__ == '__main__':
    unittest.main()
