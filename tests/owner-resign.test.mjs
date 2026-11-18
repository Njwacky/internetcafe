import { webcrypto } from 'node:crypto';
import { test } from 'node:test';
import assert from 'node:assert/strict';
if (!globalThis.crypto) Object.defineProperty(globalThis, 'crypto', {value: webcrypto});
const { resignLicense } = await import('../supabase/functions/owner-resign/signing.mjs');
const b64 = (bytes) => Buffer.from(bytes).toString('base64url');
async function fixture() {
  const old = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  const next = await crypto.subtle.generateKey('Ed25519', true, ['sign','verify']);
  const payload = {v:1,mid:'CD-1234-5678-90AB',lid:'ABC123',cust:'Test Shop',iat:'2026-01-01',exp:'2027-01-01'};
  const input = `CDLIC1.${b64(Buffer.from(JSON.stringify(payload)))}`;
  const signature = await crypto.subtle.sign('Ed25519', old.privateKey, Buffer.from(input));
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', next.privateKey));
  return { row: {license_id:payload.lid,machine_id:payload.mid,customer:payload.cust,issued_at:payload.iat,expires_at:payload.exp,key_type:'license',features:[],revoked:false,token:`${input}.${b64(signature)}`},
    seed: b64(pkcs8.slice(-32)), oldPublic: b64(await crypto.subtle.exportKey('raw',old.publicKey)), newPublic: b64(await crypto.subtle.exportKey('raw',next.publicKey)) };
}
test('replacement preserves exact signed payload and verifies with the expected key', async () => {
  const x = await fixture();
  const replacement = await resignLicense(x.row,x.seed,x.oldPublic,x.newPublic,'2026-05-01');
  assert.equal(replacement.split('.')[1],x.row.token.split('.')[1]);
  assert.notEqual(replacement,x.row.token);
  const [prefix,payload,sig]=replacement.split('.');
  const pk=await crypto.subtle.importKey('raw',Buffer.from(x.newPublic,'base64url'),'Ed25519',false,['verify']);
  assert.equal(await crypto.subtle.verify('Ed25519',pk,Buffer.from(sig,'base64url'),Buffer.from(`${prefix}.${payload}`)),true);
});
test('wrong old or expected public key is refused', async () => {
  const x = await fixture();
  await assert.rejects(resignLicense(x.row,x.seed,x.newPublic,x.newPublic,'2026-05-01'),/trusted old public key/);
  await assert.rejects(resignLicense(x.row,x.seed,x.oldPublic,x.oldPublic,'2026-05-01'),/does not match the expected public key/);
});
test('paused, expired, tampered, or inconsistent records are refused', async () => {
  const x = await fixture();
  const run=(row,day='2026-05-01') => resignLicense(row,x.seed,x.oldPublic,x.newPublic,day);
  await assert.rejects(run({...x.row,revoked:true}),/unexpired, unpaused/);
  await assert.rejects(run(x.row,'2028-01-01'),/unexpired, unpaused/);
  await assert.rejects(run({...x.row,machine_id:'CD-FFFF-FFFF-FFFF'}),/does not match its database record/);
  await assert.rejects(run({...x.row,features:['pdf_unlock']}),/does not match its database record/);
  await assert.rejects(run({...x.row,token:x.row.token.slice(0,-3)+'AAA'}),/trusted old public key/);
});

test('database repair compares the old token digest and is not executable by public roles', async () => {
  const { readFile } = await import('node:fs/promises');
  const sql = await readFile(new URL('../supabase/migrations/20261117193000_atomic_license_repair.sql', import.meta.url), 'utf8');
  assert.match(sql, /pg_advisory_xact_lock/);
  assert.match(sql, /sha256\(convert_to\(token, 'UTF8'\)\)/);
  assert.match(sql, /not revoked and expires_at >= current_date/);
  assert.match(sql, /revoke all on function[\s\S]*from public, anon, authenticated/i);
  assert.match(sql, /grant execute on function[\s\S]*to service_role/i);
});
