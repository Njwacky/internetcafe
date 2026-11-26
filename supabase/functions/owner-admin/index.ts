import { createClient } from "npm:@supabase/supabase-js@2";
import { recordOwnerAudit, verifyOwnerMfaRequest } from "../_shared/owner-auth.mjs";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const OWNER_EMAIL_DOMAIN = "CAFE-DESK-OWNER-V1|";
const DEFAULT_OWNER_EMAIL_SHA256 = "72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc";
const RELEASE_SIGNING_DOMAIN = "CAFE-DESK-RELEASE-V1";

function b64uEncode(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64uDecode(text: string): Uint8Array {
  const clean = text.trim().replace(/-/g, "+").replace(/_/g, "/");
  const pad = "=".repeat((4 - (clean.length % 4)) % 4);
  const raw = atob(clean + pad);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

// Wrap 32-byte raw Ed25519 private seed into RFC 8410 PKCS#8 DER so WebCrypto can import it
function ed25519SeedToPkcs8(seed32: Uint8Array): Uint8Array {
  const prefix = new Uint8Array([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06,
    0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
  ]);
  const out = new Uint8Array(prefix.length + 32);
  out.set(prefix, 0);
  out.set(seed32, prefix.length);
  return out;
}

async function isVerifiedOwnerEmail(email: string, configuredOwnerEmail: string): Promise<boolean> {
  const clean = String(email || "").trim().toLowerCase();
  if (!clean) return false;
  const explicit = String(configuredOwnerEmail || "").trim().toLowerCase();
  if (explicit) return clean === explicit;
  const digestBuf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(OWNER_EMAIL_DOMAIN + clean),
  );
  const hex = Array.from(new Uint8Array(digestBuf)).map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex === DEFAULT_OWNER_EMAIL_SHA256;
}

function releaseSigningMessage(version: string, sha256Hex: string): Uint8Array {
  const cleanVer = String(version || "").trim().replace(/^[vV]+/, "");
  const cleanHash = String(sha256Hex || "").trim().toLowerCase();
  return new TextEncoder().encode(`${RELEASE_SIGNING_DOMAIN}:${cleanVer}:${cleanHash}`);
}

async function importOwnerSigningKey(privKeyB64: string): Promise<CryptoKey> {
  if (!privKeyB64) {
    throw new Error("CAFEDESK_OWNER_PRIVATE_KEY_B64 secret is not set on Supabase.");
  }
  const seed = b64uDecode(privKeyB64);
  if (seed.length !== 32) throw new Error("Invalid Ed25519 private seed length.");
  return await crypto.subtle.importKey(
    "pkcs8",
    ed25519SeedToPkcs8(seed),
    { name: "Ed25519" },
    false,
    ["sign"],
  );
}

async function signReleaseSha256(version: string, sha256Hex: string, cryptoKey: CryptoKey): Promise<string> {
  const msg = releaseSigningMessage(version, sha256Hex);
  const sigBuf = await crypto.subtle.sign({ name: "Ed25519" }, cryptoKey, msg);
  return b64uEncode(new Uint8Array(sigBuf));
}

// --- GitHub release checksum helpers (pure, unit-tested) ---
// A published SHA-256 must describe the exact bytes customers will download.
// The checksums manifest, the GitHub asset digest (computed by GitHub from the
// stored bytes) and the release notes are three independent claims about the
// same file. If they ever disagree — e.g. Sync was clicked mid-upload —
// publishing any one of them would sign a hash that does not match the file
// and break every in-app update. Fail loudly instead.
function hashAfterLabel(body, label) {
  const lines = String(body || "").split(/\r?\n/);
  const needle = String(label || "").toLowerCase();
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].toLowerCase().includes(needle)) continue;
    const here = lines[i].match(/\b([0-9a-f]{64})\b/i);
    if (here) return here[1].toLowerCase();
    const next = i + 1 < lines.length ? lines[i + 1].match(/\b([0-9a-f]{64})\b/i) : null;
    if (next) return next[1].toLowerCase();
  }
  return "";
}

function parseReleaseBodyHashes(body) {
  const text = String(body || "");
  let exeSha = hashAfterLabel(text, "CafeDesk-Setup.exe");
  let zipSha = hashAfterLabel(text, "CafeDesk-Windows-USB-Portable.zip");
  if (!exeSha || !zipSha) {
    const all = Array.from(text.matchAll(/\b([0-9a-f]{64})\b/gi), (m) => m[1].toLowerCase());
    if (!exeSha && all[0]) exeSha = all[0];
    if (!zipSha && all[1]) zipSha = all[1];
  }
  return { exeSha, zipSha };
}

