-- Apply in the Supabase SQL editor to an existing Café Desk database.

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

    -- An already duplicated, unexpired base ID is unsafe: pause all matching
    -- keys on check-in and let the owner investigate. Never return their tokens.
    if (select count(*) from public.issued_machine_keys k
        where k.machine_id = v_mid and k.key_type in ('license', 'combo')
          and k.expires_at >= current_date) > 1 then
        update public.issued_machine_keys set revoked = true,
            revoke_reason = 'Duplicate Machine ID: contact the owner for review'
        where machine_id = v_mid and expires_at >= current_date and not revoked;
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

alter table public.activation_requests add column if not exists contact_name text not null default '';

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
