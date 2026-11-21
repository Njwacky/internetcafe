-- PC online-duration tracking (v0.8.3+)
-- Answers: "which PC stays online too much?" and "which PC uses the app most?"
-- alongside existing workspace_tool_visits page/tool counts.
--
-- Privacy: only duration seconds + the same station labels already sent with
-- heartbeats. Never filenames, document text, or customer data.

alter table public.station_heartbeats
    add column if not exists online_seconds bigint not null default 0;

create index if not exists idx_station_heartbeats_online
    on public.station_heartbeats (online_seconds desc);

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

create index if not exists idx_station_online_daily_date
    on public.station_online_daily (visit_date desc);
create index if not exists idx_station_online_daily_seconds
    on public.station_online_daily (online_seconds desc);

alter table public.station_online_daily enable row level security;

drop policy if exists "Owner full access to station_online_daily" on public.station_online_daily;
create policy "Owner full access to station_online_daily"
    on public.station_online_daily for all to authenticated
    using (public.is_cafedesk_owner()) with check (public.is_cafedesk_owner());

-- Replace the 9-arg telemetry RPC with a 10-arg version that also banks online
-- seconds. DROP first: PostgreSQL identifies functions by argument types, so
-- CREATE OR REPLACE alone would leave an ambiguous overload for PostgREST.
drop function if exists public.record_workspace_telemetry(text, text, text, text, text, text, text, text, jsonb);

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

    insert into public.station_heartbeats(
        machine_id,shop_id,shop_name,station_name,city_label,
        app_version,os_platform,current_screen,total_visits,
        online_seconds,first_seen_at,last_seen_at
    )
    values(v_mid,v_shop,v_bname,v_stat,v_city,v_ver,v_os,v_cur,greatest(v_added,1),v_online,now(),now())
    on conflict (machine_id) do update set
        shop_id=case when excluded.shop_id<>'' then excluded.shop_id else public.station_heartbeats.shop_id end,
        shop_name=case when excluded.shop_name<>'' then excluded.shop_name else public.station_heartbeats.shop_name end,
        station_name=case when excluded.station_name<>'' then excluded.station_name else public.station_heartbeats.station_name end,
        city_label=case when excluded.city_label<>'' then excluded.city_label else public.station_heartbeats.city_label end,
        app_version=excluded.app_version,
        os_platform=excluded.os_platform,
        current_screen=excluded.current_screen,
        total_visits=public.station_heartbeats.total_visits+greatest(v_added,0),
        online_seconds=public.station_heartbeats.online_seconds+excluded.online_seconds,
        last_seen_at=now();

    if v_online > 0 then
        insert into public.station_online_daily(
            visit_date,machine_id,shop_id,shop_name,city_label,station_name,online_seconds,last_online_at
        )
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

grant execute on function public.record_workspace_telemetry(text, text, text, text, text, text, text, text, jsonb, integer) to anon, authenticated;

notify pgrst, 'reload schema';

-- Delete PC online rows with the rest of the machine's telemetry records.
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
