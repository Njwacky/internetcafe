import { createClient } from 'npm:@supabase/supabase-js@2';
import { recordOwnerAudit, verifyOwnerMfaRequest } from '../_shared/owner-auth.mjs';
import { resignLicense } from './signing.mjs';

// A separate repair endpoint; requires verified owner identity AND server-enforced MFA (AAL2).
const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type', 'Access-Control-Allow-Methods': 'POST, OPTIONS' };
function reply(status: number, value: Record<string, unknown>) {
  return new Response(JSON.stringify(value), { status, headers: { ...cors, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
}
Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: cors });
  if (req.method !== 'POST') return reply(405, { ok: false, error: 'POST required.' });
  try {
    const ownerEmail = (Deno.env.get('CAFEDESK_OWNER_EMAIL') || '').trim();
    const ownerUserId = (Deno.env.get('CAFEDESK_OWNER_USER_ID') || '').trim();
    const admin = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!);
    const gate = await verifyOwnerMfaRequest({
      authHeader: req.headers.get('Authorization') || '',
      adminClient: admin,
      configuredOwnerEmail: ownerEmail,
      configuredOwnerUserId: ownerUserId,
    });
    if (!gate.ok) {
      return reply(gate.status, { ok: false, code: gate.code, error: gate.error });
    }
    const body = await req.json();
    const id = String(body.license_id || '').trim();
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(id)) return reply(400, { ok: false, error: 'Invalid license ID.' });
    const { data: row, error: readError } = await admin.from('issued_machine_keys').select('*').eq('license_id', id).maybeSingle();
    if (readError || !row) throw new Error('License not found or could not be read.');
    // Never re-sign an ambiguous machine. This does not create or unpause a license.
    if (row.key_type !== 'addon') {
      const { count, error: countError } = await admin.from('issued_machine_keys').select('id', { count: 'exact', head: true })
        .eq('machine_id', row.machine_id).in('key_type', ['license', 'combo']).gte('expires_at', new Date().toISOString().slice(0, 10));
      if (countError || count !== 1) throw new Error('Resolve duplicate unexpired licenses for this Machine ID first.');
    }
    const secret = Deno.env.get('CAFEDESK_OWNER_PRIVATE_KEY_B64') || '';
    if (!secret) throw new Error('Cloud signing secret is not configured.');
    // Pin both public keys in server configuration; never accept key material from the browser.
    const oldPublic = Deno.env.get('CAFEDESK_REPAIR_OLD_PUBLIC_KEY_B64') || '';
    const expectedPublic = Deno.env.get('CAFEDESK_REPAIR_EXPECTED_PUBLIC_KEY_B64') || '';
    if (!oldPublic || !expectedPublic) throw new Error('Repair public keys are not configured on the server.');
    const replacement = await resignLicense(row, secret, oldPublic, expectedPublic);
    // The DB operation locks the machine and compare-and-sets a token hash, so
    // concurrent issuance or a pause cannot silently replace another record.
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(row.token)));
    const hashHex = Array.from(hash, b => b.toString(16).padStart(2, '0')).join('');
    const { data: changed, error: updateError } = await admin.rpc('replace_license_signature', {
      p_license_id: id, p_old_token_sha256: hashHex, p_new_token: replacement,
      p_machine_id: row.machine_id, p_customer: row.customer, p_issued_at: row.issued_at,
      p_expires_at: row.expires_at, p_key_type: row.key_type, p_shop_id: row.shop_id,
      p_seat_no: row.seat_no, p_seat_max: row.seat_max, p_seat_label: row.seat_label, p_features: row.features,
    });
    if (updateError || changed !== true) throw new Error('License changed concurrently, has duplicates, or could not be updated. Refresh and retry.');
    await recordOwnerAudit(admin, gate.actor, {
      action: 'resign_license',
      targetType: 'issued_machine_keys',
      targetId: id,
      detail: { machine_id: row.machine_id, key_type: row.key_type, expires_at: row.expires_at },
    });
    return reply(200, { ok: true, license: { license_id: id, machine_id: row.machine_id, expires_at: row.expires_at } });
  } catch (err) {
    // Never return key material or raw backend errors to the caller.
    return reply(400, { ok: false, error: err instanceof Error ? err.message : 'Repair failed.' });
  }
});
