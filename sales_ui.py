"""Staff-only sales history and gross totals view."""
import json
from datetime import date,timedelta
from decimal import Decimal
import tkinter as tk
from tkinter import filedialog,messagebox,simpledialog
import customtkinter as ctk
import cloud_sync
import sales_store
import services
import settings_store


def show_reports(app,parent,card,label,button,entry):
    app.heading(parent,'Sales reports','Internal service log grouped by currency. Not a fiscal POS or tax register — payment status, cash drawer reconciliation and VAT/tax are not tracked.')
    notice=ctk.CTkFrame(parent,fg_color='#382F27',corner_radius=10)
    notice.pack(fill='x',pady=(0,12))
    label(notice,'Accounting & POS notice',size=12,bold=True,color='#FFDBA2').pack(anchor='w',padx=14,pady=(10,2))
    label(notice,'Use these totals for internal desk tracking only. Issue official tax invoices or fiscal receipts from your registered POS or accounting software where required by law. Voiding a mistaken entry keeps a permanent audit line.',size=11,color='#F1D1A5',wraplength=760,justify='left').pack(anchor='w',padx=14,pady=(0,10))
    box=card(parent);box.pack(fill='x',pady=(0,12));label(box,'Choose a date range',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,5))
    label(box,'Dates use the computer’s local date at the time each sale was recorded.',size=12,color='#A2ACC5').pack(anchor='w',padx=20)
    row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(12,18))
    start=entry(row,'YYYY-MM-DD');start.insert(0,(date.today()-timedelta(days=29)).isoformat());start.pack(side='left',padx=(0,8))
    end=entry(row,'YYYY-MM-DD');end.insert(0,date.today().isoformat());end.pack(side='left',padx=(0,8))
    result=card(parent);result.pack(fill='x',pady=(0,12));label(result,'Sales history',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,5))
    summary=label(result,'',size=12,color='#A2ACC5',justify='left');summary.pack(anchor='w',padx=20,pady=(0,8))
    lst=tk.Listbox(result,height=8,font=('Segoe UI',11),bg='#121A29',fg='#F2F4FA',selectbackground='#405270',selectforeground='#FFFFFF',relief='flat',highlightthickness=0,exportselection=False);lst.pack(fill='x',padx=20,pady=(0,10))
    detail=ctk.CTkTextbox(result,height=135,fg_color='#121A29',border_color='#34415A',border_width=1,text_color='#F2F4FA');detail.pack(fill='x',padx=20,pady=(0,12));detail.configure(state='disabled')
    state={'rows':[],'range':None}

    def load():
        try:
            data=sales_store.report(start.get().strip(),end.get().strip());state['rows']=data['rows'];state['range']=(start.get().strip(),end.get().strip())
            count=sum(x['count'] for x in data['totals']);parts=[f"{x['currency']} {Decimal(x['cents'])/100:.2f}" for x in data['totals']]
            void_note=f"  •  {data.get('voided_count',0)} voided" if data.get('voided_count') else ''
            station_parts=[f"{s['station_label']}: {s['currency']} {Decimal(s['cents'])/100:.2f} ({s['count']})" for s in data.get('by_station',[])]
            station_line=f"\nBy computer/station: {'  |  '.join(station_parts)}" if len(station_parts)>=1 else ''
            summary.configure(text=f"{count} active sale(s){void_note} • Combined Shop Totals: {', '.join(parts) if parts else 'none'}{station_line}\nShowing up to 500 latest records. CSV export includes the full date range.")
            lst.delete(0,'end')
            for r in state['rows']:
                prefix='[VOIDED] ' if r.get('voided_at') else ''
                st_tag=f"[{r['station']}] " if r.get('station') else ''
                lst.insert('end',f"{prefix}{r['created_at'][:16]}   •   {st_tag}{r['business']}   •   {r['currency']} {Decimal(r['total_cents'])/100:.2f}   •   {r['sale_id'][:8]}")
            detail.configure(state='normal');detail.delete('1.0','end');detail.configure(state='disabled')
        except Exception as exc:messagebox.showerror('Could not load report',str(exc),parent=app)

    def selected(event=None):
        index=lst.curselection()
        if not index:return
        r=state['rows'][index[0]];items=json.loads(r['items_json'])
        status_line=f"Status: VOIDED ({r['voided_at']}) — Reason: {r.get('void_reason') or 'Mistaken entry'}\n" if r.get('voided_at') else 'Status: RECORDED\n'
        station_line=f"Computer / Station: {r.get('station') or 'Unlabeled PC'}\n"
        text=f"Reference: {r['sale_id']}\n{status_line}{station_line}Date: {r['created_at']}\nBusiness: {r['business']}\n\n"
        text+='\n'.join(f"{x['name']} × {x['qty']} @ {r['currency']}{x['price']}" for x in items)
        text+=f"\n\nTotal: {r['currency']}{Decimal(r['total_cents'])/100:.2f}"
        if r.get('voided_at'):
            text+=' (excluded from active gross totals)'
        text+='\nPayment status not tracked • tax not calculated'
        detail.configure(state='normal');detail.delete('1.0','end');detail.insert('1.0',text);detail.configure(state='disabled')

    lst.bind('<<ListboxSelect>>',selected)

    def export():
        try:a,b=sales_store.date_range(start.get().strip(),end.get().strip())
        except ValueError as exc:return messagebox.showerror('Invalid date',str(exc),parent=app)
        path=filedialog.asksaveasfilename(parent=app,defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='sales-report.csv')
        if path:app.job('Exporting sales report…',lambda:sales_store.export_csv(a,b,path),lambda _:messagebox.showinfo('Report exported',f'CSV saved to:\n{path}',parent=app))

    def export_record():
        index=lst.curselection()
        if not index:return messagebox.showinfo('Select a sale','Select a sale in the list first.',parent=app)
        r=state['rows'][index[0]];items=json.loads(r['items_json'])
        path=filedialog.asksaveasfilename(parent=app,defaultextension='.pdf',filetypes=[('PDF','*.pdf')],initialfile='sale-record.pdf')
        if not path:return
        header_tag='\nVOIDED SALES RECORD COPY\n' if r.get('voided_at') else '\nSALES RECORD COPY\n'
        text=services.receipt_text(r['business'],r['currency'],items,timestamp=r['created_at'],reference=r['sale_id']).replace('\nRECEIPT\n',header_tag,1)
        if r.get('voided_at'):
            text+=f"\nVOIDED: {r['voided_at']} ({r.get('void_reason') or 'Mistaken entry'})"
        app.job('Saving sale record…',lambda:services.export_document_pdf(text,path,'Sale record',0),lambda _:messagebox.showinfo('Sale record saved',f'Saved to:\n{path}\n\nThis copy is not proof of payment.',parent=app))

    def void_selected():
        index=lst.curselection()
        if not index:return messagebox.showinfo('Select a sale','Select a sale in the list first.',parent=app)
        r=state['rows'][index[0]]
        if r.get('voided_at'):
            return messagebox.showinfo('Already voided','This sale record has already been marked voided.',parent=app)
        reason=simpledialog.askstring('Void mistaken sale',f"Void sale {r['sale_id'][:8]} ({r['currency']} {Decimal(r['total_cents'])/100:.2f})?\n\nEnter a brief reason (kept in the audit log):",initialvalue='Mistaken entry',parent=app)
        if reason is None:return
        try:
            sales_store.void_sale(r['sale_id'],reason)
            load()
            messagebox.showinfo('Sale voided','The sale was marked VOIDED and excluded from active gross totals.',parent=app)
        except Exception as exc:
            messagebox.showerror('Could not void sale',str(exc),parent=app)

    def backup_db():
        path=filedialog.asksaveasfilename(parent=app,defaultextension='.sqlite3',filetypes=[('SQLite backup','*.sqlite3 *.db')],initialfile=f'sales-backup-{date.today().isoformat()}.sqlite3')
        if path:
            app.job('Backing up sales database…',lambda:sales_store.backup_database(path),lambda _:messagebox.showinfo('Backup saved',f'Sales database backup saved to:\n{path}',parent=app))

    def merge_other_pc():
        src=filedialog.askopenfilename(
            parent=app,
            title='Import & Merge Sales from Another Shop PC',
            filetypes=[('Shop Sales / SQLite Backup','*.sqlite3 *.db *.json'),('All files','*.*')],
        )
        if not src:return
        def _do():
            return sales_store.merge_from_file(src)
        def _done(res):
            load()
            messagebox.showinfo(
                'Multi-PC Sales Merged',
                f"Imported {res['inserted']} new sale(s) from the other computer ({res['updated_voids']} void update(s), {res['skipped']} duplicate(s) safely skipped).",
                parent=app,
            )
        app.job('Merging sales from other PC…',_do,_done)

    def sync_lan():
        cfg=settings_store.load_settings()
        folder=cfg.get('lan_sync_folder','')
        if not folder:
            folder=filedialog.askdirectory(parent=app,title='Select Shared Network / USB Folder for Multi-PC Sync')
            if not folder:return
            settings_store.save_multi_pc_settings(
                cfg.get('station_name','Counter PC 1'),
                shop_id=cfg.get('shop_id',''),
                lan_sync_folder=folder,
                supabase_url=cfg.get('supabase_url',''),
                supabase_anon_key=cfg.get('supabase_anon_key',''),
            )
        def _do():
            return sales_store.sync_lan_folder(folder,station_name=cfg.get('station_name',''))
        def _done(res):
            load()
            messagebox.showinfo(
                'LAN / Folder Sync Complete',
                f"Synced with {res['peer_files']} other PC file(s) in:\n{folder}\n\n"
                f"New sales merged: {res['inserted']}  •  Void updates: {res['updated_voids']}",
                parent=app,
            )
        app.job('Syncing Multi-PC LAN folder…',_do,_done)

    def sync_cloud():
        cfg=settings_store.load_settings()
        if not cloud_sync.is_supabase_configured(cfg) or not cfg.get('shop_id'):
            return messagebox.showinfo(
                'Configure Multi-PC Cloud Sync First',
                'Open Business Settings → Multi-PC Shop & Cloud Sync and enter your Shop ID (e.g. SHOP-SUNRISE) plus your Supabase Project URL and Anon Key.',
                parent=app,
            )
        secret=simpledialog.askstring(
            'Multi-PC Cloud Sales Sync',
            f"Enter Shop Sync Passcode for {cfg.get('shop_id')} (4+ characters):",
            show='•',
            parent=app,
        )
        if not secret:return
        def _do():
            local_rows=sales_store.export_sales_records(limit=1500)
            cloud_res=cloud_sync.sync_multi_pc_sales(cfg,secret,local_rows)
            merged=sales_store.merge_external_sales(cloud_res.get('sales',[]))
            return {'pushed':cloud_res.get('pushed_count',0),**merged}
        def _done(res):
            load()
            messagebox.showinfo(
                'Supabase Multi-PC Sync Complete',
                f"Uploaded {res['pushed']} sale(s) from this PC and merged {res['inserted']} new sale(s) from your other shop PCs.",
                parent=app,
            )
        app.job('Syncing Multi-PC sales with Supabase…',_do,_done)

    button(row,'View report',load,width=120,height=34).pack(side='left')
    actions=ctk.CTkFrame(result,fg_color='transparent');actions.pack(fill='x',padx=20,pady=(0,8))
    button(actions,'⬇ Export CSV',export,secondary=True,width=130).pack(side='left',padx=(0,8))
    button(actions,'⬇ Save record PDF',export_record,secondary=True,width=165).pack(side='left',padx=(0,8))
    button(actions,'⊘ Void selected',void_selected,secondary=True,width=140).pack(side='left',padx=(0,8))
    button(actions,'💾 Backup database',backup_db,secondary=True,width=160).pack(side='left')

    mp_bar=ctk.CTkFrame(result,fg_color='transparent');mp_bar.pack(fill='x',padx=20,pady=(0,18))
    label(mp_bar,'Multi-PC Shop Sync (2+ Computers):',size=12,bold=True,color='#B9FF70').pack(side='left',padx=(0,10))
    button(mp_bar,'⇄ Sync Shared LAN Folder',sync_lan,secondary=True,width=195).pack(side='left',padx=(0,8))
    button(mp_bar,'📥 Import Other PC File',merge_other_pc,secondary=True,width=175).pack(side='left',padx=(0,8))
    button(mp_bar,'☁ Sync via Supabase',sync_cloud,secondary=True,width=165).pack(side='left')
    load()
