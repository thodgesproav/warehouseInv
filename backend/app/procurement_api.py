"""Warehouse replenishment: durable orders and atomic, replay-safe receipts."""
import csv
import hashlib
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .auth import admin_user
from .database import db_session, get_mapping, utcnow
from .inventory import get_provider
from .inventory.local_sync import LocalSyncInventoryProvider, stock_number
from .inventory.base import StockConflict
from .notifications import queue_stock_availability
from .request_workflow import fulfil_request

router = APIRouter(prefix='/api/admin/procurement')


class Selection(BaseModel):
    key: str = Field(max_length=300)
    version: str = Field(max_length=64)


class SelectedRows(BaseModel):
    entries: list[Selection] = Field(min_length=1, max_length=500)


class BulkAction(SelectedRows):
    action: Literal['ordered', 'available']
    batch_id: str = Field(min_length=16, max_length=100)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inventory_provider():
    provider = get_provider()
    if not isinstance(provider, LocalSyncInventoryProvider):
        raise HTTPException(503, 'Ordering requires the local inventory database to be enabled')
    return provider


def procurement_rows(db, provider, mapping):
    provider._require_ready(db)
    columns = set(provider._meta(db, 'columns', []))
    warnings = []
    needed = ('reorder_trigger', 'max_quantity', 'on_order', 'quantity_on_order')
    missing = [key.replace('_', ' ') for key in needed if not mapping.get(key) or mapping[key] not in columns]
    if missing: warnings.append('Ask a Superadmin to map these columns: ' + ', '.join(missing))
    orders = {r['item_id']: dict(r) for r in db.execute("SELECT * FROM procurement_orders WHERE status='ordered'")}
    result = []
    for record in db.execute('SELECT item_id,raw_json FROM local_inventory'):
        raw = json.loads(record['raw_json'])
        product = provider._product(raw, mapping)
        item_id = record['item_id']
        order = orders.get(item_id)
        try:
            maximum = stock_number(raw.get(mapping.get('max_quantity', '')))
            trigger = stock_number(raw.get(mapping.get('reorder_trigger', '')))
            stock = stock_number(raw.get(mapping['stock']))
            external_quantity = stock_number(raw.get(mapping.get('quantity_on_order', '')))
        except StockConflict:
            warnings.append(f"{product['name']}: invalid stock, reorder, maximum or order quantity; check item details.")
            continue
        external = product['on_order'] or external_quantity > 0
        if not order and (maximum == 0 or (not external and not (stock < trigger and stock < maximum))):
            continue
        quantity = order['quantity'] if order else external_quantity if external else maximum - stock
        key = f"order:{order['id']}" if order else f"external:{item_id}" if external else f"stock:{item_id}"
        result.append({'key': key, 'kind': 'inventory', 'item_id': item_id,
                       'status': 'ordered' if order or external else 'new',
                       'manufacturer': product['manufacturer'], 'master_number': product['model'],
                       'description': str(raw.get(mapping.get('description', '')) or product['name']),
                       'stock': stock, 'trigger': trigger, 'maximum': maximum, 'quantity': quantity,
                       'discontinued': product.get('discontinued', False), 'requested_by': '',
                       'blocked': 'Set a positive Quantity On Order in the source sheet before receiving this existing order.' if external and not order and quantity <= 0 else ''})
    for request in db.execute("SELECT * FROM item_requests WHERE status IN ('new','ordered','available') AND inventory_item_id IS NULL ORDER BY id"):
        result.append({'key': f"request:{request['id']}", 'kind': 'request', 'request_id': request['id'],
                       'status': request['status'], 'manufacturer': request['manufacturer'],
                       'master_number': request['manufacturer_model'], 'description': request['item_requested'],
                       'stock': None, 'trigger': None, 'maximum': None, 'quantity': request['quantity'],
                       'discontinued': False, 'requested_by': request['requested_by_name'], 'blocked': '',
                       'updated_at': request['updated_at']})
    for row in result: row['version'] = fingerprint(row)
    return {'items': result, 'warnings': warnings, 'order_columns_ready': not missing}


