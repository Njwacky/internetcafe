"""Online downloadable templates client and local cache for Café Desk."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests

from cloud_sync import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL
from settings_store import app_data_dir

VALID_CATEGORIES = ("cv", "card", "form", "collage")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def sanitize_slug(slug: str) -> str:
    value = str(slug or "").strip().lower()
    if not _SLUG_RE.match(value):
        raise ValueError(f"Invalid template slug: {slug!r}")
    return value


def validate_category(category: str) -> str:
    value = str(category or "").strip().lower()
    if value not in VALID_CATEGORIES:
        raise ValueError(f"Invalid template category: {category!r}")
    return value


def templates_dir(base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else app_data_dir()
    path = root / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_online_templates(
    category: str | None = None,
    *,
    timeout: int = 15,
    supabase_url: str | None = None,
    anon_key: str | None = None,
) -> list[dict[str, Any]]:
    url = (supabase_url or DEFAULT_SUPABASE_URL or "").strip().rstrip("/")
    key = (anon_key or DEFAULT_SUPABASE_ANON_KEY or "").strip()
    if not url or not key:
        raise RuntimeError("Supabase URL or anon key is not configured.")

    params: dict[str, str] = {
        "is_active": "eq.true",
        "select": "id,slug,category,name,description,version,preview_url,definition,updated_at",
        "order": "category.asc,name.asc",
    }
    if category is not None:
        cat = validate_category(category)
        params["category"] = f"eq.{cat}"

    resp = requests.get(
        f"{url}/rest/v1/templates",
        params=params,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected response from templates catalog.")
    return rows


def save_downloaded_template(
    item: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> Path:
    cat = validate_category(item.get("category", ""))
    slug = sanitize_slug(item.get("slug", ""))
    definition = item.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("Template definition must be a JSON object.")

    dest_dir = templates_dir(base_dir) / cat
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{slug}.json"

    payload = {
        "id": str(item.get("id") or ""),
        "slug": slug,
        "category": cat,
        "name": str(item.get("name") or slug),
        "description": str(item.get("description") or ""),
        "version": int(item.get("version") or 1),
        "preview_url": str(item.get("preview_url") or ""),
        "definition": definition,
    }
    dest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest_path


def list_downloaded_templates(
    category: str | None = None,
    *,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    root = templates_dir(base_dir)
    cats = (validate_category(category),) if category is not None else VALID_CATEGORIES
    results: list[dict[str, Any]] = []
    for cat in cats:
        cat_dir = root / cat
        if not cat_dir.is_dir():
            continue
        for file_path in sorted(cat_dir.glob("*.json")):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("slug") and isinstance(data.get("definition"), dict):
                data["_local_path"] = str(file_path)
                results.append(data)
    return results


def delete_downloaded_template(
    category: str,
    slug: str,
    *,
    base_dir: Path | None = None,
) -> bool:
    cat = validate_category(category)
    clean_slug = sanitize_slug(slug)
    target = templates_dir(base_dir) / cat / f"{clean_slug}.json"
    if target.is_file():
        target.unlink()
        return True
    return False
