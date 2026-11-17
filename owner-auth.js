/* Café Desk — owner identity gate.
 *
 * The owner's email address is NOT published anywhere in this repository or on the
 * public site. Only a SHA-256 digest of it is kept here, which is enough to decide
 * "is this session the owner?" in the browser without shipping the address itself.
 *
 * Digest input:  "CAFE-DESK-OWNER-V1|" + lowercased, trimmed email
 * Server-side check (RLS): public.is_cafedesk_owner() compares the same digest.
 *
 * A hash is not a secret — it only stops casual scraping of the owner address.
 * Real enforcement is the RLS policy plus Supabase Auth (sign-ups disabled, MFA).
 */
(function (global) {
  "use strict";

  var DIGEST_DOMAIN = "CAFE-DESK-OWNER-V1|";

  // sha256("CAFE-DESK-OWNER-V1|" + owner email), hex.
  var OWNER_EMAIL_SHA256 =
    "72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc";

  function toHex(buffer) {
    var bytes = new Uint8Array(buffer);
    var out = "";
    for (var i = 0; i < bytes.length; i++) {
      out += bytes[i].toString(16).padStart(2, "0");
    }
    return out;
  }

  function sha256Hex(text) {
    var data = new TextEncoder().encode(text);
    if (global.crypto && global.crypto.subtle) {
      return global.crypto.subtle.digest("SHA-256", data).then(toHex);
    }
    return Promise.reject(new Error("Web Crypto unavailable (needs https:// or localhost)."));
  }

  function emailDigest(email) {
    return sha256Hex(DIGEST_DOMAIN + String(email || "").trim().toLowerCase());
  }

  /** Resolves true only when the email hashes to the stored owner digest. */
  function isOwnerEmail(email) {
    return emailDigest(email).then(function (hex) {
      return hex === OWNER_EMAIL_SHA256;
    }).catch(function () {
      return false;
    });
  }

  global.CafeDeskOwner = {
    digestDomain: DIGEST_DOMAIN,
    ownerDigest: OWNER_EMAIL_SHA256,
    sha256Hex: sha256Hex,
    emailDigest: emailDigest,
    isOwnerEmail: isOwnerEmail
  };
})(window);
