"""Local sales records, independent of GUI. No payment processing or tax logic."""
import csv,json,sqlite3
from contextlib import closing
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
import settings_store
import services

def db_path():return settings_store.settings_path().parent/'sales.sqlite3'
def connect(path=None):
    target=Path(path or db_path()); target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(target,timeout=10);conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('''CREATE TABLE IF NOT EXISTS sales (
        sale_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, business TEXT NOT NULL,
        currency TEXT NOT NULL, total_cents INTEGER NOT NULL, items_json TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS sales_date_idx ON sales(created_at)');conn.commit();return conn

def validate_items(items):
    if not items or len(items)>500:raise ValueError('A sale must have between 1 and 500 service lines.')
    out=[];total=0
    for item in items:
        name=str(item['name']).strip()
        if not name or len(name)>80:raise ValueError('Invalid service name.')
        try:
            qty=int(item['qty']);value=Decimal(str(item['price']))
            if not 1<=qty<=999 or not value.is_finite() or value<0 or value>10000000 or value.as_tuple().exponent < -2:raise ValueError()
        except (ValueError,TypeError,InvalidOperation):raise ValueError('Invalid quantity or price.')
        cents=int(value*100);total+=cents*qty;out.append({'name':name,'qty':qty,'price':format(value,'.2f')})
    return out,total

def record_sale(sale_id,business,currency,items,created_at=None,path=None):
    if not isinstance(sale_id,str) or not sale_id or len(sale_id)>64:raise ValueError('Invalid sale reference.')
    business=settings_store.clean_name(business);currency=str(currency).strip()
    if not currency or len(currency)>8 or any(c in currency for c in '\n\r|'):raise ValueError('Invalid currency.')
    items,total=validate_items(items);payload=json.dumps(items,ensure_ascii=False,separators=(',',':'))
    when=created_at or datetime.now().astimezone().isoformat(timespec='seconds')
    try:datetime.fromisoformat(when)
    except ValueError:raise ValueError('Invalid sale date.')
    with closing(connect(path)) as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT business,currency,total_cents,items_json FROM sales WHERE sale_id=?',(sale_id,)).fetchone()
            if old:
                if old!=(business,currency,total,payload):raise ValueError('This sale reference already exists with different details.')
                conn.rollback();return False
            conn.execute('INSERT INTO sales(sale_id,created_at,business,currency,total_cents,items_json) VALUES(?,?,?,?,?,?)',(sale_id,when,business,currency,total,payload));conn.commit()
        except Exception:
            conn.rollback();raise
    return True

def date_range(start,end):
    try:a=date.fromisoformat(start);b=date.fromisoformat(end)
    except (ValueError,TypeError):raise ValueError('Enter dates as YYYY-MM-DD.')
    if a>b:raise ValueError('Start date must not be after end date.')
    return a.isoformat(),b.isoformat()

def report(start,end,path=None,limit=500):
    start,end=date_range(start,end)
    with closing(connect(path)) as conn:
        conn.row_factory=sqlite3.Row
        rows=conn.execute("SELECT * FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC, sale_id DESC LIMIT ?",(start,end,limit)).fetchall()
        totals=conn.execute("SELECT currency,COUNT(*) AS count,COALESCE(SUM(total_cents),0) AS cents FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? GROUP BY currency ORDER BY currency",(start,end)).fetchall()
    return {'rows':[dict(r) for r in rows],'totals':[dict(r) for r in totals]}

def export_csv(start,end,destination,path=None):
    start,end=date_range(start,end)
    services._different([path or db_path()],destination)
    def clean(v):
        t=str(v);return "'"+t if t.lstrip().startswith(('=','+','-','@','\t','\r')) else t
    def write(tmp):
        with closing(connect(path)) as conn,open(tmp,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['Reference','Date and time','Business','Currency','Total','Services'])
            for ref,when,business,currency,total,payload in conn.execute("SELECT sale_id,created_at,business,currency,total_cents,items_json FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC",(start,end)):
                details='; '.join(f"{x['name']} x{x['qty']} @ {x['price']}" for x in json.loads(payload))
                w.writerow([clean(ref),clean(when),clean(business),clean(currency),format(Decimal(total)/100,'.2f'),clean(details)])
    services._atomic_write(destination,write)
