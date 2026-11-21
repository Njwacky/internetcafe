// Real PostgreSQL execution via PGlite, never a live Supabase project.
import { PGlite } from '@electric-sql/pglite';
import { before, after, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let db;
const migration = await readFile(new URL('../supabase/migrations/20261121150000_complete_sales_sync.sql', import.meta.url), 'utf8');
const schema = await readFile(new URL('../supabase_schema.sql', import.meta.url), 'utf8');
const stamp = '2026-01-01T10:00:00+02:00';
const sale = (id = 'one', changes = {}) => ({sale_id:id,created_at:stamp,station:'Counter',business:'Shop',currency:'R',total_cents:100,items:[{name:'Service',qty:1,price:'1.00'}],voided_at:null,void_reason:null,...changes});
async function rpc(rows = [], after = '', read = true, shop = 'SHOP', pass = 'correct-hash') {
  const result = await db.query('select public.sync_shop_sales_v2($1,$2,$3::jsonb,$4,$5) as result', [shop,pass,JSON.stringify(rows),after,read]);
  return result.rows[0].result;
}
before(async () => {
  db = new PGlite();
  await db.exec('create role anon; create role authenticated;');
  for (const name of ['shop_sync_profiles', 'shop_sync_sales']) {
    const create = schema.match(new RegExp(`create table if not exists public\\.${name} \\([\\s\\S]*?\\n\\);`));
    assert.ok(create, name);
    await db.exec(create[0]);
    await db.exec(`alter table public.${name} enable row level security;`);
  }
  await db.exec(migration);
});
after(async () => { await db?.close(); });
beforeEach(async () => {
  await db.exec('reset role; truncate public.shop_sync_sales,public.shop_sync_profiles;');
  await db.exec("insert into public.shop_sync_profiles(shop_id,sync_key_sha256) values ('SHOP','correct-hash'),('OTHER','other-hash');");
});

test('consolidated schema contains the identical migration', () => {
  assert.ok(schema.endsWith(migration));
});

test('all rows beyond the old 2,000 limit are reachable by stable keyset pages', async () => {
  await db.exec(`insert into public.shop_sync_sales(shop_id,sale_id,created_at,business,total_cents,items_json)
    select 'SHOP','sale-'||lpad(i::text,5,'0'),'${stamp}','Shop',100,'[{"name":"Service","qty":1,"price":"1.00"}]'::jsonb from generate_series(1,2503) i;`);
  let cursor = '', ids = [];
  for (;;) {
    const page = await rpc([], cursor);
    assert.equal(page.protocol, 2);
    assert.ok(page.sales.length <= 250);
    if (!page.sales.length) break;
    const next = page.sales.map(x => x.sale_id);
    assert.deepEqual(next, [...next].sort());
    assert.ok(next[0] > cursor);
    cursor = page.next_after;
    assert.equal(cursor, next.at(-1));
    ids.push(...next);
  }
  assert.equal(ids.length, 2503);
  assert.equal(new Set(ids).size, 2503);
});

test('old void updates propagate without creation-date filtering and cannot be revived', async () => {
  await rpc([sale('old')]);
  await rpc([sale('new',{created_at:'2026-11-21T10:00:00+02:00'})]);
  await rpc([sale('old',{voided_at:'2026-11-21T11:00:00+02:00',void_reason:'Cancelled'})]);
  await rpc([sale('old')]);
  const rows = (await rpc()).sales;
  assert.equal(rows.find(r => r.sale_id === 'old').void_reason, 'Cancelled');
  assert.equal(rows.find(r => r.sale_id === 'old').voided_at, '2026-11-21T11:00:00+02:00');
});

test('immutable conflict cannot attach a void and rolls back the whole upload batch', async () => {
  await rpc([sale()]);
  await assert.rejects(rpc([sale('new'),sale('one',{currency:'USD',voided_at:stamp})]), /conflicts with immutable/);
  const rows = (await rpc()).sales;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].voided_at, null);
  assert.equal(rows[0].currency, 'R');
});

test('untrusted payloads, bad totals and oversized batches are rejected', async () => {
  for (const bad of [sale('bad',{total_cents:101}),sale('bad',{items:[{name:'Service',qty:1.9,price:'1.00'}]}),sale('bad',{items:[{name:'Service',qty:true,price:'1.00'}]}),sale('bad',{items:[{name:'Service',qty:1,price:'NaN'}]}),sale('bad',{voided_at:'not-a-date'}),sale('bad',{sale_id:'x'.repeat(65)}),sale('bad',{items:[]})]) {
    await assert.rejects(rpc([bad]));
  }
  await assert.rejects(rpc(Array.from({length:251},(_,i) => sale(String(i)))), /at most 250/);
  assert.equal((await rpc()).sales.length, 0);
});

test('wrong shop secret does not disclose or mutate sales; public tables stay private', async () => {
  await rpc([sale()]);
  await db.exec('set role anon');
  const denied = await rpc([sale('forged')], '', true, 'SHOP', 'wrong');
  assert.equal(denied.ok, false);
  assert.equal(denied.sales, undefined);
  assert.equal((await rpc([], '', true, 'OTHER', 'other-hash')).sales.length, 0);
  await assert.rejects(db.query('select * from public.shop_sync_sales'), /permission denied/);
  assert.equal((await rpc()).sales.length, 1);
  await db.exec('reset role');
});

test('legacy RPC reports upgrade required rather than pretending a truncated sync succeeded', async () => {
  const {rows} = await db.query("select public.sync_shop_sales('SHOP','correct-hash') as result");
  assert.equal(rows[0].result.ok, false);
  assert.match(rows[0].result.error, /Upgrade/);
});