def selected_rows(data, current):
    lookup = {row['key']: row for row in current['items']}
    chosen = []
    if len({entry.key for entry in data.entries}) != len(data.entries):
        raise HTTPException(422, 'Select each row only once')
    for entry in data.entries:
        row = lookup.get(entry.key)
        if not row or row['version'] != entry.version:
            raise HTTPException(409, 'The ordering list changed. Refresh, review quantities and try again. No changes were made.')
        if row['blocked']: raise HTTPException(409, row['blocked'])
        chosen.append(row)
    return chosen


@router.get('')
def list_procurement(_: dict = Depends(admin_user)):
    provider, mapping = inventory_provider(), get_mapping()
    with db_session() as db:
        db.execute('BEGIN')
        return procurement_rows(db, provider, mapping)


def safe_csv(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


@router.post('/export')
def export_procurement(data: SelectedRows, _: dict = Depends(admin_user)):
    provider, mapping = inventory_provider(), get_mapping()
    with db_session() as db:
        db.execute('BEGIN')
        selected = selected_rows(data, procurement_rows(db, provider, mapping))
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Manufacturer', 'Quantity', 'Master Number', 'Description'])
    for row in selected:
        writer.writerow([safe_csv(row['manufacturer']), row['quantity'], safe_csv(row['master_number']), safe_csv(row['description'])])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="procurement.csv"', 'Cache-Control': 'no-store'})


@router.post('/actions')
def bulk_action(data: BulkAction, user: dict = Depends(admin_user)):
    provider, mapping = inventory_provider(), get_mapping()
    payload_hash = fingerprint({**data.model_dump(), 'user_id': user['id']})
    with db_session() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM procurement_batches WHERE batch_id=?', (data.batch_id,)).fetchone()
        if previous:
            if previous['payload_hash'] != payload_hash: raise HTTPException(409, 'This action identifier has already been used')
            return json.loads(previous['result_json'])
        current = procurement_rows(db, provider, mapping)
        chosen = selected_rows(data, current)
        if any(r['kind'] == 'inventory' for r in chosen) and not current['order_columns_ready']:
            raise HTTPException(409, 'Configure the reorder, maximum and order column mappings first')
        if data.action == 'available' and any(r['kind'] == 'inventory' and r['status'] != 'ordered' for r in chosen):
            raise HTTPException(409, 'Mark inventory items ordered before receiving them')
        changed = 0
        for row in chosen:
            if row['kind'] == 'request':
                if row['status'] == data.action and data.action == 'ordered': continue
                fulfil_request(db, provider, mapping, row['request_id'], data.action, user)
                changed += 1
                continue
            item_id, quantity = row['item_id'], row['quantity']
            if data.action == 'ordered':
                if row['status'] == 'ordered': continue
                db.execute('INSERT INTO procurement_orders(item_id,quantity,created_at,created_by) VALUES(?,?,?,?)',
                           (item_id, quantity, utcnow(), user['id']))
                provider.update_item_in_transaction(db, item_id, {mapping['on_order']: True, mapping['quantity_on_order']: quantity}, mapping)
            else:
                order_id = int(row['key'].split(':', 1)[1]) if row['key'].startswith('order:') else None
                before = provider._row(db, item_id)
                updated = provider.update_item_in_transaction(db, item_id,
                    {mapping['stock']: row['stock'] + quantity, mapping['on_order']: False, mapping['quantity_on_order']: 0}, mapping)
                if order_id:
                    db.execute("UPDATE procurement_orders SET status='received',received_at=? WHERE id=?", (utcnow(), order_id))
                else:
                    db.execute("INSERT INTO procurement_orders(item_id,quantity,status,created_at,received_at,created_by) VALUES(?,?,'received',?,?,?)",
                               (item_id, quantity, utcnow(), utcnow(), user['id']))
                db.execute("INSERT INTO transactions(created_at,user_id,username,item_id,item_name,quantity,old_soh,new_soh,transaction_type,success,sync_status,sync_operation_id) VALUES(?,?,?,?,?,?,?,?,'order_received',1,'pending',?)",
                           (utcnow(), user['id'], user['username'], item_id, updated['name'], quantity, row['stock'], updated['stock'], updated.get('sync_operation_id')))
                queue_stock_availability(db, {item_id: before}, {item_id: updated['raw_fields']}, mapping)
            changed += 1
        result = {'changed': changed, 'status': data.action}
        db.execute('INSERT INTO procurement_batches VALUES(?,?,?)', (data.batch_id, payload_hash, json.dumps(result)))
        return result
