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
            'machine_rpc_rate_limits', v_rate_hashes
        )
    );
end;
$$;
revoke all on function public.owner_purge_machine_records(text) from public, anon;
grant execute on function public.owner_purge_machine_records(text) to authenticated;
notify pgrst, 'reload schema';
