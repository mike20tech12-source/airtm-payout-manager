from io import BytesIO
from openpyxl import Workbook
from .db import db

def transactions_xlsx():
    wb=Workbook(); ws=wb.active; ws.title='Transactions'
    headers=['ID','External ID','Sender','Amount','Currency','Status','Profile','Profile Version','Created','Completed']
    ws.append(headers)
    with db() as c: rows=c.execute('SELECT id,external_id,sender_email,amount,currency,status,profile_id,profile_version,created_at,completed_at FROM transactions ORDER BY id DESC').fetchall()
    for r in rows: ws.append(list(r))
    out=BytesIO(); wb.save(out); out.seek(0); return out
