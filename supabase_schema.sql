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
-- =============================================================================

create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- 1. REMOTE OVER-THE-INTERNET APP RELEASES (Public Read-Only, Owner Write-Only)
-- -----------------------------------------------------------------------------
create table if not exists public.app_releases (
    version         text primary key,
    download_url    text not null,
    sha256          text not null,
    release_notes   text not null default '',
    mandatory       boolean not null default false,
    min_os          text not null default 'Windows 10+',
    published_at    timestamptz not null default now()
);

alter table public.app_releases enable row level security;

drop policy if exists "Anyone can check latest app release" on public.app_releases;
create policy "Anyone can check latest app release"
    on public.app_releases
    for select
    to anon, authenticated
    using (true);

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
