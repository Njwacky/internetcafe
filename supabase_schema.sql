-- =============================================================================
-- CAFÉ DESK — SUPABASE POSTGRESQL SCHEMA + ROW LEVEL SECURITY (RLS)
-- =============================================================================
-- How to use:
--   1. Open your Supabase project -> SQL Editor -> New Query.
--   2. Paste this entire file and click "Run".
--   3. Put your PUBLIC Project URL + `anon` key in Café Desk settings (or env vars:
--      CAFE_DESK_SUPABASE_URL / CAFE_DESK_SUPABASE_ANON_KEY).
--   4. Keep your `service_role` key STRICTLY PRIVATE in `owner-keys/supabase-owner.json`
--      on your own computer (gitignored — NEVER shipped inside CafeDesk.exe).
--
-- Privacy guarantee:
--   Customer PDFs, IDs, scans, and photos NEVER touch Supabase. Only software
--   release metadata, Machine ID seat approvals, shop price lists, and anonymized
--   line-item totals are stored when a shop opts in.
--
-- Security notes (audit 2026-11-17):
--   * Shop Sync Passcodes now require 10+ characters (client-enforced in
--     cloud_sync.hash_shop_sync_secret). Shops with older 4-char passcodes must
--     rotate: owner runs `DELETE FROM public.shop_sync_profiles WHERE shop_id='...';`
--     then the shop re-pushes prices with a new 10+ char passcode.
--   * Releases are Ed25519-signed (app_releases.signature). Publish via
--     cloud_sync.owner_publish_app_release (signs with owner-keys private key)
--     or update the owner-admin Edge Function to sign before upserting.
--   * UNSIGNED RELEASES ARE REFUSED by the desktop client. An empty `signature`
--     no longer falls back to SHA-256-only verification, because this project's
--     `anon` key is public (it is embedded in the app and in owner-dashboard.html);
--     a writable app_releases row must therefore never be enough to push an .exe.
--     Always publish with a signature from `admin_keygen.py` / owner_publish_app_release.
--   * Telemetry (section 7) is OPT-IN in the client: nothing is uploaded until a
--     staff member enables "Share anonymous usage statistics" in Help & privacy.
-- =============================================================================

create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- 1. REMOTE OVER-THE-INTERNET APP RELEASES (Public Read-Only, Owner Write-Only)
-- -----------------------------------------------------------------------------
create table if not exists public.app_releases (
    version                 text primary key,
    download_url            text not null,
    sha256                  text not null,
    signature               text not null default '', -- Ed25519 sig over CAFE-DESK-RELEASE-V1:{version}:{sha256}
    portable_download_url   text not null default '',
    portable_sha256         text not null default '',
    portable_signature      text not null default '', -- Ed25519 sig over CAFE-DESK-RELEASE-V1:{version}:{portable_sha256}
    release_notes           text not null default '',
    mandatory               boolean not null default false,
    min_os                  text not null default 'Windows 10+',
    published_at            timestamptz not null default now()
);

-- Migration for projects created before release signing / portable in-app updates (safe to re-run):
alter table public.app_releases add column if not exists signature text not null default '';
alter table public.app_releases add column if not exists portable_download_url text not null default '';
alter table public.app_releases add column if not exists portable_sha256 text not null default '';
alter table public.app_releases add column if not exists portable_signature text not null default '';

alter table public.app_releases enable row level security;

drop policy if exists "Anyone can check latest app release" on public.app_releases;
create policy "Anyone can check latest app release"
    on public.app_releases
    for select
    to anon, authenticated
    using (true);

-- -----------------------------------------------------------------------------
-- CUSTOMER ANNOUNCEMENTS (in-app updates, service notices, and promotions)
-- -----------------------------------------------------------------------------
create table if not exists public.app_announcements (
    id              uuid primary key default gen_random_uuid(),
    title           text not null,
    message         text not null,
    kind            text not null default 'info' check (kind in ('update', 'promotion', 'info')),
    active          boolean not null default true,
    published_at    timestamptz not null default now()
);

alter table public.app_announcements enable row level security;
drop policy if exists "Customers can read active announcements" on public.app_announcements;
create policy "Customers can read active announcements" on public.app_announcements
    for select to anon, authenticated using (active = true);

grant select on public.app_announcements to anon, authenticated;
grant insert, update, delete on public.app_announcements to authenticated;

-- -----------------------------------------------------------------------------
-- 2. MULTI-PC SHOPS & SEAT QUOTAS (Owner Managed via service_role / Edge RPC)
-- -----------------------------------------------------------------------------
create table if not exists public.shops (
    shop_id         text primary key,
    shop_name       text not null,
    shop_pin_hash   text not null default '',
    max_seats       integer not null default 1 check (max_seats >= 1 and max_seats <= 200),
    expires_at      date not null,
    active          boolean not null default true,
    notes           text not null default '',
    created_at      timestamptz not null default now()
);

alter table public.shops enable row level security;
-- No anon SELECT/INSERT policy on `shops`: customers interact only via controlled RPCs
-- or owner service_role tools so one shop cannot enumerate other shops.

-- -----------------------------------------------------------------------------
-- 3. ISSUED MACHINE KEYS & REMOTE REVOCATION LIST
-- -----------------------------------------------------------------------------
create table if not exists public.issued_machine_keys (
    id              uuid primary key default gen_random_uuid(),
    license_id      text unique not null,
    machine_id      text not null,
    shop_id         text not null default '',
    customer        text not null default 'Licensed Café',
    seat_no         integer,
    seat_max        integer,
    seat_label      text not null default '',
    key_type        text not null default 'license', -- 'license' | 'addon' | 'combo'
    features        jsonb not null default '[]'::jsonb,
    token           text not null,
    issued_at       date not null,
    expires_at      date not null,
    revoked         boolean not null default false,
    revoke_reason   text not null default '',
    created_at      timestamptz not null default now()
);

create index if not exists idx_issued_machine_keys_mid on public.issued_machine_keys (machine_id);
create index if not exists idx_issued_machine_keys_shop on public.issued_machine_keys (shop_id);

alter table public.issued_machine_keys enable row level security;

-- Serialize issuance per Machine ID. Existing duplicates are left for the owner to
-- resolve; new overlapping base keys must reuse the existing key (even if paused).
create or replace function public.guard_duplicate_base_license()
returns trigger language plpgsql set search_path = public as $$
begin
    if new.key_type in ('license', 'combo') and new.expires_at >= current_date then
        perform pg_advisory_xact_lock(hashtextextended(upper(trim(new.machine_id)), 0));
        if exists (select 1 from public.issued_machine_keys k
                   where upper(trim(k.machine_id)) = upper(trim(new.machine_id))
                     and k.key_type in ('license', 'combo')
                     and k.expires_at >= current_date
                     and k.license_id <> new.license_id) then
            raise exception 'An unexpired base key already exists for this Machine ID. Reuse it or resolve the conflict before issuing another.';
        end if;
    end if;
    return new;
end; $$;
drop trigger if exists guard_duplicate_base_license on public.issued_machine_keys;
create trigger guard_duplicate_base_license before insert or update of machine_id, key_type, expires_at, license_id
    on public.issued_machine_keys for each row execute function public.guard_duplicate_base_license();

-- Controlled RPC: A customer PC can ONLY fetch non-revoked keys matching its exact Machine ID,
-- and check whether its specific license_id has been remotely revoked by the owner.
-- NOTE: the legacy one-argument overload was dropped in migration
-- 20261119200000_rpc_access_hardening.sql; only the (text, uuid) signature below
-- exists. Keeping both overloads would make single-argument calls ambiguous.
-- Only service_role may call this compare-and-set. It does not sign anything.
-- The Edge Function must verify both signatures before invoking it.
create or replace function public.replace_license_signature(
    p_license_id text, p_old_token_sha256 text, p_new_token text,
    p_machine_id text, p_customer text, p_issued_at date, p_expires_at date, p_key_type text,
    p_shop_id text, p_seat_no integer, p_seat_max integer, p_seat_label text, p_features jsonb
) returns boolean language plpgsql security invoker set search_path = public as $$
declare
    v_count integer;
    v_updated integer;
begin
    perform pg_advisory_xact_lock(hashtextextended(upper(trim(p_machine_id)), 0));
    if p_key_type in ('license', 'combo') then
        select count(*) into v_count from public.issued_machine_keys
        where upper(trim(machine_id)) = upper(trim(p_machine_id))
          and key_type in ('license','combo') and expires_at >= current_date;
        if v_count <> 1 then return false; end if;
    end if;
    update public.issued_machine_keys set token = p_new_token
    where license_id = p_license_id and machine_id = p_machine_id and customer = p_customer
      and issued_at = p_issued_at and expires_at = p_expires_at and key_type = p_key_type
      and shop_id is not distinct from p_shop_id and seat_no is not distinct from p_seat_no
      and seat_max is not distinct from p_seat_max and seat_label is not distinct from p_seat_label
      and features is not distinct from p_features
      and not revoked and expires_at >= current_date
      and encode(sha256(convert_to(token, 'UTF8')), 'hex') = p_old_token_sha256;
    get diagnostics v_updated = row_count;
    return v_updated = 1;
end; $$;
revoke all on function public.replace_license_signature(text,text,text,text,text,date,date,text,text,integer,integer,text,jsonb) from public, anon, authenticated;
grant execute on function public.replace_license_signature(text,text,text,text,text,date,date,text,text,integer,integer,text,jsonb) to service_role;