function assetDigestSha(asset) {
  if (!asset || typeof asset.digest !== "string") return "";
  const dm = asset.digest.match(/^sha256:([0-9a-f]{64})$/i);
  return dm ? dm[1].toLowerCase() : "";
}

function assertHashesAgree(label, values) {
  const present = (values || []).filter((v) => /^[0-9a-f]{64}$/.test(String(v || "")));
  if (present.length > 1 && present.some((v) => v !== present[0])) {
    throw new Error(
      "GitHub release " + label + " checksums disagree (manifest vs asset digest vs release notes). " +
      "The release may still be uploading — wait a minute and click Sync again. " +
      "Refusing to publish a checksum that does not match the actual file.",
    );
  }
}

function validateHttpsUrl(rawUrl: string, label = "Download URL"): string {
  const clean = String(rawUrl || "").trim();
  let parsed: URL;
  try {
    parsed = new URL(clean);
  } catch {
    throw new Error(`${label} is not a valid URL.`);
  }
  if (parsed.protocol !== "https:") {
    throw new Error(`${label} must start with https://`);
  }
  return clean;
}

function normalizeMachineId(raw: string): string {
  let hex = String(raw || "").toUpperCase().replace(/[^0-9A-F]/g, "");
  if (hex.startsWith("CD") && hex.length === 14) hex = hex.slice(2);
  if (hex.length !== 12) {
    throw new Error(`Invalid Machine ID "${raw}" — expected CD-XXXX-XXXX-XXXX (12 hex characters).`);
  }
  return `CD-${hex.slice(0, 4)}-${hex.slice(4, 8)}-${hex.slice(8, 12)}`;
}

function makeShopId(customer: string, explicitShopId?: string): string {
  if (explicitShopId && explicitShopId.trim()) {
    const clean = explicitShopId.trim().toUpperCase().replace(/[^A-Z0-9_-]/g, "").slice(0, 36);
    if (clean) return clean;
  }
  const slug = String(customer || "CAFE").toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 18) || "CAFE";
  return `SHOP-${slug}`;
}

function parseBatchMachines(rawText: string): Array<{ mid: string; label: string }> {
  const chunks = String(rawText || "").split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
  const out: Array<{ mid: string; label: string }> = [];
  const seen = new Set<string>();
  for (const chunk of chunks) {
    let midPart = chunk;
    let labelPart = "";
    for (const sep of ["=", "|", ":"]) {
      const idx = chunk.indexOf(sep);
      if (idx !== -1) {
        midPart = chunk.slice(0, idx).trim();
        labelPart = chunk.slice(idx + 1).trim().replace(/\s+/g, " ").slice(0, 40);
        break;
      }
    }
    const norm = normalizeMachineId(midPart);
    if (seen.has(norm)) continue;
    seen.add(norm);
    out.push({ mid: norm, label: labelPart || `PC ${out.length + 1}` });
  }
  if (!out.length) throw new Error("Provide at least one Machine ID (e.g. CD-1111-2222-3333).");
  return out;
}

function randomLid(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
}

function sortedJsonBytes(obj: Record<string, unknown>): Uint8Array {
  const keys = Object.keys(obj).sort();
  const sorted: Record<string, unknown> = {};
  for (const k of keys) sorted[k] = obj[k];
  return new TextEncoder().encode(JSON.stringify(sorted));
}

