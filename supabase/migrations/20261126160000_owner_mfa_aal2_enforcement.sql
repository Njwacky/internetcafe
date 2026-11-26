-- Server-enforced MFA (AAL2) and append-only audit trail for owner operations.
-- Enrolling TOTP alone is insufficient if RLS policies, SECURITY DEFINER RPCs,
-- or Edge Functions still accept single-factor (AAL1) tokens.

-- 1. Upgrade the central PostgreSQL owner gate to require BOTH the verified
--    owner e-mail digest AND Authenticator Assurance Level 2 (aal = 'aal2'),
--    plus optional server-managed app_metadata.cafedesk_role = 'owner'.
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

grant execute on function public.is_cafedesk_owner() to anon, authenticated;

-- 2. Gate release-bucket object deletion behind owner + AAL2 as well.
do $$
begin
    if to_regclass('storage.objects') is not null then
        execute 'drop policy if exists "Owner delete from cafedesk-releases" on storage.objects';
        execute 'create policy "Owner delete from cafedesk-releases"
            on storage.objects for delete to authenticated
            using (bucket_id = ''cafedesk-releases'' and public.is_cafedesk_owner())';
    end if;
end;
$$;

-- 3. Append-only owner security audit log (readable only by owner at AAL2;
--    appendable only via SECURITY DEFINER owner RPCs/triggers or service_role).
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

do $$
begin
    if to_regclass('public.owner_license_actions') is not null then
        execute 'drop policy if exists "Owner read access to owner_license_actions" on public.owner_license_actions';
        execute 'create policy "Owner read access to owner_license_actions"
            on public.owner_license_actions for select to authenticated
            using (public.is_cafedesk_owner())';
    end if;
end;
$$;

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

-- 4. Automatic audit trigger for direct authenticated owner mutations on
--    releases, announcements, and downloadable templates.
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

-- 5. Update owner SECURITY DEFINER RPCs to surface explicit AAL2 MFA errors
--    and write append-only audit records.
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
