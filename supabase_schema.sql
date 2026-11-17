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
    version         text primary key,
    download_url    text not null,
    sha256          text not null,
    signature       text not null default '', -- Ed25519 sig over CAFE-DESK-RELEASE-V1:{version}:{sha256}
    release_notes   text not null default '',
    mandatory       boolean not null default false,
    min_os          text not null default 'Windows 10+',
    published_at    timestamptz not null default now()
);

-- Migration for projects created before release signing (safe to re-run):
alter table public.app_releases add column if not exists signature text not null default '';

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

-- Controlled RPC: A customer PC can ONLY fetch non-revoked keys matching its exact Machine ID,
-- and check whether its specific license_id has been remotely revoked by the owner.
create or replace function public.check_machine_entitlements(p_machine_id text)
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

    select coalesce(jsonb_agg(jsonb_build_object(
        'license_id', k.license_id,
        'key_type', k.key_type,
        'shop_id', k.shop_id,
        'customer', k.customer,
        'seat_no', k.seat_no,
        'seat_max', k.seat_max,
        'seat_label', k.seat_label,
        'expires_at', k.expires_at,
        'token', k.token
    ) order by k.created_at desc), '[]'::jsonb)
    into v_keys
    from public.issued_machine_keys k
    where k.machine_id = v_mid
      and k.revoked = false
      and k.expires_at >= current_date;

    select coalesce(jsonb_agg(jsonb_build_object(
        'license_id', r.license_id,
        'reason', r.revoke_reason
    )), '[]'::jsonb)
    into v_revoked
    from public.issued_machine_keys r
    where r.machine_id = v_mid
      and r.revoked = true;

    return jsonb_build_object(
        'ok', true,
        'machine_id', v_mid,
        'active_keys', v_keys,
        'revoked_keys', v_revoked
    );
end;
$$;

grant execute on function public.check_machine_entitlements(text) to anon, authenticated;

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
-- Keep this digest in sync with owner-auth.js if the owner account ever changes.
create or replace function public.cafedesk_owner_digest()
returns text
language sql
immutable
as $$
    select '72f63f593e8b7db71539bdb0c9ed04567a072b946102b1f353a049870b9ba1bc';
$$;

create or replace function public.is_cafedesk_owner()
returns boolean
language sql
stable
as $$
    select coalesce(
        encode(
            digest('CAFE-DESK-OWNER-V1|' || lower(coalesce(auth.jwt() ->> 'email', '')), 'sha256'),
            'hex'
        ) = public.cafedesk_owner_digest(),
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
    first_seen_at   timestamptz not null default now(),
    last_seen_at    timestamptz not null default now()
);

create index if not exists idx_station_heartbeats_seen on public.station_heartbeats (last_seen_at desc);
create index if not exists idx_station_heartbeats_city on public.station_heartbeats (city_label);

alter table public.station_heartbeats enable row level security;

drop policy if exists "Owner full access to station_heartbeats" on public.station_heartbeats;
create policy "Owner full access to station_heartbeats"
    on public.station_heartbeats for all to authenticated
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
    p_screen_counts jsonb default '{}'::jsonb
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
        app_version, os_platform, current_screen, total_visits, first_seen_at, last_seen_at
    ) values (
        v_mid, v_shop, v_bname, v_stat, v_city,
        v_ver, v_os, v_cur, greatest(v_added, 1), now(), now()
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
        last_seen_at = now();

    return jsonb_build_object('ok', true, 'recorded_visits', v_added);
end;
$$;

grant execute on function public.record_workspace_telemetry(text, text, text, text, text, text, text, text, jsonb) to anon, authenticated;
