"""Local sales records, independent of GUI. No payment processing or tax logic."""
import csv,json,shutil,sqlite3,uuid
import license_guard
from contextlib import closing
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from pathlib import Path
import settings_store
import services


class SalesStoreError(ValueError):
    """Raised when the local sales SQLite file cannot complete an operation."""


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


def _database_error(exc,target):
    """Do not misdiagnose a busy/read-only/full disk as database corruption."""
    code=(getattr(exc,'sqlite_errorcode',0) or 0) & 0xff
    if code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
        return SalesStoreError('Sales database is busy or locked. Close other app instances and retry. Completed saved sales remain in the local retry queue when available.')
    if code==sqlite3.SQLITE_FULL:
        return SalesStoreError('Sales database could not be written because the disk is full. Free space and retry the pending saved sales.')
    if code==sqlite3.SQLITE_READONLY:
        return SalesStoreError('Sales database is read-only. Check folder permissions and retry the pending saved sales.')
    if code in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):
        _quarantine_corrupt_db(target)
        name=Path(target).name
        return SalesStoreError(f'Local sales database ({name}) is damaged or unreadable. A quarantine copy ({name}.corrupt) was saved when possible; restore a backup or archive the damaged file, then retry pending saved sales.')
    return SalesStoreError('Could not access the local sales database: '+str(exc)+'. Check storage and permissions, then retry pending saved sales.')


