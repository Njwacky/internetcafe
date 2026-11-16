"""Staff-only sales history and gross totals view."""
import json
from datetime import date,timedelta
from decimal import Decimal
import tkinter as tk
from tkinter import filedialog,messagebox
import customtkinter as ctk
import sales_store
import services

def show_reports(app,parent,card,label,button,entry):
    app.heading(parent,'Sales reports','Internal service log grouped by currency. Not a fiscal POS or tax register — payment status, cash drawer reconciliation and VAT/tax are not tracked.')
    notice=ctk.CTkFrame(parent,fg_color='#382F27',corner_radius=10)
    notice.pack(fill='x',pady=(0,12))
    label(notice,'Accounting & POS notice',size=12,bold=True,color='#FFDBA2').pack(anchor='w',padx=14,pady=(10,2))
    label(notice,'Use these totals for internal desk tracking only. Issue official tax invoices or fiscal receipts from your registered POS or accounting software where required by law.',size=11,color='#F1D1A5',wraplength=760,justify='left').pack(anchor='w',padx=14,pady=(0,10))
    box=card(parent);box.pack(fill='x',pady=(0,12));label(box,'Choose a date range',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,5))
    label(box,'Dates use the computer’s local date at the time each sale was recorded.',size=12,color='#A2ACC5').pack(anchor='w',padx=20)
    row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=20,pady=(12,18))
    start=entry(row,'YYYY-MM-DD');start.insert(0,(date.today()-timedelta(days=29)).isoformat());start.pack(side='left',padx=(0,8))
    end=entry(row,'YYYY-MM-DD');end.insert(0,date.today().isoformat());end.pack(side='left',padx=(0,8))
    result=card(parent);result.pack(fill='x',pady=(0,12));label(result,'Sales history',size=16,bold=True).pack(anchor='w',padx=20,pady=(17,5))
    summary=label(result,'',size=12,color='#A2ACC5',justify='left');summary.pack(anchor='w',padx=20,pady=(0,8))
    lst=tk.Listbox(result,height=8,font=('Segoe UI',11),bg='#121A29',fg='#F2F4FA',selectbackground='#405270',selectforeground='#FFFFFF',relief='flat',highlightthickness=0,exportselection=False);lst.pack(fill='x',padx=20,pady=(0,10))
    detail=ctk.CTkTextbox(result,height=125,fg_color='#121A29',border_color='#34415A',border_width=1,text_color='#F2F4FA');detail.pack(fill='x',padx=20,pady=(0,12));detail.configure(state='disabled')
    state={'rows':[],'range':None}
    def load():
        try:
            data=sales_store.report(start.get().strip(),end.get().strip());state['rows']=data['rows'];state['range']=(start.get().strip(),end.get().strip())
            count=sum(x['count'] for x in data['totals']);parts=[f"{x['currency']} {Decimal(x['cents'])/100:.2f}" for x in data['totals']]
            summary.configure(text=f"{count} recorded sale(s) • Gross totals: {', '.join(parts) if parts else 'none'}\nShowing up to 500 latest records. CSV export includes the full date range.")
            lst.delete(0,'end')
            for r in state['rows']:lst.insert('end',f"{r['created_at'][:16]}   •   {r['business']}   •   {r['currency']} {Decimal(r['total_cents'])/100:.2f}   •   {r['sale_id'][:8]}")
            detail.configure(state='normal');detail.delete('1.0','end');detail.configure(state='disabled')
        except Exception as exc:messagebox.showerror('Could not load report',str(exc),parent=app)
    def selected(event=None):
        index=lst.curselection()
        if not index:return
        r=state['rows'][index[0]];items=json.loads(r['items_json']);text=f"Reference: {r['sale_id']}\nDate: {r['created_at']}\nBusiness: {r['business']}\n\n"
        text+='\n'.join(f"{x['name']} × {x['qty']} @ {r['currency']}{x['price']}" for x in items)
        text+=f"\n\nTotal: {r['currency']}{Decimal(r['total_cents'])/100:.2f}\nPayment status not tracked • tax not calculated"
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
        text=services.receipt_text(r['business'],r['currency'],items,timestamp=r['created_at'],reference=r['sale_id']).replace('\nRECEIPT\n','\nSALES RECORD COPY\n',1)
        app.job('Saving sale record…',lambda:services.export_document_pdf(text,path,'Sale record',0),lambda _:messagebox.showinfo('Sale record saved',f'Saved to:\n{path}\n\nThis copy is not proof of payment.',parent=app))
    button(row,'View report',load,width=120,height=34).pack(side='left')
    actions=ctk.CTkFrame(result,fg_color='transparent');actions.pack(fill='x',padx=20,pady=(0,18))
    button(actions,'⬇ Export CSV',export,secondary=True,width=130).pack(side='left',padx=(0,8));button(actions,'⬇ Save record PDF',export_record,secondary=True,width=165).pack(side='left');load()
