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

    if (select count(*) from public.issued_machine_keys k
        where upper(trim(k.machine_id)) = v_mid and k.key_type in ('license', 'combo')
          and k.expires_at >= current_date) > 1 then
        update public.issued_machine_keys set revoked = true,
            revoke_reason = 'Duplicate Machine ID: contact the owner for review'
        where upper(trim(machine_id)) = v_mid and expires_at >= current_date and not revoked;
    end if;

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
    p_screen_counts jsonb default '{}'::jsonb
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
    insert into public.station_heartbeats(machine_id,shop_id,shop_name,station_name,city_label,app_version,os_platform,current_screen,total_visits,first_seen_at,last_seen_at)
    values(v_mid,v_shop,v_bname,v_stat,v_city,v_ver,v_os,v_cur,greatest(v_added,1),now(),now())
    on conflict (machine_id) do update set
        shop_id=case when excluded.shop_id<>'' then excluded.shop_id else public.station_heartbeats.shop_id end,
        shop_name=case when excluded.shop_name<>'' then excluded.shop_name else public.station_heartbeats.shop_name end,
        station_name=case when excluded.station_name<>'' then excluded.station_name else public.station_heartbeats.station_name end,
        city_label=case when excluded.city_label<>'' then excluded.city_label else public.station_heartbeats.city_label end,
        app_version=excluded.app_version, os_platform=excluded.os_platform, current_screen=excluded.current_screen,
        total_visits=public.station_heartbeats.total_visits+greatest(v_added,0), last_seen_at=now();
    return jsonb_build_object('ok', true, 'recorded_visits', v_added);
end;
$$;

notify pgrst, 'reload schema';
