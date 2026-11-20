"""CustomTkinter dialog for browsing and downloading online Café Desk templates."""
from __future__ import annotations

import customtkinter as ctk

from online_templates import (
    VALID_CATEGORIES,
    delete_downloaded_template,
    fetch_online_templates,
    list_downloaded_templates,
    save_downloaded_template,
)
from workers import WorkerPool


class OnlineTemplatesDialog(ctk.CTkToplevel):
    def __init__(self, master, *, on_changed=None):
        super().__init__(master)
        self.title("Online Templates")
        self.geometry("760x540")
        self.minsize(640, 440)
        self.on_changed = on_changed
        self._items: list[dict] = []
        self._downloaded: dict[tuple[str, str], dict] = {}

        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            top,
            text="Online Templates",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=(4, 12))

        self.category_var = ctk.StringVar(value="all")
        self.category_menu = ctk.CTkOptionMenu(
            top,
            values=["all", *VALID_CATEGORIES],
            variable=self.category_var,
            command=lambda _v: self._render_list(),
            width=130,
        )
        self.category_menu.pack(side="left", padx=6)

        self.refresh_btn = ctk.CTkButton(
            top,
            text="Refresh",
            width=96,
            command=self.refresh_catalog,
        )
        self.refresh_btn.pack(side="right", padx=4)

        self.status_label = ctk.CTkLabel(self, text="Loading online templates...", anchor="w")
        self.status_label.pack(fill="x", padx=20, pady=(0, 6))

        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        self.refresh_catalog()

    def _reload_local(self):
        local = list_downloaded_templates()
        self._downloaded = {(t["category"], t["slug"]): t for t in local}

    def refresh_catalog(self):
        self.refresh_btn.configure(state="disabled")
        self.status_label.configure(text="Fetching templates from Supabase...")
        self._reload_local()

        def worker():
            try:
                rows = fetch_online_templates()
                err = None
            except Exception as exc:
                rows = []
                err = str(exc)
            return rows, err

        def _deliver(result):
            rows, err = result
            self.after(0, lambda: self._on_loaded(rows, err))

        WorkerPool().run(worker, on_result=_deliver)

    def _on_loaded(self, rows: list[dict], err: str | None):
        self.refresh_btn.configure(state="normal")
        if err:
            self.status_label.configure(
                text=f"Offline or error ({err}). Showing {len(self._downloaded)} downloaded template(s)."
            )
            self._items = list(self._downloaded.values())
        else:
            self._items = rows
            self.status_label.configure(
                text=f"{len(rows)} online template(s) available · {len(self._downloaded)} downloaded"
            )
        self._render_list()

    def _render_list(self):
        for child in self.scroll.winfo_children():
            child.destroy()

        selected = self.category_var.get().strip().lower()
        visible = [
            item for item in self._items
            if selected == "all" or str(item.get("category", "")).lower() == selected
        ]

        if not visible:
            ctk.CTkLabel(
                self.scroll,
                text="No templates found for this category.",
                text_color="gray",
            ).pack(pady=24)
            return

        for item in visible:
            cat = str(item.get("category", ""))
            slug = str(item.get("slug", ""))
            local = self._downloaded.get((cat, slug))
            ver = int(item.get("version") or 1)

            row = ctk.CTkFrame(self.scroll)
            row.pack(fill="x", padx=6, pady=5)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

            badge = f"[{cat.upper()}]  {item.get('name') or slug}  (v{ver})"
            if local:
                badge += "  ✓ Downloaded"
            ctk.CTkLabel(
                info,
                text=badge,
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
            ).pack(fill="x")

            desc = str(item.get("description") or "").strip()
            if desc:
                ctk.CTkLabel(
                    info,
                    text=desc,
                    anchor="w",
                    text_color="gray75",
                    wraplength=460,
                    justify="left",
                ).pack(fill="x", pady=(2, 0))

            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.pack(side="right", padx=10, pady=8)

            btn_text = "Update" if local and int(local.get("version") or 1) < ver else ("Re-download" if local else "Download")
            ctk.CTkButton(
                actions,
                text=btn_text,
                width=105,
                command=lambda it=item: self._download(it),
            ).pack(side="left", padx=4)

            if local:
                ctk.CTkButton(
                    actions,
                    text="Remove",
                    width=80,
                    fg_color="#8b2635",
                    hover_color="#a83244",
                    command=lambda c=cat, s=slug: self._remove(c, s),
                ).pack(side="left", padx=4)

    def _download(self, item: dict):
        try:
            save_downloaded_template(item)
            self._reload_local()
            self.status_label.configure(text=f"Downloaded '{item.get('name') or item.get('slug')}'.")
            self._render_list()
            if self.on_changed:
                self.on_changed()
        except Exception as exc:
            self.status_label.configure(text=f"Download failed: {exc}")

    def _remove(self, category: str, slug: str):
        if delete_downloaded_template(category, slug):
            self._reload_local()
            self.status_label.configure(text=f"Removed '{slug}'.")
            self._render_list()
            if self.on_changed:
                self.on_changed()
