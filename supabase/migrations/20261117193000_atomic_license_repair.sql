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