-- -----------------------------------------------------------------------------
-- 4. MULTI-PC ACTIVATION REQUEST QUEUE (Shop PC -> Owner Approval Queue)
-- -----------------------------------------------------------------------------
create table if not exists public.activation_requests (
    id              uuid primary key default gen_random_uuid(),
    machine_id      text not null,
    shop_id         text not null default '',
    shop_name       text not null default '',
    station_name    text not null default '',
    requested_kind  text not null default 'license', -- 'license' | 'pdf_unlock' | 'combo'
    contact_phone   text not null default '',
    status          text not null default 'pending', -- 'pending' | 'approved' | 'rejected'
    created_at      timestamptz not null default now()
);

alter table public.activation_requests add column if not exists contact_name text not null default '';

create index if not exists idx_activation_requests_status on public.activation_requests (status, created_at desc);

alter table public.activation_requests enable row level security;

create or replace function public.submit_activation_request(
    p_machine_id text,
    p_shop_id text default '',
    p_shop_name text default '',
    p_station_name text default '',
    p_requested_kind text default 'license',
    p_contact_phone text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_req_id uuid;
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;

    insert into public.activation_requests (
        machine_id, shop_id, shop_name, station_name, requested_kind, contact_phone
    ) values (
        v_mid,
        left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40),
        left(trim(coalesce(p_shop_name, '')), 80),
        left(trim(coalesce(p_station_name, '')), 40),
        case when p_requested_kind in ('license', 'pdf_unlock', 'combo') then p_requested_kind else 'license' end,
        left(trim(coalesce(p_contact_phone, '')), 40)
    )
    returning id into v_req_id;

    return jsonb_build_object('ok', true, 'request_id', v_req_id, 'machine_id', v_mid);
end;
$$;

grant execute on function public.submit_activation_request(text, text, text, text, text, text) to anon, authenticated;

-- Dedicated contact request; legacy request RPC remains available to older PCs.
create or replace function public.submit_license_contact_request(
    p_machine_id text, p_contact_name text, p_company text, p_contact_phone text,
    p_station_name text default ''
) returns jsonb language plpgsql security definer set search_path = public as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_req_id uuid;
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;
    if trim(coalesce(p_contact_name, '')) = '' or trim(coalesce(p_company, '')) = ''
       or length(regexp_replace(coalesce(p_contact_phone, ''), '[^0-9]', '', 'g')) < 7 then
        return jsonb_build_object('ok', false, 'error', 'Name, company and a valid phone number are required.');
    end if;
    insert into public.activation_requests (machine_id, shop_name, contact_name, contact_phone, station_name, requested_kind)
    values (v_mid, left(trim(p_company), 80), left(trim(p_contact_name), 80),
            left(trim(p_contact_phone), 40), left(trim(coalesce(p_station_name, '')), 40), 'license')
    returning id into v_req_id;
    return jsonb_build_object('ok', true, 'request_id', v_req_id, 'machine_id', v_mid);
end; $$;
grant execute on function public.submit_license_contact_request(text, text, text, text, text) to anon, authenticated;

-- -----------------------------------------------------------------------------
-- 5. MULTI-PC SHOP PRICE LIST & RECORDED SALES SYNC (Opt-In per Shop via Sync Key)
-- -----------------------------------------------------------------------------
-- Each multi-PC shop chooses a shared secret `shop_sync_secret` (hashed with SHA-256
-- inside these RPCs) so Desk 1, Desk 2, Desk 3, and the Shop Owner's laptop can sync
-- prices and combine daily sales totals without exposing data to any other shop.

create table if not exists public.shop_sync_profiles (
    shop_id         text primary key,
    sync_key_sha256 text not null,
    business_name   text not null default 'Internet Café',
    currency        text not null default 'R',
    prices_json     jsonb not null default '[]'::jsonb,
    updated_by      text not null default '',
    updated_at      timestamptz not null default now()
);

alter table public.shop_sync_profiles enable row level security;

create table if not exists public.shop_sync_sales (
    shop_id         text not null,
    sale_id         text not null,
    created_at      text not null,
    station         text not null default '',
    business        text not null default '',
    currency        text not null default 'R',
    total_cents     integer not null default 0,
    items_json      jsonb not null default '[]'::jsonb,
    voided_at       text,
    void_reason     text,
    synced_at       timestamptz not null default now(),
    primary key (shop_id, sale_id)
);

create index if not exists idx_shop_sync_sales_created on public.shop_sync_sales (shop_id, created_at desc);

alter table public.shop_sync_sales enable row level security;

