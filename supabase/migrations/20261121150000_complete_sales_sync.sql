-- Complete, keyset-paginated full-history reconciliation, including old voids.
-- Re-scan every run deliberately: a creation-date watermark loses later voids.
-- No live migration is applied by the desktop app.
create index if not exists idx_shop_sync_sales_cursor
    on public.shop_sync_sales(shop_id, sale_id collate "C");

-- The local ledger supports up to 500 lines x 999 units x 10,000,000 currency.
-- Integer cents overflow at ordinary valid values; bigint covers the same range.
alter table public.shop_sync_sales alter column total_cents type bigint;

-- Canonical semantic identity, shared by uploads and comparisons with legacy rows.
-- Formatting differences ("1" vs 1 units; 1 vs "1.00" price) are not new sales.
create or replace function public.normalize_shop_sale_items(p_items jsonb)
returns jsonb language plpgsql immutable set search_path = public as $$
declare
    v_line jsonb;
    v_qty numeric;
    v_price numeric;
    v_items jsonb := '[]'::jsonb;
    v_name text;
begin
    if jsonb_typeof(p_items) is distinct from 'array' then
        raise exception 'Service lines must be an array.';
    end if;
    if jsonb_array_length(p_items) not between 1 and 500 then
        raise exception 'A sale must contain between 1 and 500 service lines.';
    end if;
    for v_line in select value from jsonb_array_elements(p_items) loop
        v_name := regexp_replace(v_line->>'name', '^[[:space:]]+|[[:space:]]+$', '', 'g');
        if jsonb_typeof(v_line) is distinct from 'object'
           or jsonb_typeof(v_line->'name') is distinct from 'string'
           or length(v_name) not between 1 and 80
           or coalesce(v_line->>'qty', '') !~ '^[0-9]+$'
           or coalesce(v_line->>'price', '') !~ '^-?[0-9]+([.][0-9]{1,2})?$' then
            raise exception 'Invalid service name, integer quantity or price.';
        end if;
        v_qty := (v_line->>'qty')::numeric;
        v_price := (v_line->>'price')::numeric;
        if v_qty not between 1 and 999 or v_price not between 0 and 10000000 then
            raise exception 'Service quantity or price is out of range.';
        end if;
        v_items := v_items || jsonb_build_array(jsonb_build_object(
            'name',v_name,'qty',v_qty::integer,
            'price',to_char(v_price,'FM999999990.00')));
    end loop;
    return v_items;
end;
$$;
revoke all on function public.normalize_shop_sale_items(jsonb) from public,anon,authenticated;