async function upsertReleaseWithFallback(
  adminClient: ReturnType<typeof createClient>,
  releaseRow: Record<string, unknown>,
): Promise<void> {
  const { error: relErr } = await adminClient.from("app_releases").upsert(releaseRow);
  if (!relErr) return;
  if (/portable_/i.test(String(relErr.message || ""))) {
    const legacyRow: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(releaseRow)) {
      if (!k.startsWith("portable_")) legacyRow[k] = v;
    }
    const { error: retryErr } = await adminClient.from("app_releases").upsert(legacyRow);
    if (retryErr) throw new Error(retryErr.message);
    return;
  }
  throw new Error(relErr.message);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const ownerEmailEnv = (Deno.env.get("CAFEDESK_OWNER_EMAIL") || "").trim();
    const ownerUserIdEnv = (Deno.env.get("CAFEDESK_OWNER_USER_ID") || "").trim();
    const privKeyB64 = (Deno.env.get("CAFEDESK_OWNER_PRIVATE_KEY_B64") || "").trim();

    const adminClient = createClient(supabaseUrl, serviceRoleKey);
    const gate = await verifyOwnerMfaRequest({
      authHeader: req.headers.get("Authorization") || "",
      adminClient,
      configuredOwnerEmail: ownerEmailEnv,
      configuredOwnerUserId: ownerUserIdEnv,
    });
    if (!gate.ok) {
      return new Response(
        JSON.stringify({ ok: false, code: gate.code, error: gate.error }),
        {
          status: gate.status,
          headers: { ...corsHeaders, "Content-Type": "application/json", "Cache-Control": "no-store" },
        },
      );
    }
    const actor = gate.actor;

    const body = await req.json();
    const action = String(body?.action || "");

    if (action === "sign_keys") {
      const cryptoKey = await importOwnerSigningKey(privKeyB64);

      const customer = String(body.customer || "Licensed Café").trim().replace(/\s+/g, " ").slice(0, 80) || "Licensed Café";
      const days = Math.max(1, Math.min(3650, Number(body.days || 365)));
      const mode = String(body.mode || "license");
      const machines = parseBatchMachines(String(body.machines || ""));
      const isMulti = machines.length > 1 || Boolean(body.shop_id);
      const shopId = isMulti ? makeShopId(customer, body.shop_id) : "";

      const now = new Date();
      const iat = now.toISOString().slice(0, 10);
      const expDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + days));
      const exp = expDate.toISOString().slice(0, 10);

      const dbRows: Array<Record<string, unknown>> = [];
      const issuedOut: Array<Record<string, unknown>> = [];

      for (let i = 0; i < machines.length; i++) {
        const seatNo = i + 1;
        const seatMax = machines.length;
        const { mid, label } = machines[i];
        const lid = randomLid();

        let prefix = "CDLIC1";
        let keyType = "license";
        let feats: string[] = [];
        const payload: Record<string, unknown> = {
          cust: customer,
          exp,
          iat,
          lid,
          mid,
          v: 1,
        };

        if (mode === "addon") {
          prefix = "CDADD1";
          keyType = "addon";
          feats = ["pdf_unlock"];
          payload.type = "addon";
          payload.feat = feats;
        } else if (mode === "combo" || mode === "multi_all" || (mode === "multi_seat1" && seatNo === 1)) {
          keyType = "combo";
          feats = ["pdf_unlock"];
          payload.feat = feats;
        }

        if (isMulti) {
          payload.shop_id = shopId;
          payload.seat_no = seatNo;
          payload.seat_max = seatMax;
          payload.seat_label = label;
        }

        const payloadB64 = b64uEncode(sortedJsonBytes(payload));
        const signingInput = new TextEncoder().encode(`${prefix}.${payloadB64}`);
        const sigBuf = await crypto.subtle.sign({ name: "Ed25519" }, cryptoKey, signingInput);
        const sigB64 = b64uEncode(new Uint8Array(sigBuf));
        const token = `${prefix}.${payloadB64}.${sigB64}`;

        const row = {
          license_id: lid,
          machine_id: mid,
          shop_id: shopId,
          customer,
          seat_no: isMulti ? seatNo : null,
          seat_max: isMulti ? seatMax : null,
          seat_label: isMulti ? label : (label !== "PC 1" ? label : ""),
          key_type: keyType,
          features: feats,
          token,
          issued_at: iat,
          expires_at: exp,
          revoked: false,
          revoke_reason: "",
        };
        dbRows.push(row);
        issuedOut.push(row);
      }

      const { error: insErr } = await adminClient.from("issued_machine_keys").insert(dbRows);
      if (insErr) throw new Error(insErr.message);

      if (body.request_id) {
        await adminClient.from("activation_requests").update({ status: "approved" }).eq("id", String(body.request_id));
      }

      await recordOwnerAudit(adminClient, actor, {
        action: "sign_keys",
        targetType: "issued_machine_keys",
        targetId: shopId || (issuedOut[0] ? String(issuedOut[0].machine_id) : ""),
        detail: {
          mode,
          days,
          customer,
          count: issuedOut.length,
          license_ids: issuedOut.map((r) => r.license_id),
        },
      });

      return new Response(JSON.stringify({ ok: true, shop_id: shopId, issued: issuedOut }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "set_revoked") {
      const lid = String(body.license_id || "").trim();
      const revoked = Boolean(body.revoked);
      const reason = String(body.reason || (revoked ? "Revoked by owner" : "")).slice(0, 160);
      const { error } = await adminClient
        .from("issued_machine_keys")
        .update({ revoked, revoke_reason: reason })
        .eq("license_id", lid);
      if (error) throw new Error(error.message);
      await recordOwnerAudit(adminClient, actor, {
        action: "set_revoked",
        targetType: "issued_machine_keys",
        targetId: lid,
        detail: { revoked, reason },
      });
      return new Response(JSON.stringify({ ok: true, license_id: lid, revoked }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "update_request_status") {
      const reqId = String(body.request_id || "").trim();
      const status = String(body.status || "rejected");
      const { error } = await adminClient.from("activation_requests").update({ status }).eq("id", reqId);
      if (error) throw new Error(error.message);
      await recordOwnerAudit(adminClient, actor, {
        action: "update_request_status",
        targetType: "activation_requests",
        targetId: reqId,
        detail: { status },
      });
      return new Response(JSON.stringify({ ok: true }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "publish_release") {
      const cryptoKey = await importOwnerSigningKey(privKeyB64);
      const cleanVer = String(body.version || "").trim().replace(/^[vV]+/, "");
      if (!/^\d+\.\d+\.\d+(?:[-._][0-9A-Za-z._-]+)?$/.test(cleanVer)) {
        throw new Error("Enter a valid semantic version (e.g. 0.8.5).");
      }
      const downloadUrl = validateHttpsUrl(String(body.download_url || ""), "Download URL");
      const sha256 = String(body.sha256 || "").trim().toLowerCase();
      if (!/^[0-9a-f]{64}$/.test(sha256)) {
        throw new Error("A valid 64-character SHA-256 checksum is required.");
      }
      const signature = await signReleaseSha256(cleanVer, sha256, cryptoKey);

      let portableUrl = "";
      let portableSha = "";
      let portableSig = "";
      if (body.portable_download_url) {
        portableUrl = validateHttpsUrl(String(body.portable_download_url), "Portable ZIP URL");
        portableSha = String(body.portable_sha256 || "").trim().toLowerCase();
        if (!/^[0-9a-f]{64}$/.test(portableSha)) {
          throw new Error("A valid 64-character SHA-256 checksum is required for the portable ZIP.");
        }
        portableSig = await signReleaseSha256(cleanVer, portableSha, cryptoKey);
      } else if (downloadUrl.toLowerCase().endsWith(".zip")) {
        portableUrl = downloadUrl;
        portableSha = sha256;
        portableSig = signature;
      }

      const releaseRow: Record<string, unknown> = {
        version: cleanVer,
        download_url: downloadUrl,
        sha256,
        signature,
        portable_download_url: portableUrl,
        portable_sha256: portableSha,
        portable_signature: portableSig,
        release_notes: String(body.release_notes || "").trim().slice(0, 2000),
        mandatory: Boolean(body.mandatory),
        published_at: body.published_at ? String(body.published_at) : new Date().toISOString(),
      };
      await upsertReleaseWithFallback(adminClient, releaseRow);
      await recordOwnerAudit(adminClient, actor, {
        action: "publish_release",
        targetType: "app_releases",
        targetId: cleanVer,
        detail: { sha256, portable_sha256: portableSha, mandatory: Boolean(body.mandatory) },
      });
      return new Response(JSON.stringify({ ok: true, release: releaseRow }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    if (action === "sync_github_release") {
      const cryptoKey = await importOwnerSigningKey(privKeyB64);
      const repo = String(body.repo || "Njwacky/internetcafe").trim();
      const ghResp = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
        headers: {
          "Accept": "application/vnd.github+json",
          "User-Agent": "CafeDesk-Owner-Admin",
        },
      });
      if (!ghResp.ok) {
        throw new Error(`GitHub Release not built yet (HTTP ${ghResp.status}). Wait ~90s for GitHub Actions to finish building and click Sync again.`);
      }
      const ghRel = await ghResp.json();
      const tag = String(ghRel.tag_name || "").replace(/^v/i, "").trim();
      if (!tag) {
        throw new Error("Latest GitHub Release is missing a valid version tag.");
      }
      const assets: Array<Record<string, unknown>> = Array.isArray(ghRel.assets) ? ghRel.assets : [];
      const exeAsset = assets.find((a) => String(a.name || "").toLowerCase().endsWith(".exe"));
      const zipAsset = assets.find((a) => String(a.name || "").toLowerCase().endsWith(".zip"));
      const manifestAsset = assets.find((a) => String(a.name || "").toLowerCase().endsWith("checksums.json"));

      let manifestExeSha = "";
      let manifestZipSha = "";
      if (manifestAsset && manifestAsset.browser_download_url) {
        const mResp = await fetch(String(manifestAsset.browser_download_url));
        if (mResp.ok) {
          try {
            const mText = (await mResp.text()).replace(/^\uFEFF/, "").trim();
            const mJson = JSON.parse(mText);
            const manifestVer = String(mJson?.version || "").trim().replace(/^v/i, "");
            if (manifestVer && manifestVer !== tag) {
              throw new Error(
                `Checksums manifest is for v${manifestVer} but the GitHub release is v${tag}. ` +
                "The release may still be uploading — wait a minute and click Sync again.",
              );
            }
            manifestExeSha = String(mJson?.setup_exe?.sha256 || "").toLowerCase().trim();
            manifestZipSha = String(mJson?.usb_zip?.sha256 || "").toLowerCase().trim();
          } catch (err) {
            if (err instanceof Error && /Checksums manifest is for/.test(err.message)) throw err;
            // Fall through to GitHub asset digests / release body checksums below
          }
        }
      }
      const digestExeSha = assetDigestSha(exeAsset);
      const digestZipSha = assetDigestSha(zipAsset);
      const bodyHashes = parseReleaseBodyHashes(ghRel.body || "");
      // Every independent claim about the same file must agree before signing.
      assertHashesAgree("Setup.exe", [digestExeSha, manifestExeSha, bodyHashes.exeSha]);
      assertHashesAgree("Portable ZIP", [digestZipSha, manifestZipSha, bodyHashes.zipSha]);
      // GitHub's asset digest is computed from the stored bytes, so it wins;
      // the manifest and release notes are fallbacks for older releases.
      const exeSha = digestExeSha || manifestExeSha || bodyHashes.exeSha;
      const zipSha = digestZipSha || manifestZipSha || bodyHashes.zipSha;

      const chosenAsset = exeAsset || zipAsset;
      const chosenSha = exeAsset ? exeSha : zipSha;
      if (!chosenAsset || !chosenAsset.browser_download_url) {
        throw new Error("Release found on GitHub, but Windows .exe/.zip assets are still uploading. Try again in 30 seconds.");
      }
      if (!/^[0-9a-f]{64}$/.test(chosenSha)) {
        throw new Error("Release found, but SHA-256 checksum manifest was missing.");
      }

      const downloadUrl = validateHttpsUrl(String(chosenAsset.browser_download_url), "GitHub asset URL");
      const signature = await signReleaseSha256(tag, chosenSha, cryptoKey);

      let portableUrl = "";
      let portableSha = "";
      let portableSig = "";
      if (zipAsset && zipAsset.browser_download_url && /^[0-9a-f]{64}$/.test(zipSha)) {
        portableUrl = validateHttpsUrl(String(zipAsset.browser_download_url), "GitHub portable ZIP URL");
        portableSha = zipSha;
        portableSig = await signReleaseSha256(tag, zipSha, cryptoKey);
      }

      const releaseRow: Record<string, unknown> = {
        version: tag,
        download_url: downloadUrl,
        sha256: chosenSha,
        signature,
        portable_download_url: portableUrl,
        portable_sha256: portableSha,
        portable_signature: portableSig,
        release_notes: String(body.release_notes || `Official Café Desk v${tag} Windows 10/11 in-app update.`),
        mandatory: Boolean(body.mandatory),
        published_at: new Date().toISOString(),
      };
      await upsertReleaseWithFallback(adminClient, releaseRow);
      await recordOwnerAudit(adminClient, actor, {
        action: "sync_github_release",
        targetType: "app_releases",
        targetId: tag,
        detail: { repo, sha256: chosenSha, portable_sha256: portableSha },
      });

      return new Response(JSON.stringify({
        ok: true,
        release: releaseRow,
        exe_url: exeAsset ? String(exeAsset.browser_download_url) : "",
        zip_url: portableUrl,
        exe_sha256: exeSha,
        zip_sha256: zipSha,
      }), {
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    return new Response(JSON.stringify({ ok: false, error: `Unknown action "${action}"` }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return new Response(JSON.stringify({ ok: false, error: msg }), {
      status: 400,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  }
});