-- RPC: Push/update shop price list (registers shop sync secret on first push, verifies thereafter)
create or replace function public.push_shop_profile(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_business_name text,
    p_currency text,
    p_prices_json jsonb,
    p_station text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_existing_hash text;
begin
    if length(v_shop) < 3 or length(coalesce(p_sync_key_sha256, '')) <> 64 then
        return jsonb_build_object('ok', false, 'error', 'Valid Shop ID and Shop Sync Secret are required.');
    end if;

    select sync_key_sha256 into v_existing_hash
    from public.shop_sync_profiles
    where shop_id = v_shop;

    if v_existing_hash is not null and v_existing_hash <> p_sync_key_sha256 then
        return jsonb_build_object('ok', false, 'error', 'Shop Sync Secret does not match this Shop ID.');
    end if;

    insert into public.shop_sync_profiles (
        shop_id, sync_key_sha256, business_name, currency, prices_json, updated_by, updated_at
    ) values (
        v_shop,
        p_sync_key_sha256,
        left(trim(coalesce(p_business_name, 'Internet Café')), 80),
        left(trim(coalesce(p_currency, 'R')), 8),
        coalesce(p_prices_json, '[]'::jsonb),
        left(trim(coalesce(p_station, '')), 40),
        now()
    )
    on conflict (shop_id) do update set
        business_name = excluded.business_name,
        currency = excluded.currency,
        prices_json = excluded.prices_json,
        updated_by = excluded.updated_by,
        updated_at = now();

    return jsonb_build_object('ok', true, 'shop_id', v_shop);
end;
$$;

grant execute on function public.push_shop_profile(text, text, text, text, jsonb, text) to anon, authenticated;

-- RPC: Pull shop price list onto another PC in the same shop
create or replace function public.pull_shop_profile(
    p_shop_id text,
    p_sync_key_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_row public.shop_sync_profiles%rowtype;
begin
    select * into v_row from public.shop_sync_profiles where shop_id = v_shop;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'No cloud profile found for this Shop ID yet.');
    end if;
    if v_row.sync_key_sha256 <> coalesce(p_sync_key_sha256, '') then
        return jsonb_build_object('ok', false, 'error', 'Shop Sync Secret does not match this Shop ID.');
    end if;

    return jsonb_build_object(
        'ok', true,
        'shop_id', v_row.shop_id,
        'business_name', v_row.business_name,
        'currency', v_row.currency,
        'prices', v_row.prices_json,
        'updated_by', v_row.updated_by,
        'updated_at', v_row.updated_at
    );
end;
$$;

grant execute on function public.pull_shop_profile(text, text) to anon, authenticated;

-- RPC: Sync batch of sales from one PC & return combined sales from all PCs in the same shop
create or replace function public.sync_shop_sales(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_sales jsonb default '[]'::jsonb,
    p_since text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_hash text;
    v_item jsonb;
    v_pushed integer := 0;
    v_rows jsonb;
begin
    select sync_key_sha256 into v_hash from public.shop_sync_profiles where shop_id = v_shop;
    if v_hash is null then
        return jsonb_build_object('ok', false, 'error', 'Push your Shop Price List first to initialize this Shop ID in Supabase.');
    end if;
    if v_hash <> coalesce(p_sync_key_sha256, '') then
        return jsonb_build_object('ok', false, 'error', 'Shop Sync Secret does not match this Shop ID.');
    end if;

    if jsonb_typeof(p_sales) = 'array' then
        for v_item in select * from jsonb_array_elements(p_sales)
        loop
            if coalesce(v_item->>'sale_id', '') <> '' then
                insert into public.shop_sync_sales (
                    shop_id, sale_id, created_at, station, business, currency,
                    total_cents, items_json, voided_at, void_reason, synced_at
                ) values (
                    v_shop,
                    left(v_item->>'sale_id', 64),
                    left(coalesce(v_item->>'created_at', ''), 40),
                    left(coalesce(v_item->>'station', ''), 40),
                    left(coalesce(v_item->>'business', ''), 80),
                    left(coalesce(v_item->>'currency', 'R'), 8),
                    coalesce((v_item->>'total_cents')::integer, 0),
                    coalesce(v_item->'items', '[]'::jsonb),
                    nullif(left(coalesce(v_item->>'voided_at', ''), 40), ''),
                    nullif(left(coalesce(v_item->>'void_reason', ''), 120), ''),
                    now()
                )
                on conflict (shop_id, sale_id) do update set
                    voided_at = coalesce(excluded.voided_at, public.shop_sync_sales.voided_at),
                    void_reason = coalesce(excluded.void_reason, public.shop_sync_sales.void_reason),
                    synced_at = now();
                v_pushed := v_pushed + 1;
            end if;
        end loop;
    end if;

    select coalesce(jsonb_agg(jsonb_build_object(
        'sale_id', s.sale_id,
        'created_at', s.created_at,
        'station', s.station,
        'business', s.business,
        'currency', s.currency,
        'total_cents', s.total_cents,
        'items', s.items_json,
        'voided_at', s.voided_at,
        'void_reason', s.void_reason
    ) order by s.created_at desc), '[]'::jsonb)
    into v_rows
    from (
        select * from public.shop_sync_sales
        where shop_id = v_shop
          and (coalesce(p_since, '') = '' or created_at >= p_since)
        order by created_at desc
        limit 2000
    ) s;

    return jsonb_build_object(
        'ok', true,
        'shop_id', v_shop,
        'pushed_count', v_pushed,
        'sales', v_rows
    );
end;
$$;

-- Public Read-Only Storage Bucket for Remote Over-the-Internet Installer Releases
insert into storage.buckets (id, name, public)
values ('cafedesk-releases', 'cafedesk-releases', true)
on conflict (id) do update set public = true;

drop policy if exists "Public can download cafedesk releases" on storage.objects;
create policy "Public can download cafedesk releases"
    on storage.objects
    for select
    to anon, authenticated
    using (bucket_id = 'cafedesk-releases');

grant execute on function public.sync_shop_sales(text, text, jsonb, text) to anon, authenticated;

-- -----------------------------------------------------------------------------
-- 6. OWNER WEB DASHBOARD RLS POLICIES (`owner-dashboard.html` via Supabase Auth)
-- -----------------------------------------------------------------------------
-- The owner address is NOT stored in plaintext here (nor in owner-dashboard.html).
-- We compare a SHA-256 digest of "CAFE-DESK-OWNER-V1|" + lower(email) instead.
-- This block is generated from tools/sync_owner_digest.py — do NOT edit by hand.
-- To rotate the owner account: update OWNER_EMAIL_SHA256 in that script, run it,
-- and re-run this file in the SQL editor so the database gets the same digest.
-- OWNER-DIGEST-BEGIN (generated by tools/sync_owner_digest.py — do not edit by hand)
create or replace function public.cafedesk_owner_digest()
returns text
language sql
immutable
as $$
    select '72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc';
$$;
-- OWNER-DIGEST-END

create or replace function public.is_cafedesk_owner()
returns boolean
language sql
stable
as $$
    select coalesce(
        coalesce(auth.jwt() ->> 'aal', '') = 'aal2'
        and (
            jsonb_typeof(auth.jwt() -> 'amr') is distinct from 'array'
            or exists (
                select 1
                from jsonb_array_elements(auth.jwt() -> 'amr') as elem
                where lower(coalesce(elem ->> 'method', trim(both '"' from elem::text), ''))
                      in ('totp', 'mfa/totp', 'webauthn', 'passkey', 'recovery_code')
            )
        )
        and encode(
            digest('CAFE-DESK-OWNER-V1|' || lower(coalesce(auth.jwt() ->> 'email', '')), 'sha256'),
            'hex'
        ) = public.cafedesk_owner_digest()
        and (
            nullif(coalesce(auth.jwt() -> 'app_metadata' ->> 'cafedesk_role', ''), '') is null
            or (auth.jwt() -> 'app_metadata' ->> 'cafedesk_role') = 'owner'
        ),
        false
    );
$$;

grant execute on function public.cafedesk_owner_digest() to anon, authenticated;
grant execute on function public.is_cafedesk_owner() to anon, authenticated;

drop policy if exists "Owner full access to issued_machine_keys" on public.issued_machine_keys;
create policy "Owner full access to issued_machine_keys"
    on public.issued_machine_keys for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner full access to activation_requests" on public.activation_requests;
create policy "Owner full access to activation_requests"
    on public.activation_requests for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner full access to shops" on public.shops;
create policy "Owner full access to shops"
    on public.shops for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner manage app announcements" on public.app_announcements;
create policy "Owner manage app announcements" on public.app_announcements
    for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner write access to app_releases" on public.app_releases;
create policy "Owner write access to app_releases"
    on public.app_releases for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner upload to cafedesk-releases" on storage.objects;
create policy "Owner upload to cafedesk-releases"
    on storage.objects for insert to authenticated
    with check (bucket_id = 'cafedesk-releases' and public.is_cafedesk_owner());

drop policy if exists "Owner update cafedesk-releases" on storage.objects;
create policy "Owner update cafedesk-releases"
    on storage.objects for update to authenticated
    using (bucket_id = 'cafedesk-releases' and public.is_cafedesk_owner())
    with check (bucket_id = 'cafedesk-releases' and public.is_cafedesk_owner());

drop policy if exists "Owner delete from cafedesk-releases" on storage.objects;
create policy "Owner delete from cafedesk-releases"
    on storage.objects for delete to authenticated
    using (bucket_id = 'cafedesk-releases' and public.is_cafedesk_owner());

-- -----------------------------------------------------------------------------
-- 7. ANONYMIZED WORKSPACE TELEMETRY & LIVE STATION PULSE (v0.8.2+)
-- -----------------------------------------------------------------------------
-- Privacy-by-design: NEVER accepts filenames, document text, customer names, or
-- personal info. Only counts which sidebar screen/tool was opened and basic
-- station/city/shop heartbeat metadata so the Owner Dashboard shows Most-Visited
-- Places/Tools, Active Cities, and Online PCs.

create table if not exists public.station_heartbeats (
    machine_id      text primary key,
    shop_id         text not null default '',
    shop_name       text not null default '',
    station_name    text not null default '',
    city_label      text not null default '',
    app_version     text not null default '',
    os_platform     text not null default '',
    current_screen  text not null default 'Home',
    total_visits    bigint not null default 0,
    online_seconds  bigint not null default 0,
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now()
);

create index if not exists idx_station_heartbeats_seen on public.station_heartbeats (last_seen_at desc);
create index if not exists idx_station_heartbeats_city on public.station_heartbeats (city_label);
create index if not exists idx_station_heartbeats_online on public.station_heartbeats (online_seconds desc);

alter table public.station_heartbeats enable row level security;

drop policy if exists "Owner full access to station_heartbeats" on public.station_heartbeats;
create policy "Owner full access to station_heartbeats"
    on public.station_heartbeats for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

-- Per-day online duration per PC (answers "which PC stays online too much?").
create table if not exists public.station_online_daily (
    visit_date      date not null default current_date,
    machine_id      text not null,
    shop_id         text not null default '',
    shop_name       text not null default '',
    city_label      text not null default '',
    station_name    text not null default '',
    online_seconds  bigint not null default 0 check (online_seconds >= 0),
    last_online_at  timestamptz not null default now(),
    primary key (visit_date, machine_id)
);

create index if not exists idx_station_online_daily_date on public.station_online_daily (visit_date desc);
create index if not exists idx_station_online_daily_seconds on public.station_online_daily (online_seconds desc);

alter table public.station_online_daily enable row level security;

drop policy if exists "Owner full access to station_online_daily" on public.station_online_daily;
create policy "Owner full access to station_online_daily"
    on public.station_online_daily for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

create table if not exists public.workspace_tool_visits (
    visit_date      date not null default current_date,
    machine_id      text not null,
    shop_id         text not null default '',
    shop_name       text not null default '',
    city_label      text not null default '',
    screen_name     text not null,
    visit_count     integer not null default 1 check (visit_count >= 1),
    last_visited_at timestamptz not null default now(),
    primary key (visit_date, machine_id, screen_name)
);

create index if not exists idx_workspace_tool_visits_date on public.workspace_tool_visits (visit_date desc);
create index if not exists idx_workspace_tool_visits_screen on public.workspace_tool_visits (screen_name);

alter table public.workspace_tool_visits enable row level security;

drop policy if exists "Owner full access to workspace_tool_visits" on public.workspace_tool_visits;
create policy "Owner full access to workspace_tool_visits"
    on public.workspace_tool_visits for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

drop policy if exists "Owner read access to shop_sync_profiles" on public.shop_sync_profiles;
create policy "Owner read access to shop_sync_profiles"
    on public.shop_sync_profiles for select to authenticated
    using (public.is_cafedesk_owner());

drop policy if exists "Owner read access to shop_sync_sales" on public.shop_sync_sales;
create policy "Owner read access to shop_sync_sales"
    on public.shop_sync_sales for select to authenticated
    using (public.is_cafedesk_owner());

create or replace function public.record_workspace_telemetry(
    p_machine_id text,
    p_shop_id text default '',
    p_shop_name text default '',
    p_station_name text default '',
    p_city_label text default '',
    p_app_version text default '',
    p_os_platform text default '',
    p_current_screen text default 'Home',
    p_screen_counts jsonb default '{}'::jsonb,
    p_online_seconds integer default 0
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_bname text := left(trim(coalesce(p_shop_name, 'Café Desk Station')), 80);
    v_stat text := left(trim(coalesce(p_station_name, 'Counter PC 1')), 40);
    v_city text := left(trim(coalesce(p_city_label, '')), 60);
    v_ver text := left(trim(coalesce(p_app_version, '')), 24);
    v_os text := left(trim(coalesce(p_os_platform, '')), 32);
    v_cur text := left(trim(coalesce(p_current_screen, 'Home')), 48);
    v_online integer := least(greatest(coalesce(p_online_seconds, 0), 0), 86400);
    v_key text;
    v_val integer;
    v_added bigint := 0;
    v_allowed constant text[] := array[
        'Home', 'Price list', 'PDF tools', 'Bluetooth files', 'Scan & images',
        'A4 picture sheets', 'Career documents', 'Cards & invitations', 'Forms creator',
        'Receipts', 'Sales reports', 'Price settings', 'Business settings',
        'AI settings', 'Projects & recovery', 'About & contact', 'Help & privacy'
    ];
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;

    if not (v_cur = any(v_allowed)) then
        v_cur := 'Home';
    end if;

    if jsonb_typeof(p_screen_counts) = 'object' then
        for v_key, v_val in
            select key, least(greatest(coalesce(nullif(regexp_replace(value::text, '[^0-9]', '', 'g'), '')::integer, 0), 0), 5000)
            from jsonb_each_text(p_screen_counts)
        loop
            if v_val > 0 and (v_key = any(v_allowed)) then
                insert into public.workspace_tool_visits (
                    visit_date, machine_id, shop_id, shop_name, city_label, screen_name, visit_count, last_visited_at
                ) values (
                    current_date, v_mid, v_shop, v_bname, v_city, v_key, v_val, now()
                )
                on conflict (visit_date, machine_id, screen_name) do update set
                    visit_count = public.workspace_tool_visits.visit_count + excluded.visit_count,
                    shop_id = case when excluded.shop_id <> '' then excluded.shop_id else public.workspace_tool_visits.shop_id end,
                    shop_name = case when excluded.shop_name <> '' then excluded.shop_name else public.workspace_tool_visits.shop_name end,
                    city_label = case when excluded.city_label <> '' then excluded.city_label else public.workspace_tool_visits.city_label end,
                    last_visited_at = now();
                v_added := v_added + v_val;
            end if;
        end loop;
    end if;

    insert into public.station_heartbeats (
        machine_id, shop_id, shop_name, station_name, city_label,
        app_version, os_platform, current_screen, total_visits,
        online_seconds, first_seen_at, last_seen_at
    ) values (
        v_mid, v_shop, v_bname, v_stat, v_city,
        v_ver, v_os, v_cur, greatest(v_added, 1), v_online, now(), now()
    )
    on conflict (machine_id) do update set
        shop_id = case when excluded.shop_id <> '' then excluded.shop_id else public.station_heartbeats.shop_id end,
        shop_name = case when excluded.shop_name <> '' then excluded.shop_name else public.station_heartbeats.shop_name end,
        station_name = case when excluded.station_name <> '' then excluded.station_name else public.station_heartbeats.station_name end,
        city_label = case when excluded.city_label <> '' then excluded.city_label else public.station_heartbeats.city_label end,
        app_version = excluded.app_version,
        os_platform = excluded.os_platform,
        current_screen = excluded.current_screen,
        total_visits = public.station_heartbeats.total_visits + greatest(v_added, 0),
        online_seconds = public.station_heartbeats.online_seconds + excluded.online_seconds,
        last_seen_at = now();

    if v_online > 0 then
        insert into public.station_online_daily (
            visit_date, machine_id, shop_id, shop_name, city_label, station_name, online_seconds, last_online_at
        ) values (
            current_date, v_mid, v_shop, v_bname, v_city, v_stat, v_online, now()
        )
        on conflict (visit_date, machine_id) do update set
            online_seconds = public.station_online_daily.online_seconds + excluded.online_seconds,
            shop_id = case when excluded.shop_id <> '' then excluded.shop_id else public.station_online_daily.shop_id end,
            shop_name = case when excluded.shop_name <> '' then excluded.shop_name else public.station_online_daily.shop_name end,
            city_label = case when excluded.city_label <> '' then excluded.city_label else public.station_online_daily.city_label end,
            station_name = case when excluded.station_name <> '' then excluded.station_name else public.station_online_daily.station_name end,
            last_online_at = now();
    end if;

    return jsonb_build_object('ok', true, 'recorded_visits', v_added, 'online_seconds', v_online);
end;
$$;

grant execute on function public.record_workspace_telemetry(text, text, text, text, text, text, text, text, jsonb, integer) to anon, authenticated;
-- Owner-only, atomic deletion of every Café Desk cloud record keyed directly to
-- one Machine ID. Shop-level sales and other shared shop records are intentionally
-- preserved because they are not reliably attributable to one PC.
create or replace function public.owner_purge_machine_records(p_machine_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_keys integer := 0;
    v_requests integer := 0;
    v_heartbeats integer := 0;
    v_visits integer := 0;
    v_online_rows integer := 0;
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner may delete machine records.' using errcode = '42501';
    end if;

    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;

    delete from public.issued_machine_keys where upper(trim(machine_id)) = v_mid;
    get diagnostics v_keys = row_count;

    delete from public.activation_requests where upper(trim(machine_id)) = v_mid;
    get diagnostics v_requests = row_count;

    delete from public.workspace_tool_visits where upper(trim(machine_id)) = v_mid;
    get diagnostics v_visits = row_count;

    delete from public.station_heartbeats where upper(trim(machine_id)) = v_mid;
    get diagnostics v_heartbeats = row_count;

    delete from public.station_online_daily where upper(trim(machine_id)) = v_mid;
    get diagnostics v_online_rows = row_count;

    return jsonb_build_object(
        'ok', true,
        'machine_id', v_mid,
        'deleted', jsonb_build_object(
            'issued_machine_keys', v_keys,
            'activation_requests', v_requests,
            'workspace_tool_visits', v_visits,
            'station_heartbeats', v_heartbeats,
            'station_online_daily', v_online_rows
        )
    );
end;
$$;

revoke all on function public.owner_purge_machine_records(text) from public, anon;
grant execute on function public.owner_purge_machine_records(text) to authenticated;
-- Harden public client RPCs and remove first-caller ownership of shop sync IDs.
-- Apply after the base Café Desk schema / owner RLS policies.

-- Link a cloud-delivered key to the specific random activation request approved by
-- the owner. A Machine ID alone must not be enough to fetch active signed tokens.
alter table public.activation_requests add column if not exists contact_name text not null default '';
alter table public.issued_machine_keys
    add column if not exists activation_request_id uuid references public.activation_requests(id) on delete set null;

-- Machine-ID-scoped request limits. Only hashes are retained; no customer IP or raw
-- Machine ID is stored here. This is defense in depth, not a substitute for an IP WAF.
create table if not exists public.machine_rpc_rate_limits (
    endpoint text not null,
    machine_hash text not null,
    window_started_at timestamptz not null,
    call_count integer not null default 0,
    updated_at timestamptz not null default now(),
    primary key (endpoint, machine_hash)
);
create index if not exists idx_machine_rpc_rate_limits_updated
    on public.machine_rpc_rate_limits (updated_at);
alter table public.machine_rpc_rate_limits enable row level security;
revoke all on public.machine_rpc_rate_limits from public, anon, authenticated;

create or replace function public.enforce_machine_rpc_rate_limit(
    p_endpoint text,
    p_machine_id text,
    p_max_calls integer,
    p_window_seconds integer
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_hash text;
    v_count integer;
    v_now timestamptz := clock_timestamp();
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;
    if p_max_calls < 1 or p_window_seconds < 1 then
        raise exception 'Invalid rate-limit configuration.' using errcode = '22023';
    end if;

    v_hash := encode(digest(v_mid, 'sha256'), 'hex');
    insert into public.machine_rpc_rate_limits(endpoint, machine_hash, window_started_at, call_count, updated_at)
    values (left(p_endpoint, 40), v_hash, v_now, 1, v_now)
    on conflict (endpoint, machine_hash) do update set
        call_count = case
            when public.machine_rpc_rate_limits.window_started_at <= v_now - make_interval(secs => p_window_seconds) then 1
            else public.machine_rpc_rate_limits.call_count + 1
        end,
        window_started_at = case
            when public.machine_rpc_rate_limits.window_started_at <= v_now - make_interval(secs => p_window_seconds) then v_now
            else public.machine_rpc_rate_limits.window_started_at
        end,
        updated_at = v_now
    returning call_count into v_count;

    if v_count > p_max_calls then
        raise exception 'Too many requests for this computer. Wait and try again.' using errcode = 'P0001';
    end if;

    -- Keep the private limiter table bounded without doing a cleanup scan on every call.
    if random() < 0.02 then
        delete from public.machine_rpc_rate_limits where updated_at < v_now - interval '1 day';
    end if;
end;
$$;
revoke all on function public.enforce_machine_rpc_rate_limit(text, text, integer, integer) from public, anon, authenticated;

-- Owner-only first registration. The owner enters the passcode in the dashboard;
-- only its domain-separated SHA-256 digest is stored.
create or replace function public.owner_register_shop_sync_profile(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_business_name text default 'Internet Café'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_hash text := lower(trim(coalesce(p_sync_key_sha256, '')));
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner may register a shop.' using errcode = '42501';
    end if;
    if length(v_shop) < 3 or v_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Enter a valid Shop ID and a valid 64-character passcode hash.' using errcode = '22023';
    end if;
    if exists (select 1 from public.shop_sync_profiles where shop_id = v_shop) then
        return jsonb_build_object('ok', false, 'error', 'That Shop ID is already registered. It was not changed.');
    end if;

    insert into public.shop_sync_profiles(shop_id, sync_key_sha256, business_name, currency, prices_json, updated_by)
    values (v_shop, v_hash, left(trim(coalesce(p_business_name, 'Internet Café')), 80), 'R', '[]'::jsonb, 'Owner registration');

    return jsonb_build_object('ok', true, 'shop_id', v_shop);
end;
$$;
revoke all on function public.owner_register_shop_sync_profile(text, text, text) from public, anon;
grant execute on function public.owner_register_shop_sync_profile(text, text, text) to authenticated;

-- Public push can update an owner-registered profile only; it cannot claim an
-- unregistered Shop ID. Existing profiles keep their current shared passcode hash.
create or replace function public.push_shop_profile(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_business_name text,
    p_currency text,
    p_prices_json jsonb,
    p_station text default ''
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_existing_hash text;
begin
    if length(v_shop) < 3 or length(coalesce(p_sync_key_sha256, '')) <> 64 then
        return jsonb_build_object('ok', false, 'error', 'Valid Shop ID and Shop Sync Passcode are required.');
    end if;

    select sync_key_sha256 into v_existing_hash
    from public.shop_sync_profiles where shop_id = v_shop;
    if not found then
        return jsonb_build_object('ok', false, 'error', 'This Shop ID is not registered yet. Ask the Café Desk owner to register it first.');
    end if;
    if v_existing_hash <> lower(p_sync_key_sha256) then
        return jsonb_build_object('ok', false, 'error', 'Shop Sync Passcode does not match this Shop ID.');
    end if;

    update public.shop_sync_profiles set
        business_name = left(trim(coalesce(p_business_name, 'Internet Café')), 80),
        currency = left(trim(coalesce(p_currency, 'R')), 8),
        prices_json = coalesce(p_prices_json, '[]'::jsonb),
        updated_by = left(trim(coalesce(p_station, '')), 40),
        updated_at = now()
    where shop_id = v_shop and sync_key_sha256 = lower(p_sync_key_sha256);

    return jsonb_build_object('ok', true, 'shop_id', v_shop);
end;
$$;

-- A random approved request UUID is now required to receive active key tokens.
-- The legacy one-argument call remains resolvable via the default NULL argument,
-- but can only receive revocation notices (not active tokens).
drop function if exists public.check_machine_entitlements(text);
create function public.check_machine_entitlements(p_machine_id text, p_request_id uuid default null)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_keys jsonb;
    v_revoked jsonb;
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;
    perform public.enforce_machine_rpc_rate_limit('check_machine_entitlements', v_mid, 60, 60);

    -- No destructive repair here (see 20261126120000_entitlement_read_side_effect_free.sql):
    -- a read must never revoke or pause licence state. Duplicate unexpired base keys are
    -- resolved by the owner with owner_review_duplicate_machine_keys.

    select coalesce(jsonb_agg(jsonb_build_object('license_id', k.license_id, 'key_type', k.key_type,
        'shop_id', k.shop_id, 'customer', k.customer, 'seat_no', k.seat_no, 'seat_max', k.seat_max,
        'seat_label', k.seat_label, 'expires_at', k.expires_at, 'token', k.token)
        order by k.created_at desc), '[]'::jsonb)
    into v_keys
    from public.issued_machine_keys k
    where upper(trim(k.machine_id)) = v_mid and k.revoked = false and k.expires_at >= current_date
      and p_request_id is not null and k.activation_request_id = p_request_id
      and exists (
          select 1 from public.activation_requests ar
          where ar.id = p_request_id and upper(trim(ar.machine_id)) = v_mid and ar.status = 'approved'
      );

    select coalesce(jsonb_agg(jsonb_build_object('license_id', r.license_id, 'reason', r.revoke_reason)), '[]'::jsonb)
    into v_revoked
    from public.issued_machine_keys r
    where upper(trim(r.machine_id)) = v_mid and r.revoked = true;

    return jsonb_build_object('ok', true, 'machine_id', v_mid, 'active_keys', v_keys, 'revoked_keys', v_revoked);
end;
$$;
revoke all on function public.check_machine_entitlements(text, uuid) from public;
grant execute on function public.check_machine_entitlements(text, uuid) to anon, authenticated;

-- Entitlement reads are side-effect free; duplicate-key repair is an owner-only,
-- previewable, audited action with an explicit keep-selection policy
-- (migration 20261126120000_entitlement_read_side_effect_free.sql).

create table if not exists public.owner_license_actions (
    id                bigserial primary key,
    acted_at          timestamptz not null default now(),
    actor_email       text not null default '',
    machine_id        text not null,
    action            text not null,
    keep_license_id   text not null default '',
    revoked_license_ids text not null default '[]'::text,
    detail            text not null default ''
);
alter table public.owner_license_actions enable row level security;

-- Append-only: the audit record of an owner action must never be rewritten or
-- deleted (UPDATE/DELETE/TRUNCATE are rejected). Corrections are new events.
create or replace function public.owner_license_actions_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception 'owner_license_actions is append-only: % is not permitted', tg_op
        using errcode = 'restrict_violation';
end;
$$;
drop trigger if exists owner_license_actions_no_mutation on public.owner_license_actions;
create trigger owner_license_actions_no_mutation
    before update or delete on public.owner_license_actions
    for each row execute function public.owner_license_actions_append_only();
drop trigger if exists owner_license_actions_no_truncate on public.owner_license_actions;
create trigger owner_license_actions_no_truncate
    before truncate on public.owner_license_actions
    for each statement execute function public.owner_license_actions_append_only();

create or replace function public.owner_review_duplicate_machine_keys(
    p_machine_id text,
    p_action text default 'preview',
    p_keep_license_id text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_action text := lower(trim(coalesce(p_action, 'preview')));
    v_keep text := upper(trim(coalesce(p_keep_license_id, '')));
    v_keys jsonb;
    v_revoked_ids text[] := '{}';
    v_row record;
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner may review duplicate machine keys.' using errcode = '42501';
    end if;
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;
    if v_action not in ('preview', 'resolve') then
        raise exception 'p_action must be preview or resolve.' using errcode = '22023';
    end if;

    select coalesce(jsonb_agg(jsonb_build_object(
        'license_id', k.license_id, 'key_type', k.key_type, 'customer', k.customer,
        'expires_at', k.expires_at, 'revoked', k.revoked, 'created_at', k.created_at
    ) order by k.created_at), '[]'::jsonb)
    into v_keys
    from public.issued_machine_keys k
    where upper(trim(k.machine_id)) = v_mid and k.key_type in ('license', 'combo')
      and k.expires_at >= current_date;

    if v_action = 'preview' or jsonb_array_length(v_keys) <= 1 then
        return jsonb_build_object('ok', true, 'machine_id', v_mid, 'duplicate_keys', v_keys,
            'action', 'none', 'revoked_license_ids', '[]'::text);
    end if;

    -- Explicit selection policy: the owner names exactly one licence to keep;
    -- every other unexpired base key for this Machine ID is revoked.
    if v_keep = '' or not exists (
        select 1 from jsonb_array_elements(v_keys) x where upper(trim(x->>'license_id')) = v_keep
    ) then
        raise exception 'Resolve requires p_keep_license_id with one of the listed license_ids.' using errcode = '22023';
    end if;

    perform pg_advisory_xact_lock(hashtextextended(v_mid, 0));

    for v_row in
        update public.issued_machine_keys k
        set revoked = true,
            revoke_reason = 'Duplicate Machine ID: owner review (kept ' || v_keep || ')'
        where upper(trim(k.machine_id)) = v_mid and k.key_type in ('license', 'combo')
          and k.expires_at >= current_date and not k.revoked
          and upper(trim(k.license_id)) <> v_keep
        returning k.license_id
    loop
        v_revoked_ids := array_append(v_revoked_ids, v_row.license_id);
    end loop;

    insert into public.owner_license_actions (actor_email, machine_id, action, keep_license_id, revoked_license_ids, detail)
    values (coalesce(auth.jwt()->>'email',''), v_mid, 'resolve_duplicates', v_keep,
            array_to_json(v_revoked_ids)::text, 'Owner-reviewed duplicate repair');

    return jsonb_build_object('ok', true, 'machine_id', v_mid, 'duplicate_keys', v_keys,
        'action', 'resolved', 'keep_license_id', v_keep, 'revoked_license_ids', v_revoked_ids);
end;
$$;
revoke all on function public.owner_review_duplicate_machine_keys(text, text, text) from public, anon, authenticated;
grant execute on function public.owner_review_duplicate_machine_keys(text, text, text) to authenticated;

-- Rate-limit public request creation and require valid details. Requests remain
-- owner-approved; the UUID is returned only to the submitting client.
create or replace function public.submit_activation_request(
    p_machine_id text,
    p_shop_id text default '',
    p_shop_name text default '',
    p_station_name text default '',
    p_requested_kind text default 'license',
    p_contact_phone text default ''
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_req_id uuid;
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;
    perform public.enforce_machine_rpc_rate_limit('submit_activation_request', v_mid, 5, 3600);
    insert into public.activation_requests(machine_id, shop_id, shop_name, station_name, requested_kind, contact_phone)
    values (v_mid, left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40),
        left(trim(coalesce(p_shop_name, '')), 80), left(trim(coalesce(p_station_name, '')), 40),
        case when p_requested_kind in ('license', 'pdf_unlock', 'combo') then p_requested_kind else 'license' end,
        left(trim(coalesce(p_contact_phone, '')), 40))
    returning id into v_req_id;
    return jsonb_build_object('ok', true, 'request_id', v_req_id, 'machine_id', v_mid);
end;
$$;

create or replace function public.submit_license_contact_request(
    p_machine_id text, p_contact_name text, p_company text, p_contact_phone text,
    p_station_name text default ''
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_req_id uuid;
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;
    if trim(coalesce(p_contact_name, '')) = '' or trim(coalesce(p_company, '')) = ''
       or length(regexp_replace(coalesce(p_contact_phone, ''), '[^0-9]', '', 'g')) < 7 then
        return jsonb_build_object('ok', false, 'error', 'Name, company and a valid phone number are required.');
    end if;
    perform public.enforce_machine_rpc_rate_limit('submit_license_contact_request', v_mid, 3, 3600);
    insert into public.activation_requests(machine_id, shop_name, contact_name, contact_phone, station_name, requested_kind)
    values (v_mid, left(trim(p_company), 80), left(trim(p_contact_name), 80),
        left(trim(p_contact_phone), 40), left(trim(coalesce(p_station_name, '')), 40), 'license')
    returning id into v_req_id;
    return jsonb_build_object('ok', true, 'request_id', v_req_id, 'machine_id', v_mid);
end;
$$;

-- Keep arbitrary anonymous clients from inventing telemetry for unlicensed PCs;
-- the packaged app's issued license row is the server-side eligibility check.
create or replace function public.record_workspace_telemetry(
    p_machine_id text,
    p_shop_id text default '',
    p_shop_name text default '',
    p_station_name text default '',
    p_city_label text default '',
    p_app_version text default '',
    p_os_platform text default '',
    p_current_screen text default 'Home',
    p_screen_counts jsonb default '{}'::jsonb,
    p_online_seconds integer default 0
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_bname text := left(trim(coalesce(p_shop_name, 'Café Desk Station')), 80);
    v_stat text := left(trim(coalesce(p_station_name, 'Counter PC 1')), 40);
    v_city text := left(trim(coalesce(p_city_label, '')), 60);
    v_ver text := left(trim(coalesce(p_app_version, '')), 24);
    v_os text := left(trim(coalesce(p_os_platform, '')), 32);
    v_cur text := left(trim(coalesce(p_current_screen, 'Home')), 48);
    v_online integer := least(greatest(coalesce(p_online_seconds, 0), 0), 86400);
    v_key text;
    v_val integer;
    v_added bigint := 0;
    v_allowed constant text[] := array[
        'Home', 'Price list', 'PDF tools', 'Bluetooth files', 'Scan & images',
        'A4 picture sheets', 'Career documents', 'Cards & invitations', 'Forms creator',
        'Receipts', 'Sales reports', 'Price settings', 'Business settings',
        'AI settings', 'Projects & recovery', 'About & contact', 'Help & privacy'
    ];
begin
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        return jsonb_build_object('ok', false, 'error', 'Invalid Machine ID format.');
    end if;
    if not exists (select 1 from public.issued_machine_keys k where upper(trim(k.machine_id))=v_mid
        and k.key_type in ('license','combo') and not k.revoked and k.expires_at >= current_date) then
        return jsonb_build_object('ok', true, 'recorded_visits', 0, 'ignored', true);
    end if;
    perform public.enforce_machine_rpc_rate_limit('record_workspace_telemetry', v_mid, 120, 60);
    if not (v_cur = any(v_allowed)) then v_cur := 'Home'; end if;
    if jsonb_typeof(p_screen_counts) = 'object' then
        for v_key, v_val in
            select key, least(greatest(coalesce(nullif(regexp_replace(value::text, '[^0-9]', '', 'g'), '')::integer, 0), 0), 5000)
            from jsonb_each_text(p_screen_counts)
        loop
            if v_val > 0 and (v_key = any(v_allowed)) then
                insert into public.workspace_tool_visits(visit_date,machine_id,shop_id,shop_name,city_label,screen_name,visit_count,last_visited_at)
                values (current_date,v_mid,v_shop,v_bname,v_city,v_key,v_val,now())
                on conflict (visit_date,machine_id,screen_name) do update set
                    visit_count=public.workspace_tool_visits.visit_count+excluded.visit_count,
                    shop_id=case when excluded.shop_id<>'' then excluded.shop_id else public.workspace_tool_visits.shop_id end,
                    shop_name=case when excluded.shop_name<>'' then excluded.shop_name else public.workspace_tool_visits.shop_name end,
                    city_label=case when excluded.city_label<>'' then excluded.city_label else public.workspace_tool_visits.city_label end,
                    last_visited_at=now();
                v_added := v_added + v_val;
            end if;
        end loop;
    end if;
    insert into public.station_heartbeats(machine_id,shop_id,shop_name,station_name,city_label,app_version,os_platform,current_screen,total_visits,online_seconds,first_seen_at,last_seen_at)
    values(v_mid,v_shop,v_bname,v_stat,v_city,v_ver,v_os,v_cur,greatest(v_added,1),v_online,now(),now())
    on conflict (machine_id) do update set
        shop_id=case when excluded.shop_id<>'' then excluded.shop_id else public.station_heartbeats.shop_id end,
        shop_name=case when excluded.shop_name<>'' then excluded.shop_name else public.station_heartbeats.shop_name end,
        station_name=case when excluded.station_name<>'' then excluded.station_name else public.station_heartbeats.station_name end,
        city_label=case when excluded.city_label<>'' then excluded.city_label else public.station_heartbeats.city_label end,
        app_version=excluded.app_version, os_platform=excluded.os_platform, current_screen=excluded.current_screen,
        total_visits=public.station_heartbeats.total_visits+greatest(v_added,0),
        online_seconds=public.station_heartbeats.online_seconds+excluded.online_seconds,
        last_seen_at=now();
    if v_online > 0 then
        insert into public.station_online_daily(visit_date,machine_id,shop_id,shop_name,city_label,station_name,online_seconds,last_online_at)
        values(current_date,v_mid,v_shop,v_bname,v_city,v_stat,v_online,now())
        on conflict (visit_date,machine_id) do update set
            online_seconds=public.station_online_daily.online_seconds+excluded.online_seconds,
            shop_id=case when excluded.shop_id<>'' then excluded.shop_id else public.station_online_daily.shop_id end,
            shop_name=case when excluded.shop_name<>'' then excluded.shop_name else public.station_online_daily.shop_name end,
            city_label=case when excluded.city_label<>'' then excluded.city_label else public.station_online_daily.city_label end,
            station_name=case when excluded.station_name<>'' then excluded.station_name else public.station_online_daily.station_name end,
            last_online_at=now();
    end if;
    return jsonb_build_object('ok', true, 'recorded_visits', v_added, 'online_seconds', v_online);
end;
$$;

notify pgrst, 'reload schema';
-- Extend the owner purge to erase the hashed per-machine RPC throttle state too.
-- The hash is not a raw Machine ID, but it remains linkable to that ID.
create or replace function public.owner_purge_machine_records(p_machine_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_keys integer := 0;
    v_requests integer := 0;
    v_heartbeats integer := 0;
    v_visits integer := 0;
    v_online_rows integer := 0;
    v_rate_hashes integer := 0;
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner may delete machine records.' using errcode = '42501';
    end if;
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;

    delete from public.issued_machine_keys where upper(trim(machine_id)) = v_mid;
    get diagnostics v_keys = row_count;
    delete from public.activation_requests where upper(trim(machine_id)) = v_mid;
    get diagnostics v_requests = row_count;
    delete from public.workspace_tool_visits where upper(trim(machine_id)) = v_mid;
    get diagnostics v_visits = row_count;
    delete from public.station_heartbeats where upper(trim(machine_id)) = v_mid;
    get diagnostics v_heartbeats = row_count;
    delete from public.station_online_daily where upper(trim(machine_id)) = v_mid;
    get diagnostics v_online_rows = row_count;
    delete from public.machine_rpc_rate_limits
    where machine_hash = encode(digest(v_mid, 'sha256'), 'hex');
    get diagnostics v_rate_hashes = row_count;

    return jsonb_build_object(
        'ok', true,
        'machine_id', v_mid,
        'deleted', jsonb_build_object(
            'issued_machine_keys', v_keys,
            'activation_requests', v_requests,
            'workspace_tool_visits', v_visits,
            'station_heartbeats', v_heartbeats,
            'station_online_daily', v_online_rows,
            'machine_rpc_rate_limits', v_rate_hashes
        )
    );
end;
$$;
revoke all on function public.owner_purge_machine_records(text) from public, anon;
grant execute on function public.owner_purge_machine_records(text) to authenticated;
notify pgrst, 'reload schema';

-- Server-enforced MFA (AAL2) and append-only audit trail for owner operations
-- (migration 20261126160000_owner_mfa_aal2_enforcement.sql).
create table if not exists public.owner_audit_log (
    id              bigserial primary key,
    acted_at        timestamptz not null default now(),
    actor_user_id   text not null default '',
    actor_email     text not null default '',
    actor_aal       text not null default '',
    action          text not null,
    target_type     text not null default '',
    target_id       text not null default '',
    detail          jsonb not null default '{}'::jsonb
);
alter table public.owner_audit_log enable row level security;

-- Explicit privileges: do not depend on project-specific default grants.
revoke all on public.owner_audit_log from anon, authenticated;
grant select on public.owner_audit_log to authenticated;
grant select, insert on public.owner_audit_log to service_role;
grant usage, select on sequence public.owner_audit_log_id_seq to service_role;

drop policy if exists "Owner read access to owner_audit_log" on public.owner_audit_log;
create policy "Owner read access to owner_audit_log"
    on public.owner_audit_log for select to authenticated
    using (public.is_cafedesk_owner());

drop policy if exists "Owner read access to owner_license_actions" on public.owner_license_actions;
create policy "Owner read access to owner_license_actions"
    on public.owner_license_actions for select to authenticated
    using (public.is_cafedesk_owner());

create or replace function public.owner_audit_log_append_only()
returns trigger
language plpgsql
as $$
begin
    raise exception 'owner_audit_log is append-only: % is not permitted', tg_op
        using errcode = 'restrict_violation';
end;
$$;

drop trigger if exists owner_audit_log_no_mutation on public.owner_audit_log;
create trigger owner_audit_log_no_mutation
    before update or delete on public.owner_audit_log
    for each row execute function public.owner_audit_log_append_only();

drop trigger if exists owner_audit_log_no_truncate on public.owner_audit_log;
create trigger owner_audit_log_no_truncate
    before truncate on public.owner_audit_log
    for each statement execute function public.owner_audit_log_append_only();

create or replace function public.audit_owner_table_mutation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_record jsonb := case when tg_op = 'DELETE' then to_jsonb(old) else to_jsonb(new) end;
    v_target_id text := coalesce(
        v_record ->> 'license_id',
        v_record ->> 'version',
        v_record ->> 'slug',
        v_record ->> 'id',
        ''
    );
begin
    if coalesce(auth.role(), '') = 'authenticated' then
        insert into public.owner_audit_log (
            actor_user_id,
            actor_email,
            actor_aal,
            action,
            target_type,
            target_id,
            detail
        )
        values (
            coalesce(auth.jwt() ->> 'sub', ''),
            coalesce(auth.jwt() ->> 'email', ''),
            coalesce(auth.jwt() ->> 'aal', ''),
            lower(tg_op) || '_' || tg_table_name,
            tg_table_name,
            v_target_id,
            jsonb_build_object('op', tg_op)
        );
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;
revoke all on function public.audit_owner_table_mutation() from public, anon, authenticated;

drop trigger if exists trg_audit_owner_app_releases on public.app_releases;
create trigger trg_audit_owner_app_releases
    after insert or update or delete on public.app_releases
    for each row execute function public.audit_owner_table_mutation();

drop trigger if exists trg_audit_owner_app_announcements on public.app_announcements;
create trigger trg_audit_owner_app_announcements
    after insert or update or delete on public.app_announcements
    for each row execute function public.audit_owner_table_mutation();

drop trigger if exists trg_audit_owner_issued_machine_keys on public.issued_machine_keys;
create trigger trg_audit_owner_issued_machine_keys
    after insert or update or delete on public.issued_machine_keys
    for each row execute function public.audit_owner_table_mutation();

drop trigger if exists trg_audit_owner_activation_requests on public.activation_requests;
create trigger trg_audit_owner_activation_requests
    after insert or update or delete on public.activation_requests
    for each row execute function public.audit_owner_table_mutation();

do $$
begin
    if to_regclass('public.templates') is not null then
        execute 'drop trigger if exists trg_audit_owner_templates on public.templates';
        execute 'create trigger trg_audit_owner_templates
            after insert or update or delete on public.templates
            for each row execute function public.audit_owner_table_mutation()';
    end if;
end;
$$;

create or replace function public.owner_register_shop_sync_profile(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_business_name text default 'Internet Café'
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_hash text := lower(trim(coalesce(p_sync_key_sha256, '')));
    v_name text := left(trim(coalesce(p_business_name, 'Internet Café')), 80);
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner with verified MFA (AAL2) may register a shop.' using errcode = '42501';
    end if;
    if length(v_shop) < 3 or v_hash !~ '^[0-9a-f]{64}$' then
        raise exception 'Enter a valid Shop ID and a valid 64-character passcode hash.' using errcode = '22023';
    end if;
    if exists (select 1 from public.shop_sync_profiles where shop_id = v_shop) then
        return jsonb_build_object('ok', false, 'error', 'That Shop ID is already registered. It was not changed.');
    end if;

    insert into public.shop_sync_profiles(shop_id, sync_key_sha256, business_name, currency, prices_json, updated_by)
    values (v_shop, v_hash, v_name, 'R', '[]'::jsonb, 'Owner registration');

    insert into public.owner_audit_log (
        actor_user_id, actor_email, actor_aal, action, target_type, target_id, detail
    )
    values (
        coalesce(auth.jwt() ->> 'sub', ''),
        coalesce(auth.jwt() ->> 'email', ''),
        coalesce(auth.jwt() ->> 'aal', ''),
        'register_shop_sync_profile',
        'shop_sync_profiles',
        v_shop,
        jsonb_build_object('business_name', v_name)
    );

    return jsonb_build_object('ok', true, 'shop_id', v_shop);
end;
$$;
revoke all on function public.owner_register_shop_sync_profile(text, text, text) from public, anon;
grant execute on function public.owner_register_shop_sync_profile(text, text, text) to authenticated;

create or replace function public.owner_review_duplicate_machine_keys(
    p_machine_id text,
    p_action text default 'preview',
    p_keep_license_id text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_action text := lower(trim(coalesce(p_action, 'preview')));
    v_keep text := upper(trim(coalesce(p_keep_license_id, '')));
    v_keys jsonb;
    v_revoked_ids text[] := '{}';
    v_row record;
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner with verified MFA (AAL2) may review duplicate machine keys.' using errcode = '42501';
    end if;
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;
    if v_action not in ('preview', 'resolve') then
        raise exception 'p_action must be preview or resolve.' using errcode = '22023';
    end if;

    select coalesce(jsonb_agg(jsonb_build_object(
        'license_id', k.license_id, 'key_type', k.key_type, 'customer', k.customer,
        'expires_at', k.expires_at, 'revoked', k.revoked, 'created_at', k.created_at
    ) order by k.created_at), '[]'::jsonb)
    into v_keys
    from public.issued_machine_keys k
    where upper(trim(k.machine_id)) = v_mid and k.key_type in ('license', 'combo')
      and not k.revoked and k.expires_at >= current_date;

    if v_action = 'preview' or jsonb_array_length(v_keys) <= 1 then
        return jsonb_build_object(
            'ok', true, 'machine_id', v_mid, 'action', 'none',
            'duplicate_keys', v_keys, 'revoked_license_ids', '[]'::jsonb
        );
    end if;

    perform pg_advisory_xact_lock(hashtext('cafedesk-dup:' || v_mid));

    if v_keep = '' or not exists (
        select 1 from public.issued_machine_keys k
        where upper(trim(k.machine_id)) = v_mid and upper(trim(k.license_id)) = v_keep
          and k.key_type in ('license', 'combo') and not k.revoked and k.expires_at >= current_date
    ) then
        raise exception 'p_keep_license_id must name one active base/combo licence on %.', v_mid
            using errcode = '22023';
    end if;

    for v_row in
        select id, license_id from public.issued_machine_keys k
        where upper(trim(k.machine_id)) = v_mid and upper(trim(k.license_id)) <> v_keep
          and k.key_type in ('license', 'combo') and not k.revoked and k.expires_at >= current_date
        for update
    loop
        update public.issued_machine_keys
        set revoked = true,
            revoke_reason = 'Resolved duplicate Machine ID in favour of ' || v_keep
        where id = v_row.id;
        v_revoked_ids := array_append(v_revoked_ids, v_row.license_id);
    end loop;

    insert into public.owner_license_actions(actor_email, machine_id, action, keep_license_id, revoked_license_ids, detail)
    values (coalesce(auth.jwt()->>'email',''), v_mid, 'resolve_duplicates', v_keep,
            to_jsonb(v_revoked_ids)::text, 'Owner resolved duplicate active base/combo licences (AAL2)');

    insert into public.owner_audit_log (
        actor_user_id, actor_email, actor_aal, action, target_type, target_id, detail
    )
    values (
        coalesce(auth.jwt() ->> 'sub', ''),
        coalesce(auth.jwt() ->> 'email', ''),
        coalesce(auth.jwt() ->> 'aal', ''),
        'resolve_duplicate_machine_keys',
        'issued_machine_keys',
        v_mid,
        jsonb_build_object('keep_license_id', v_keep, 'revoked_license_ids', to_jsonb(v_revoked_ids))
    );

    return jsonb_build_object(
        'ok', true, 'machine_id', v_mid, 'action', 'resolved',
        'keep_license_id', v_keep, 'revoked_license_ids', to_jsonb(v_revoked_ids)
    );
end;
$$;
revoke all on function public.owner_review_duplicate_machine_keys(text, text, text) from public, anon;
grant execute on function public.owner_review_duplicate_machine_keys(text, text, text) to authenticated;

create or replace function public.owner_purge_machine_records(p_machine_id text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_mid text := upper(trim(coalesce(p_machine_id, '')));
    v_keys integer := 0;
    v_requests integer := 0;
    v_heartbeats integer := 0;
    v_visits integer := 0;
    v_online_rows integer := 0;
    v_rate_hashes integer := 0;
    v_deleted jsonb;
begin
    if not public.is_cafedesk_owner() then
        raise exception 'Only the Café Desk owner with verified MFA (AAL2) may delete machine records.' using errcode = '42501';
    end if;
    if v_mid !~ '^CD-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$' then
        raise exception 'Invalid Machine ID format.' using errcode = '22023';
    end if;

    delete from public.issued_machine_keys where upper(trim(machine_id)) = v_mid;
    get diagnostics v_keys = row_count;
    delete from public.activation_requests where upper(trim(machine_id)) = v_mid;
    get diagnostics v_requests = row_count;
    delete from public.workspace_tool_visits where upper(trim(machine_id)) = v_mid;
    get diagnostics v_visits = row_count;
    delete from public.station_heartbeats where upper(trim(machine_id)) = v_mid;
    get diagnostics v_heartbeats = row_count;
    delete from public.station_online_daily where upper(trim(machine_id)) = v_mid;
    get diagnostics v_online_rows = row_count;
    delete from public.machine_rpc_rate_limits
    where machine_hash = encode(digest(v_mid, 'sha256'), 'hex');
    get diagnostics v_rate_hashes = row_count;

    v_deleted := jsonb_build_object(
        'issued_machine_keys', v_keys,
        'activation_requests', v_requests,
        'workspace_tool_visits', v_visits,
        'station_heartbeats', v_heartbeats,
        'station_online_daily', v_online_rows,
        'machine_rpc_rate_limits', v_rate_hashes
    );

    insert into public.owner_audit_log (
        actor_user_id, actor_email, actor_aal, action, target_type, target_id, detail
    )
    values (
        coalesce(auth.jwt() ->> 'sub', ''),
        coalesce(auth.jwt() ->> 'email', ''),
        coalesce(auth.jwt() ->> 'aal', ''),
        'purge_machine_records',
        'machine',
        v_mid,
        v_deleted
    );

    return jsonb_build_object(
        'ok', true,
        'machine_id', v_mid,
        'deleted', v_deleted
    );
end;
$$;
revoke all on function public.owner_purge_machine_records(text) from public, anon;
grant execute on function public.owner_purge_machine_records(text) to authenticated;
notify pgrst, 'reload schema';


-- Complete, keyset-paginated full-history reconciliation, including old voids.
-- Re-scan every run deliberately: a creation-date watermark loses later voids.
-- No live migration is applied by the desktop app.
create index if not exists idx_shop_sync_sales_cursor
    on public.shop_sync_sales(shop_id, sale_id collate "C");

-- The local ledger supports up to 500 lines x 999 units x 10,000,000 currency.
-- Integer cents overflow at ordinary valid values; bigint covers the same range.
alter table public.shop_sync_sales alter column total_cents type bigint;

-- Canonical semantic identity, shared by uploads and comparisons with legacy rows.
-- Formatting differences ("1" vs 1 units; 1 vs "1.00" price) are not new sales.
create or replace function public.normalize_shop_sale_items(p_items jsonb)
returns jsonb language plpgsql immutable set search_path = public as $$
declare
    v_line jsonb;
    v_qty numeric;
    v_price numeric;
    v_items jsonb := '[]'::jsonb;
    v_name text;
begin
    if jsonb_typeof(p_items) is distinct from 'array' then
        raise exception 'Service lines must be an array.';
    end if;
    if jsonb_array_length(p_items) not between 1 and 500 then
        raise exception 'A sale must contain between 1 and 500 service lines.';
    end if;
    for v_line in select value from jsonb_array_elements(p_items) loop
        v_name := regexp_replace(v_line->>'name', '^[[:space:]]+|[[:space:]]+$', '', 'g');
        if jsonb_typeof(v_line) is distinct from 'object'
           or jsonb_typeof(v_line->'name') is distinct from 'string'
           or length(v_name) not between 1 and 80
           or coalesce(v_line->>'qty', '') !~ '^[0-9]+$'
           or coalesce(v_line->>'price', '') !~ '^-?[0-9]+([.][0-9]{1,2})?$' then
            raise exception 'Invalid service name, integer quantity or price.';
        end if;
        v_qty := (v_line->>'qty')::numeric;
        v_price := (v_line->>'price')::numeric;
        if v_qty not between 1 and 999 or v_price not between 0 and 10000000 then
            raise exception 'Service quantity or price is out of range.';
        end if;
        v_items := v_items || jsonb_build_array(jsonb_build_object(
            'name',v_name,'qty',v_qty::integer,
            'price',to_char(v_price,'FM999999990.00')));
    end loop;
    return v_items;
end;
$$;
revoke all on function public.normalize_shop_sale_items(jsonb) from public,anon,authenticated;

create or replace function public.sync_shop_sales_v2(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_sales jsonb default '[]'::jsonb,
    p_after text default '',
    p_read boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_shop text := left(regexp_replace(upper(coalesce(p_shop_id, '')), '[^A-Z0-9_-]', '', 'g'), 40);
    v_hash text;
    v_item jsonb;
    v_old public.shop_sync_sales%rowtype;
    v_total numeric;
    v_items jsonb;
    v_pushed integer := 0;
    v_rows jsonb;
    v_after text;
begin
    select sync_key_sha256 into v_hash from public.shop_sync_profiles where shop_id = v_shop;
    if v_hash is null or v_hash <> coalesce(p_sync_key_sha256, '') then
        return jsonb_build_object('ok', false, 'error', 'Shop ID or Sync Passcode is incorrect. Initialize the shop price list first.');
    end if;
    if jsonb_typeof(p_sales) is distinct from 'array' or jsonb_array_length(p_sales) > 250 then
        raise exception 'Supply an array of at most 250 sales per upload.';
    end if;
    -- Serialize uploads per shop so conflict checking and insert/void are atomic.
    if jsonb_array_length(p_sales) > 0 then
        perform pg_advisory_xact_lock(hashtextextended('sales-sync|' || v_shop, 0));
    end if;
    for v_item in select value from jsonb_array_elements(p_sales) loop
        if jsonb_typeof(v_item) is distinct from 'object'
           or jsonb_typeof(v_item->'sale_id') is distinct from 'string'
           or length(trim(v_item->>'sale_id')) not between 1 and 64
           or length(coalesce(v_item->>'station', '')) > 40
           or length(coalesce(v_item->>'business', '')) not between 1 and 80
           or length(coalesce(v_item->>'currency', '')) not between 1 and 8
           or (v_item->>'currency') ~ E'[\\n\\r|]'
           or length(coalesce(v_item->>'created_at', '')) not between 10 and 40
           or (v_item->>'created_at') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}($|[T ])'
           or jsonb_typeof(v_item->'items') is distinct from 'array' then
            raise exception 'Invalid sale identity, metadata or service lines.';
        end if;
        perform (v_item->>'created_at')::timestamp with time zone;
        if nullif(v_item->>'voided_at', '') is not null then
            if length(v_item->>'voided_at') not between 10 and 40
               or (v_item->>'voided_at') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}($|[T ])' then
                raise exception 'Invalid void timestamp.';
            end if;
            perform (v_item->>'voided_at')::timestamp with time zone;
        end if;
        v_items := public.normalize_shop_sale_items(v_item->'items');
        select sum((line->>'qty')::numeric * (line->>'price')::numeric * 100)
            into v_total from jsonb_array_elements(v_items) line;
        if jsonb_typeof(v_item->'total_cents') is distinct from 'number'
           or v_total <> (v_item->>'total_cents')::numeric
           or v_total > 499500000000000 then
            raise exception 'Sale total does not match service lines or exceeds the cloud ledger limit.';
        end if;
        select * into v_old from public.shop_sync_sales
            where shop_id = v_shop and sale_id = v_item->>'sale_id';
        if found then
            if row(v_old.created_at, v_old.station, v_old.business, v_old.currency, v_old.total_cents, public.normalize_shop_sale_items(v_old.items_json))
               is distinct from row(v_item->>'created_at', coalesce(v_item->>'station', ''),
                   v_item->>'business', v_item->>'currency', v_total::bigint, v_items) then
                raise exception 'Sale reference conflicts with immutable recorded details: %', v_item->>'sale_id';
            end if;
            if v_old.voided_at is null and nullif(v_item->>'voided_at', '') is not null then
                update public.shop_sync_sales set voided_at = v_item->>'voided_at',
                    void_reason = left(coalesce(v_item->>'void_reason', 'Voided on shop PC'), 160), synced_at = now()
                    where shop_id = v_shop and sale_id = v_old.sale_id;
            end if;
        else
            insert into public.shop_sync_sales(shop_id,sale_id,created_at,station,business,currency,total_cents,items_json,voided_at,void_reason)
            values(v_shop,v_item->>'sale_id',v_item->>'created_at',coalesce(v_item->>'station',''),
                   v_item->>'business',v_item->>'currency',v_total::bigint,v_items,
                   nullif(v_item->>'voided_at',''),left(v_item->>'void_reason',160));
        end if;
        v_pushed := v_pushed + 1;
    end loop;
    select coalesce(jsonb_agg(jsonb_build_object(
        'sale_id',s.sale_id,'created_at',s.created_at,'station',s.station,'business',s.business,
        'currency',s.currency,'total_cents',s.total_cents,'items',s.items_json,
        'voided_at',s.voided_at,'void_reason',s.void_reason
    ) order by s.sale_id collate "C"), '[]'::jsonb), max(s.sale_id collate "C") into v_rows,v_after
    from (select * from public.shop_sync_sales
          where p_read and shop_id=v_shop and sale_id collate "C" > coalesce(p_after,'') collate "C"
          order by sale_id collate "C" limit 250) s;
    return jsonb_build_object('ok',true,'protocol',2,'shop_id',v_shop,
        'pushed_count',v_pushed,'sales',v_rows,'next_after',v_after);
end;
$$;
revoke all on function public.sync_shop_sales_v2(text,text,jsonb,text,boolean) from public;
grant execute on function public.sync_shop_sales_v2(text,text,jsonb,text,boolean) to anon,authenticated;

-- Fail explicitly for older clients rather than silently returning only 2,000 rows.
create or replace function public.sync_shop_sales(
    p_shop_id text, p_sync_key_sha256 text, p_sales jsonb default '[]'::jsonb, p_since text default ''
) returns jsonb language sql security definer set search_path = public as $$
    select jsonb_build_object('ok',false,'error','Upgrade Cafe Desk on every shop PC for complete sales synchronization (protocol 2).');
$$;

revoke all on function public.sync_shop_sales(text,text,jsonb,text) from public;
grant execute on function public.sync_shop_sales(text,text,jsonb,text) to anon,authenticated;