create or replace function public.sync_shop_sales_v2(
    p_shop_id text,
    p_sync_key_sha256 text,
    p_sales jsonb default '[]'::jsonb,
    p_after text default '',
    p_read boolean default true
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
    v_old public.shop_sync_sales%rowtype;
    v_total numeric;
    v_items jsonb;
    v_pushed integer := 0;
    v_rows jsonb;
    v_after text;
begin
    select sync_key_sha256 into v_hash from public.shop_sync_profiles where shop_id = v_shop;
    if v_hash is null or v_hash <> coalesce(p_sync_key_sha256, '') then
        return jsonb_build_object('ok', false, 'error', 'Shop ID or Sync Passcode is incorrect. Initialize the shop price list first.');
    end if;
    if jsonb_typeof(p_sales) is distinct from 'array' or jsonb_array_length(p_sales) > 250 then
        raise exception 'Supply an array of at most 250 sales per upload.';
    end if;
    -- Serialize uploads per shop so conflict checking and insert/void are atomic.
    if jsonb_array_length(p_sales) > 0 then
        perform pg_advisory_xact_lock(hashtextextended('sales-sync|' || v_shop, 0));
    end if;
    for v_item in select value from jsonb_array_elements(p_sales) loop
        if jsonb_typeof(v_item) is distinct from 'object'
           or jsonb_typeof(v_item->'sale_id') is distinct from 'string'
           or length(trim(v_item->>'sale_id')) not between 1 and 64
           or length(coalesce(v_item->>'station', '')) > 40
           or length(coalesce(v_item->>'business', '')) not between 1 and 80
           or length(coalesce(v_item->>'currency', '')) not between 1 and 8
           or (v_item->>'currency') ~ E'[\\n\\r|]'
           or length(coalesce(v_item->>'created_at', '')) not between 10 and 40
           or (v_item->>'created_at') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}($|[T ])'
           or jsonb_typeof(v_item->'items') is distinct from 'array' then
            raise exception 'Invalid sale identity, metadata or service lines.';
        end if;
        perform (v_item->>'created_at')::timestamp with time zone;
        if nullif(v_item->>'voided_at', '') is not null then
            if length(v_item->>'voided_at') not between 10 and 40
               or (v_item->>'voided_at') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}($|[T ])' then
                raise exception 'Invalid void timestamp.';
            end if;
            perform (v_item->>'voided_at')::timestamp with time zone;
        end if;
        v_items := public.normalize_shop_sale_items(v_item->'items');
        select sum((line->>'qty')::numeric * (line->>'price')::numeric * 100)
            into v_total from jsonb_array_elements(v_items) line;
        if jsonb_typeof(v_item->'total_cents') is distinct from 'number'
           or v_total <> (v_item->>'total_cents')::numeric
           or v_total > 499500000000000 then
            raise exception 'Sale total does not match service lines or exceeds the cloud ledger limit.';
        end if;
        select * into v_old from public.shop_sync_sales
            where shop_id = v_shop and sale_id = v_item->>'sale_id';
        if found then
            if row(v_old.created_at, v_old.station, v_old.business, v_old.currency, v_old.total_cents, public.normalize_shop_sale_items(v_old.items_json))
               is distinct from row(v_item->>'created_at', coalesce(v_item->>'station', ''),
                   v_item->>'business', v_item->>'currency', v_total::bigint, v_items) then
                raise exception 'Sale reference conflicts with immutable recorded details: %', v_item->>'sale_id';
            end if;
            if v_old.voided_at is null and nullif(v_item->>'voided_at', '') is not null then
                update public.shop_sync_sales set voided_at = v_item->>'voided_at',
                    void_reason = left(coalesce(v_item->>'void_reason', 'Voided on shop PC'), 160), synced_at = now()
                    where shop_id = v_shop and sale_id = v_old.sale_id;
            end if;
        else
            insert into public.shop_sync_sales(shop_id,sale_id,created_at,station,business,currency,total_cents,items_json,voided_at,void_reason)
            values(v_shop,v_item->>'sale_id',v_item->>'created_at',coalesce(v_item->>'station',''),
                   v_item->>'business',v_item->>'currency',v_total::bigint,v_items,
                   nullif(v_item->>'voided_at',''),left(v_item->>'void_reason',160));
        end if;
        v_pushed := v_pushed + 1;
    end loop;
    select coalesce(jsonb_agg(jsonb_build_object(
        'sale_id',s.sale_id,'created_at',s.created_at,'station',s.station,'business',s.business,
        'currency',s.currency,'total_cents',s.total_cents,'items',s.items_json,
        'voided_at',s.voided_at,'void_reason',s.void_reason
    ) order by s.sale_id collate "C"), '[]'::jsonb), max(s.sale_id collate "C") into v_rows,v_after
    from (select * from public.shop_sync_sales
          where p_read and shop_id=v_shop and sale_id collate "C" > coalesce(p_after,'') collate "C"
          order by sale_id collate "C" limit 250) s;
    return jsonb_build_object('ok',true,'protocol',2,'shop_id',v_shop,
        'pushed_count',v_pushed,'sales',v_rows,'next_after',v_after);
end;
$$;
revoke all on function public.sync_shop_sales_v2(text,text,jsonb,text,boolean) from public;
grant execute on function public.sync_shop_sales_v2(text,text,jsonb,text,boolean) to anon,authenticated;

-- Fail explicitly for older clients rather than silently returning only 2,000 rows.
create or replace function public.sync_shop_sales(
    p_shop_id text, p_sync_key_sha256 text, p_sales jsonb default '[]'::jsonb, p_since text default ''
) returns jsonb language sql security definer set search_path = public as $$
    select jsonb_build_object('ok',false,'error','Upgrade Cafe Desk on every shop PC for complete sales synchronization (protocol 2).');
$$;

revoke all on function public.sync_shop_sales(text,text,jsonb,text) from public;
grant execute on function public.sync_shop_sales(text,text,jsonb,text) to anon,authenticated;
