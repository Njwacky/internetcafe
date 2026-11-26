-- Step 3: serialize explicit duplicate-licence repair with issuance/re-signing.
-- Owner AAL2 enforcement and append-only audit behavior from Step 2 are retained.
-- Preview stays a read-only snapshot; resolve revalidates under the machine lock.
-- Busy row locks fail closed with 55P03 to avoid row/advisory lock inversion.

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

    if v_action = 'resolve' then
        -- Same normalized bigint lock as issuance and signature replacement.
        -- Acquire BEFORE reading candidates, validating the keeper or returning
        -- a no-op: all of those decisions must reflect the post-wait state.
        perform pg_advisory_xact_lock(hashtextextended(v_mid, 0));

        -- Protect the keeper too, including currently paused candidates. A plain
        -- pause/delete need not take our advisory lock. NOWAIT is deliberate:
        -- UPDATE OF identity/expiry columns obtains a row lock before its BEFORE
        -- trigger requests the machine lock. Waiting here could invert that
        -- order and deadlock. A busy row aborts safely with SQLSTATE 55P03; retry
        -- the entire transaction after refreshing, never continue partially.
        perform 1 from public.issued_machine_keys k
        where upper(trim(k.machine_id)) = v_mid
          and k.key_type in ('license', 'combo') and k.expires_at >= current_date
        order by k.id
        for update nowait;
    end if;

    select coalesce(jsonb_agg(jsonb_build_object(
        'license_id', k.license_id, 'key_type', k.key_type, 'customer', k.customer,
        'expires_at', k.expires_at, 'revoked', k.revoked, 'created_at', k.created_at
    ) order by k.created_at, k.license_id, k.id), '[]'::jsonb)
    into v_keys
    from public.issued_machine_keys k
    where upper(trim(k.machine_id)) = v_mid and k.key_type in ('license', 'combo')
      and not k.revoked and k.expires_at >= current_date;

    if v_action = 'preview' then
        return jsonb_build_object(
            'ok', true, 'machine_id', v_mid, 'action', 'none',
            'duplicate_keys', v_keys, 'revoked_license_ids', '[]'::jsonb
        );
    end if;

    -- Validate even with zero/one remaining active candidates. A stale request
    -- naming a keeper revoked by another repair must not report success/no-op.
    -- Legacy case/whitespace variants must not resolve to multiple keepers.
    if v_keep = '' or (
        select count(*) from jsonb_array_elements(v_keys) candidate
        where upper(trim(candidate ->> 'license_id')) = v_keep
    ) <> 1 then
        raise exception 'p_keep_license_id must name exactly one active base/combo licence on %.', v_mid
            using errcode = '22023';
    end if;

    if jsonb_array_length(v_keys) <= 1 then
        return jsonb_build_object(
            'ok', true, 'machine_id', v_mid, 'action', 'none',
            'duplicate_keys', v_keys, 'revoked_license_ids', '[]'::jsonb
        );
    end if;

    for v_row in
        select id, license_id from public.issued_machine_keys k
        where upper(trim(k.machine_id)) = v_mid and upper(trim(k.license_id)) <> v_keep
          and k.key_type in ('license', 'combo') and not k.revoked and k.expires_at >= current_date
        order by k.id
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

notify pgrst, 'reload schema';
