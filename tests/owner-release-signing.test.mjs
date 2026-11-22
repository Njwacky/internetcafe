import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';

const edgeTs = fs.readFileSync(
  new URL('../supabase/functions/owner-admin/index.ts', import.meta.url),
  'utf8',
);
const dashHtml = fs.readFileSync(
  new URL('../owner-dashboard.html', import.meta.url),
  'utf8',
);

// Extract pure signing helpers from owner-admin/index.ts (before Deno.serve) and strip TS type annotations.
function loadEdgeHelpers() {
  const head = edgeTs
    .replace(/^import .*$/gm, '')
    .split('function normalizeMachineId')[0]
    .replace(/:\s*Promise<[^>]+>/g, '')
    .replace(/:\s*(?:string|boolean|Uint8Array|CryptoKey|URL)\b/g, '');
  const ctx = {
    crypto: globalThis.crypto,
    TextEncoder,
    Uint8Array,
    URL,
    atob,
    btoa,
  };
  vm.createContext(ctx);
  vm.runInContext(
    head + '\nglobalThis.exports = { importOwnerSigningKey, signReleaseSha256, releaseSigningMessage, isVerifiedOwnerEmail };',
    ctx,
  );
  return ctx.exports;
}

function extractDashFn(name) {
  const match = new RegExp('(?:async )?function ' + name + '\\(').exec(dashHtml);
  assert.ok(match, `Missing ${name} in owner-dashboard.html`);
  const start = match.index;
  const next = /\n[ \t]*(?:async )?function \w+\(/.exec(dashHtml.slice(start + 1));
  return dashHtml.slice(start, next ? start + 1 + next.index : undefined).replace(/<\/script>[\s\S]*$/, '');
}

test('owner-admin Edge Function contains no plaintext owner email and signs both exe and zip releases', () => {
  assert.ok(!edgeTs.includes('@gmail.com'), 'owner-admin/index.ts must not embed plaintext owner email');
  assert.match(edgeTs, /action === "publish_release"/);
  assert.match(edgeTs, /action === "sync_github_release"/);
  assert.match(edgeTs, /signReleaseSha256\(tag, chosenSha, cryptoKey\)/);
  assert.match(edgeTs, /signReleaseSha256\(tag, zipSha, cryptoKey\)/);
});

test('owner-admin Ed25519 release signature verifies identically in Python cloud_sync.verify_release_signature', async () => {
  const { importOwnerSigningKey, signReleaseSha256 } = loadEdgeHelpers();

  const pyBin = fs.existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python3';
  const keyJson = execFileSync(
    pyBin,
    [
      '-c',
      `import base64, json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
priv = Ed25519PrivateKey.generate()
pr = base64.urlsafe_b64encode(priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode().rstrip('=')
pu = base64.urlsafe_b64encode(priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip('=')
print(json.dumps({"priv": pr, "pub": pu}))`,
    ],
    { encoding: 'utf8' },
  );
  const { priv, pub } = JSON.parse(keyJson.trim());
  const sha = '6efa840474f18d38fc5d0d8f1cd98d96a6ae5c5b8ad7faeee27df75beee6d815';

  const cryptoKey = await importOwnerSigningKey(priv);
  const sig = await signReleaseSha256('v0.8.5', sha, cryptoKey);

  const verifyOut = execFileSync(
    pyBin,
    [
      '-c',
      `import json, sys, cloud_sync
ver, sha, sig, pub = sys.argv[1:5]
ok = cloud_sync.verify_release_signature(ver, sha, sig, pub)
bad_ver = cloud_sync.verify_release_signature("0.8.6", sha, sig, pub)
print(json.dumps({"ok": ok, "bad_ver": bad_ver}))`,
      '0.8.5',
      sha,
      sig,
      pub,
    ],
    { encoding: 'utf8' },
  );
  const parsed = JSON.parse(verifyOut.trim());
  assert.equal(parsed.ok, true, 'Python desktop client must accept Edge Function release signature');
  assert.equal(parsed.bad_ver, false, 'Version mismatch must fail verification');
});

test('publishReleaseNow in owner-dashboard.html signs via owner-admin instead of writing an unsigned row', async () => {
  const adminCalls = [];
  const inputs = {
    '#relVersion': { value: 'v0.8.5' },
    '#relFile': { files: [] },
    '#relUrl': { value: 'https://github.com/Njwacky/internetcafe/releases/download/v0.8.5/CafeDesk-Setup.exe' },
    '#relSha': { value: 'a'.repeat(64) },
    '#relNotes': { value: 'Seamless in-app self-update' },
    '#pubRelBtn': { disabled: false, textContent: 'Upload & publish' },
  };
  const toasts = [];
  const ctx = {
    $: (sel) => inputs[sel],
    callOwnerAdmin: async (payload) => {
      adminCalls.push(payload);
      return { ok: true, release: payload };
    },
    sb: {
      from: () => {
        throw new Error('publishReleaseNow must not write unsigned rows directly via sb.from');
      },
    },
    refreshAll: async () => {},
    toast: (msg, kind = 'ok') => toasts.push([msg, kind]),
  };
  vm.createContext(ctx);
  vm.runInContext(extractDashFn('publishReleaseNow'), ctx);
  await ctx.publishReleaseNow();

  assert.equal(adminCalls.length, 1);
  assert.equal(adminCalls[0].action, 'publish_release');
  assert.equal(adminCalls[0].version, '0.8.5');
  assert.equal(adminCalls[0].sha256, 'a'.repeat(64));
  assert.equal(toasts[0][1], 'ok');
});
