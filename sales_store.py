"""Local sales records, independent of GUI. No payment processing or tax logic."""
import csv,json,shutil,sqlite3
from contextlib import closing
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
import settings_store
import services


class SalesStoreError(ValueError):
    """Raised when the local sales SQLite file is damaged or unreadable."""


def db_path():
    return settings_store.settings_path().parent/'sales.sqlite3'


def _quarantine_corrupt_db(target):
    p=Path(target)
    if not p.exists():
        return None
    corrupt_copy=p.with_name(p.name+'.corrupt')
    try:
        if not corrupt_copy.exists():
            shutil.copy2(p,corrupt_copy)
    except OSError:
        pass
    return corrupt_copy


def connect(path=None):
    target=Path(path or db_path()); target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        conn=sqlite3.connect(target,timeout=10)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute('''CREATE TABLE IF NOT EXISTS sales (
            sale_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, business TEXT NOT NULL,
            currency TEXT NOT NULL, total_cents INTEGER NOT NULL, items_json TEXT NOT NULL,
            voided_at TEXT, void_reason TEXT)''')
        cols={row[1] for row in conn.execute('PRAGMA table_info(sales)').fetchall()}
        if 'voided_at' not in cols:
            conn.execute('ALTER TABLE sales ADD COLUMN voided_at TEXT')
        if 'void_reason' not in cols:
            conn.execute('ALTER TABLE sales ADD COLUMN void_reason TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS sales_date_idx ON sales(created_at)')
        conn.commit()
        return conn
    except sqlite3.DatabaseError as exc:
        try: conn.close()
        except Exception: pass
        _quarantine_corrupt_db(target)
        raise SalesStoreError(
            f'Local sales database ({target.name}) is damaged or unreadable. '
            f'A quarantine copy ({target.name}.corrupt) was saved; restore a backup or archive the damaged file.'
        ) from exc


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
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            _quarantine_corrupt_db(path or db_path())
            raise SalesStoreError('Could not record sale because the local sales database is damaged.') from exc
        except Exception:
            conn.rollback();raise
    return True


def void_sale(sale_id,reason='Mistaken entry',voided_at=None,path=None):
    """Mark a recorded sale as voided (preserving an audit trail rather than silently deleting rows)."""
    if not isinstance(sale_id,str) or not sale_id.strip():
        raise ValueError('Select a valid sale reference to void.')
    clean_reason=' '.join(str(reason or 'Mistaken entry').split())[:160]
    if not clean_reason:
        raise ValueError('Enter a brief void reason.')
    when=voided_at or datetime.now().astimezone().isoformat(timespec='seconds')
    try: datetime.fromisoformat(when)
    except ValueError: raise ValueError('Invalid void timestamp.')
    with closing(connect(path)) as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT voided_at FROM sales WHERE sale_id=?',(sale_id,)).fetchone()
            if not row:
                conn.rollback()
                raise ValueError('Sale record not found.')
            if row[0]:
                conn.rollback()
                return False
            conn.execute('UPDATE sales SET voided_at=?, void_reason=? WHERE sale_id=?',(when,clean_reason,sale_id))
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise SalesStoreError('Could not void sale because the local sales database is damaged.') from exc
        except Exception:
            conn.rollback();raise
    return True


def backup_database(destination,path=None):
    """Export a consistent SQLite snapshot backup to a staff-selected external file."""
    src_path=Path(path or db_path())
    services._different([src_path],destination)
    def write(tmp):
        with closing(connect(src_path)) as src_conn, closing(sqlite3.connect(tmp)) as dst_conn:
            src_conn.backup(dst_conn)
    services._atomic_write(destination,write)


def date_range(start,end):
    try:a=date.fromisoformat(start);b=date.fromisoformat(end)
    except (ValueError,TypeError):raise ValueError('Enter dates as YYYY-MM-DD.')
    if a>b:raise ValueError('Start date must not be after end date.')
    return a.isoformat(),b.isoformat()


def report(start,end,path=None,limit=500):
    start,end=date_range(start,end)
    try:
        with closing(connect(path)) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("SELECT * FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC, sale_id DESC LIMIT ?",(start,end,limit)).fetchall()
            totals=conn.execute("SELECT currency,COUNT(*) AS count,COALESCE(SUM(total_cents),0) AS cents FROM sales WHERE voided_at IS NULL AND substr(created_at,1,10) BETWEEN ? AND ? GROUP BY currency ORDER BY currency",(start,end)).fetchall()
            void_count=conn.execute("SELECT COUNT(*) FROM sales WHERE voided_at IS NOT NULL AND substr(created_at,1,10) BETWEEN ? AND ?",(start,end)).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        _quarantine_corrupt_db(path or db_path())
        raise SalesStoreError('Sales database is damaged or unreadable. Restore a backup or archive sales.sqlite3.') from exc
    return {'rows':[dict(r) for r in rows],'totals':[dict(r) for r in totals],'voided_count':int(void_count or 0)}


_CSV_FORMULA_PREFIXES = (
    '=','+','-','@','\t','\r','\n','\v','\f',
    '\uff1d','\uff0b','\uff0d','\uff20','\u2212',
)
_CSV_STRIP_CHARS = ' \t\r\n\v\f\x00\u00a0\u200b\ufeff'


def sanitize_csv_cell(value):
    """Neutralize spreadsheet formula triggers across ASCII/Unicode leading whitespace and fullwidth operators."""
    t = str(value).replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    stripped = t.lstrip(_CSV_STRIP_CHARS)
    if stripped.startswith(_CSV_FORMULA_PREFIXES) or t.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + t
    return t


def export_csv(start,end,destination,path=None):
    start,end=date_range(start,end)
    services._different([path or db_path()],destination)
    def write(tmp):
        try:
            with closing(connect(path)) as conn,open(tmp,'w',encoding='utf-8-sig',newline='') as f:
                w=csv.writer(f)
                w.writerow(['Reference','Date and time','Status','Business','Currency','Total','Services','Void reason'])
                for ref,when,business,currency,total,payload,voided_at,void_reason in conn.execute(
                    "SELECT sale_id,created_at,business,currency,total_cents,items_json,voided_at,void_reason "
                    "FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC",(start,end)
                ):
                    details='; '.join(f"{x['name']} x{x['qty']} @ {x['price']}" for x in json.loads(payload))
                    status_label='VOIDED' if voided_at else 'RECORDED'
                    effective_total='0.00' if voided_at else format(Decimal(total)/100,'.2f')
                    w.writerow([
                        sanitize_csv_cell(ref),
                        sanitize_csv_cell(when),
                        status_label,
                        sanitize_csv_cell(business),
                        sanitize_csv_cell(currency),
                        effective_total,
                        sanitize_csv_cell(details),
                        sanitize_csv_cell(void_reason or ''),
                    ])
        except sqlite3.DatabaseError as exc:
            raise SalesStoreError('Could not export CSV because the local sales database is damaged.') from exc
    services._atomic_write(destination,write)
