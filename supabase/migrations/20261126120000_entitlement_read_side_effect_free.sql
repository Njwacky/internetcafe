-- Entitlement reads must be side-effect free; duplicate-key repair is an
-- owner-only, previewable, audited action with an explicit keep-selection policy.
--
-- Previously `check_machine_entitlements` (a public read RPC) revoked EVERY
-- unexpired base key for a Machine ID whenever duplicates existed - before its
-- request-UUID authorization check, and even for legacy one-argument calls.
-- A normal read (or an unauthorized caller) could therefore revoke the
-- legitimate licence, and the owner never chose which key to keep.
--
-- This migration:
--   1. makes the read side-effect-free (duplicates are simply not returned as
--      active keys; the legitimate key keeps working);
--   2. adds `owner_review_duplicate_machine_keys`, callable only by the owner,
--      which previews the duplicates and - only with an explicit action and a
--      chosen `p_keep_license_id` - revokes the others in one transaction and
--      records an audit row.
-- Apply with the normal owner-controlled migration process. No passcode rotation
-- is required; the public read contract is unchanged apart from the removed
-- destructive side effect.

-- 1. Side-effect-free public read.
create or replace function public.check_machine_entitlements(p_machine_id text, p_request_id uuid default null)
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

    -- No destructive repair here. Duplicate unexpired base keys are a data
    -- condition for the owner to resolve with owner_review_duplicate_machine_keys;
    -- a read must never revoke, pause or otherwise mutate licence state.

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

-- 2. Audit table for owner licence actions (service_role bypasses RLS; no public
--    policy is granted, so only the security-definer owner RPC can write here).
--    The table is append-only: UPDATE/DELETE/TRUNCATE are rejected, and each
--    resolve writes its audit row in the same transaction as the revocation.
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

-- 3. Owner-only, previewable, audited duplicate repair.
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
