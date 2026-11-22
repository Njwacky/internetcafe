-- Additive columns on public.app_releases so portable/USB Café Desk builds can
-- self-update in-place from a signed .zip archive while installed builds use
-- the signed silent Setup.exe installer. Safe to re-run on existing projects.
alter table public.app_releases
    add column if not exists signature text not null default '',
    add column if not exists portable_download_url text not null default '',
    add column if not exists portable_sha256 text not null default '',
    add column if not exists portable_signature text not null default '';
