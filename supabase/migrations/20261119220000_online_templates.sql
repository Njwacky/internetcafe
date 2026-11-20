-- Online downloadable templates catalog for Café Desk
-- Publicly readable when active; only the owner (public.is_cafedesk_owner()) can insert/update/delete.

create table if not exists public.templates (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  category text not null check (category in ('cv', 'card', 'form', 'collage')),
  name text not null,
  description text not null default '',
  version integer not null default 1 check (version >= 1),
  preview_url text not null default '',
  definition jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_templates_category_active
  on public.templates (category, is_active, name);

alter table public.templates enable row level security;

drop policy if exists "templates_public_read" on public.templates;
create policy "templates_public_read"
  on public.templates
  for select
  to anon, authenticated
  using (is_active = true or public.is_cafedesk_owner());

drop policy if exists "templates_owner_insert" on public.templates;
create policy "templates_owner_insert"
  on public.templates
  for insert
  to authenticated
  with check (public.is_cafedesk_owner());

drop policy if exists "templates_owner_update" on public.templates;
create policy "templates_owner_update"
  on public.templates
  for update
  to authenticated
  using (public.is_cafedesk_owner())
  with check (public.is_cafedesk_owner());

drop policy if exists "templates_owner_delete" on public.templates;
create policy "templates_owner_delete"
  on public.templates
  for delete
  to authenticated
  using (public.is_cafedesk_owner());