def connect(path=None,timeout=10):
    target=Path(path or db_path()); target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        conn=sqlite3.connect(target,timeout=timeout)
        try: settings_store._harden(target)
        except Exception: pass
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute(f'PRAGMA busy_timeout={int(max(0,timeout)*1000)}')
        conn.execute('''CREATE TABLE IF NOT EXISTS sales (
            sale_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, station TEXT NOT NULL DEFAULT '',
            business TEXT NOT NULL, currency TEXT NOT NULL, total_cents INTEGER NOT NULL,
            items_json TEXT NOT NULL, voided_at TEXT, void_reason TEXT)''')
        cols={row[1] for row in conn.execute('PRAGMA table_info(sales)').fetchall()}
        if 'station' not in cols:
            conn.execute("ALTER TABLE sales ADD COLUMN station TEXT NOT NULL DEFAULT ''")
        if 'voided_at' not in cols:
            conn.execute('ALTER TABLE sales ADD COLUMN voided_at TEXT')
        if 'void_reason' not in cols:
            conn.execute('ALTER TABLE sales ADD COLUMN void_reason TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS sales_date_idx ON sales(created_at)')
        conn.execute('CREATE TABLE IF NOT EXISTS sales_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.commit()
        return conn
    except sqlite3.DatabaseError as exc:
        try: conn.close()
        except Exception: pass
        raise _database_error(exc,target) from exc


def validate_items(items):
    if not isinstance(items,list) or not items or len(items)>500:raise ValueError('A sale must have between 1 and 500 service lines.')
    out=[];total=0
    for item in items:
        if not isinstance(item,dict) or not all(k in item for k in ('name','qty','price')):
            raise ValueError('Invalid service line.')
        name=str(item['name']).strip()
        if not name or len(name)>80:raise ValueError('Invalid service name.')
        try:
            raw_qty=item['qty']
            if isinstance(raw_qty,bool) or not (type(raw_qty) is int or (isinstance(raw_qty,str) and raw_qty.isascii() and raw_qty.isdigit())):
                raise ValueError()
            qty=int(raw_qty);value=Decimal(str(item['price']))
            if not 1<=qty<=999 or not value.is_finite() or value<0 or value>10000000 or value.as_tuple().exponent < -2:raise ValueError()
        except (ValueError,TypeError,InvalidOperation):raise ValueError('Invalid quantity or price.')
        cents=int(value*100);total+=cents*qty;out.append({'name':name,'qty':qty,'price':format(value,'.2f')})
    return out,total


def record_sale(sale_id,business,currency,items,created_at=None,path=None,station=None):
    if not isinstance(sale_id,str) or not sale_id or len(sale_id)>64:raise ValueError('Invalid sale reference.')
    business=settings_store.clean_name(business);currency=str(currency).strip()
    if not currency or len(currency)>8 or any(c in currency for c in '\n\r|'):raise ValueError('Invalid currency.')
    clean_station=' '.join(str(station if station is not None else settings_store.load_settings().get('station_name','')).split())[:40]
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
            conn.execute('INSERT INTO sales(sale_id,created_at,station,business,currency,total_cents,items_json) VALUES(?,?,?,?,?,?,?)',(sale_id,when,clean_station,business,currency,total,payload));conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            raise _database_error(exc,path or db_path()) from exc
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
            raise _database_error(exc,path or db_path()) from exc
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
            by_station=conn.execute("SELECT CASE WHEN TRIM(COALESCE(station,''))='' THEN 'Unlabeled PC' ELSE station END AS station_label, currency, COUNT(*) AS count, COALESCE(SUM(total_cents),0) AS cents FROM sales WHERE voided_at IS NULL AND substr(created_at,1,10) BETWEEN ? AND ? GROUP BY station_label, currency ORDER BY station_label, currency",(start,end)).fetchall()
            void_count=conn.execute("SELECT COUNT(*) FROM sales WHERE voided_at IS NOT NULL AND substr(created_at,1,10) BETWEEN ? AND ?",(start,end)).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise _database_error(exc,path or db_path()) from exc
    return {
        'rows':[dict(r) for r in rows],
        'totals':[dict(r) for r in totals],
        'by_station':[dict(r) for r in by_station],
        'voided_count':int(void_count or 0),
    }


def export_sales_records(start=None,end=None,path=None,limit=None):
    """Return normalized sale dicts suitable for multi-PC JSON/LAN/Supabase sync."""
    with closing(connect(path)) as conn:
        conn.row_factory=sqlite3.Row
        if start and end:
            s,e=date_range(start,end)
            rows=conn.execute("SELECT * FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC LIMIT ?",(s,e,limit if limit is not None else -1)).fetchall()
        else:
            rows=conn.execute("SELECT * FROM sales ORDER BY created_at DESC, sale_id DESC LIMIT ?",(limit if limit is not None else -1,)).fetchall()
    out=[]
    for r in rows:
        try: items=json.loads(r['items_json'])
        except Exception: items=[]
        out.append({
            'sale_id':r['sale_id'],
            'created_at':r['created_at'],
            'station':r['station'] if 'station' in r.keys() else '',
            'business':r['business'],
            'currency':r['currency'],
            'total_cents':int(r['total_cents']),
            'items':items,
            'voided_at':r['voided_at'],
            'void_reason':r['void_reason'],
        })
    return out


def merge_external_sales(records,path=None):
    """Merge valid rows; report rejected rows/conflicts without hiding partial success.

    Money, original date and station are immutable. Voids are monotonic, but may
    only attach to an identical original sale, never merely a matching ID.
    """
    if not isinstance(records,list):
        raise ValueError('Sales sync data must be a list of sale records.')
    inserted=0; updated_voids=0; skipped=0; rejected=0; conflicts=0; errors=[]
    def reject(index,reason,conflict=False):
        nonlocal rejected,conflicts
        rejected+=1
        if conflict:conflicts+=1
        if len(errors)<20:errors.append(f'Row {index+1}: {reason}')
    with closing(connect(path)) as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            for index,rec in enumerate(records):
                try:
                    if not isinstance(rec,dict):raise ValueError('Sale must be an object.')
                    sid=rec.get('sale_id')
                    if not isinstance(sid,str) or not sid.strip() or len(sid)>64:raise ValueError('Invalid sale reference.')
                    raw_items=rec.get('items')
                    if raw_items is None and rec.get('items_json'):
                        raw_items=json.loads(str(rec['items_json']))
                    clean_items,calc_cents=validate_items(raw_items)
                    bname=settings_store.clean_name(rec.get('business') or settings_store.DEFAULT_NAME)
                    curr=str(rec.get('currency') or 'R').strip()
                    if not curr or len(curr)>8 or any(c in curr for c in '\n\r|'):raise ValueError('Invalid currency.')
                    when=str(rec.get('created_at') or '').strip()
                    datetime.fromisoformat(when)
                    st=' '.join(str(rec.get('station') or '').split())[:40]
                    v_at=str(rec.get('voided_at') or '').strip() or None
                    if v_at:datetime.fromisoformat(v_at)
                    v_reason=' '.join(str(rec.get('void_reason') or '').split())[:160] or None
                    if 'total_cents' in rec and (type(rec['total_cents']) is not int or rec['total_cents']!=calc_cents):
                        raise ValueError('Sale total does not match its service lines.')
                    payload=json.dumps(clean_items,ensure_ascii=False,separators=(',',':'))
                except (ValueError,TypeError,KeyError,InvalidOperation) as exc:
                    reject(index,str(exc));continue
                existing=conn.execute('SELECT created_at,station,business,currency,total_cents,items_json,voided_at FROM sales WHERE sale_id=?',(sid,)).fetchone()
                immutable=(when,st,bname,curr,calc_cents,payload)
                if existing is None:
                    conn.execute(
                        'INSERT INTO sales(sale_id,created_at,station,business,currency,total_cents,items_json,voided_at,void_reason) VALUES(?,?,?,?,?,?,?,?,?)',
                        (sid,*immutable,v_at,v_reason),
                    )
                    inserted+=1
                else:
                    # Older ledgers may serialize equivalent item JSON with spaces.
                    old_items,_=validate_items(json.loads(existing[5]))
                    old=(*existing[:5],json.dumps(old_items,ensure_ascii=False,separators=(',',':')))
                    if old!=immutable:
                        reject(index,'Sale reference conflicts with immutable recorded details.',True);continue
                    if v_at and not existing[6]:
                        conn.execute('UPDATE sales SET voided_at=?, void_reason=? WHERE sale_id=?',(v_at,v_reason or 'Voided on shop PC',sid))
                        updated_voids+=1
                    else:skipped+=1
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback();raise _database_error(exc,path or db_path()) from exc
        except Exception:
            conn.rollback();raise
    return {'inserted':inserted,'updated_voids':updated_voids,'skipped':skipped,
            'rejected':rejected,'conflicts':conflicts,'errors':errors}


def merge_from_file(source_file,path=None):
    """Merge sales from another PC's `.sqlite3` backup or `.cafesales.json` export."""
    src=Path(source_file)
    if not src.is_file():
        raise FileNotFoundError('Selected sales file was not found.')
    if src.suffix.lower() in ('.json',):
        raw=json.loads(src.read_text(encoding='utf-8'))
        if isinstance(raw,dict) and 'sales' not in raw:
            raise ValueError('Sales file has no sales array.')
        recs=raw['sales'] if isinstance(raw,dict) else raw
        return merge_external_sales(recs,path=path)
    # Otherwise treat as SQLite backup from another PC
    other_records=export_sales_records(path=src)
    return merge_external_sales(other_records,path=path)


def sync_identity(path=None):
    """Stable ledger identity + machine binding; editable station names are labels only.

    A copied backup on a different PC gets a distinct identity. Two ledgers on the
    same PC also remain distinct. Legacy label-based snapshots are read, not erased.
    """
    with closing(connect(path)) as conn:
        conn.execute('INSERT OR IGNORE INTO sales_metadata(key,value) VALUES(?,?)',('sync_uuid',uuid.uuid4().hex))
        seed=conn.execute('SELECT value FROM sales_metadata WHERE key=?',('sync_uuid',)).fetchone()[0]
        conn.commit()
    return uuid.uuid5(uuid.NAMESPACE_OID,license_guard.get_machine_id()+'|'+seed).hex


def sync_lan_folder(folder_path,station_name='',path=None):
    """Full-history snapshots preserve old voids; failures are explicit partial results."""
    folder=Path(str(folder_path or '').strip())
    if not str(folder_path or '').strip():
        raise ValueError('Choose a Shared Network / USB Sync Folder in Business Settings first.')
    folder.mkdir(parents=True,exist_ok=True)
    identity=sync_identity(path)
    own_file=folder/f'sales-{identity}.cafesales.json'
    own_records=export_sales_records(path=path)
    own_payload=json.dumps({'format':'cafedesk-sales-v2','station':station_name,'device_id':identity,'sales':own_records},ensure_ascii=False)
    services._atomic_write(own_file,lambda tmp: Path(tmp).write_text(own_payload,encoding='utf-8'))
    total_in=0; total_void=0; files_read=0; rejected=0; errors=[]
    for peer_file in sorted(folder.glob('sales-*.cafesales.json')):
        if peer_file.resolve()==own_file.resolve():continue
        try:
            res=merge_from_file(peer_file,path=path)
            total_in+=res['inserted'];total_void+=res['updated_voids'];files_read+=1
            rejected+=res['rejected']
            if res['rejected']:errors.append(f"{peer_file.name}: {res['rejected']} rejected row(s). "+'; '.join(res['errors'][:3]))
        except Exception as exc:
            errors.append(f'{peer_file.name}: {exc}')
    return {'inserted':total_in,'updated_voids':total_void,'peer_files':files_read,
            'own_file':str(own_file),'rejected':rejected,'errors':errors,'complete':not errors}



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
                w.writerow(['Reference','Date and time','Computer / Station','Status','Business','Currency','Total','Services','Void reason'])
                for ref,when,station,business,currency,total,payload,voided_at,void_reason in conn.execute(
                    "SELECT sale_id,created_at,station,business,currency,total_cents,items_json,voided_at,void_reason "
                    "FROM sales WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC",(start,end)
                ):
                    details='; '.join(f"{x['name']} x{x['qty']} @ {x['price']}" for x in json.loads(payload))
                    status_label='VOIDED' if voided_at else 'RECORDED'
                    effective_total='0.00' if voided_at else format(Decimal(total)/100,'.2f')
                    w.writerow([
                        sanitize_csv_cell(ref),
                        sanitize_csv_cell(when),
                        sanitize_csv_cell(station or ''),
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
