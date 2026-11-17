// Pure WebCrypto helpers; private key material never leaves the Edge Function.
function decode(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+={0,2}$/.test(value)) throw new Error('Invalid base64url data.');
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(normalized + '='.repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}
function encode(bytes) {
  return btoa(Array.from(bytes, b => String.fromCharCode(b)).join('')).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
async function publicKey(value) {
  const bytes = decode(value);
  if (bytes.length !== 32) throw new Error('Public key must be a 32-byte Ed25519 key.');
  return crypto.subtle.importKey('raw', bytes, { name: 'Ed25519' }, false, ['verify']);
}
async function signingKey(value) {
  const seed = decode(value);
  if (seed.length !== 32) throw new Error('Signing secret must be a 32-byte Ed25519 private seed.');
  const prefix = Uint8Array.from([0x30,0x2e,0x02,0x01,0x00,0x30,0x05,0x06,0x03,0x2b,0x65,0x70,0x04,0x22,0x04,0x20]);
  const der = new Uint8Array(prefix.length + seed.length);
  der.set(prefix); der.set(seed, prefix.length);
  return crypto.subtle.importKey('pkcs8', der, { name: 'Ed25519' }, false, ['sign']);
}

export async function resignLicense(row, privateSeed, oldPublic, expectedPublic, today = new Date().toISOString().slice(0, 10)) {
  if (!row || row.revoked || row.expires_at < today) throw new Error('Only an unexpired, unpaused license can be re-signed.');
  const parts = String(row.token || '').split('.');
  if (parts.length !== 3 || !['CDLIC1', 'CDADD1'].includes(parts[0])) throw new Error('Stored license format is invalid.');
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  if (!await crypto.subtle.verify('Ed25519', await publicKey(oldPublic), decode(parts[2]), signed))
    throw new Error('The existing key does not match the trusted old public key. Nothing was changed.');
  let p;
  try { p = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(decode(parts[1]))); }
  catch (_) { throw new Error('Stored license payload is invalid.'); }
  if (!p || p.v !== 1 || p.mid !== row.machine_id || p.lid !== row.license_id ||
      p.cust !== row.customer || p.iat !== row.issued_at || p.exp !== row.expires_at ||
      (parts[0] === 'CDADD1' ? row.key_type !== 'addon' || p.type !== 'addon' : !['license','combo'].includes(row.key_type)))
    throw new Error('Stored license payload does not match its database record. Nothing was changed.');
  for (const field of ['shop_id', 'seat_no', 'seat_max', 'seat_label']) {
    if (Object.hasOwn(p, field) && p[field] !== row[field])
      throw new Error('Stored license payload does not match its database record. Nothing was changed.');
  }
  const signedFeatures = Array.isArray(p.feat) ? [...p.feat].sort() : [];
  const storedFeatures = Array.isArray(row.features) ? [...row.features].sort() : [];
  if (JSON.stringify(signedFeatures) !== JSON.stringify(storedFeatures))
    throw new Error('Stored license payload does not match its database record. Nothing was changed.');
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', await signingKey(privateSeed), signed));
  if (!await crypto.subtle.verify('Ed25519', await publicKey(expectedPublic), signature, signed))
    throw new Error('The cloud signing secret does not match the expected public key. Nothing was changed.');
  return `${parts[0]}.${parts[1]}.${encode(signature)}`;
}
