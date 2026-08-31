"""Local-first inventory with a durable outbox and a single background sync worker.

Network calls never hold a SQLite write transaction. A pull is reconciled with
the *current* outbox, including edits queued while the pull/write was in flight.
Ambiguous writes are inspected, not blindly retried (the legacy flow has no
idempotency key). Excel remains editable; overlapping edits are flagged.
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..database import db_session, get_mapping, utcnow, runtime_setting
from .base import InsufficientStock, InventoryProvider, ItemNotFound, StockConflict, SyncUnavailable
from .power_automate import PowerAutomateInventoryProvider

log = logging.getLogger(__name__)
ACTIVE = ('pending', 'sending', 'uncertain', 'conflict')


def same(a: Any, b: Any) -> bool:
    # The edit form sends strings; Excel usually returns numbers and booleans.
    if a is None: a = ''
    if b is None: b = ''
    return str(a) == str(b)


def stock_number(value: Any) -> int:
    try:
        number = float(value or 0)
        if not math.isfinite(number) or not number.is_integer() or number < 0:
            raise ValueError()
        return int(number)
    except (ValueError, TypeError):
        raise StockConflict('Stock must be a non-negative whole number') from None


class LocalSyncInventoryProvider(InventoryProvider):
    def __init__(self, remote=None, interval: int | None = None):
        self.remote = remote or PowerAutomateInventoryProvider()
        self._default_interval = max(10, interval or settings.sync_interval_seconds)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._cycle_lock = threading.Lock()
        self._syncing = False
        self._next_sync: float | None = None

    @property
    def interval(self):
        return int(runtime_setting('sync_interval_seconds', self._default_interval))

    @staticmethod
    def _meta(db, key, default=None):
        row = db.execute('SELECT value FROM inventory_sync_meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set_meta(db, key, value):
        db.execute('INSERT OR REPLACE INTO inventory_sync_meta VALUES (?,?)', (key, json.dumps(value)))

    @staticmethod
    def _active(db):
        return [dict(row) for row in db.execute("SELECT * FROM inventory_outbox WHERE state IN ('pending','sending','uncertain','conflict') ORDER BY sequence")]

    @staticmethod
    def _put(db, item_id, raw):
        db.execute('INSERT OR REPLACE INTO local_inventory VALUES (?,?)', (item_id, json.dumps(raw)))

    def _require_ready(self, db):
        if not self._meta(db, 'initialised', False):
            raise SyncUnavailable('Initial Excel download is still in progress. Please try again shortly.')

    def _row(self, db, item_id):
        self._require_ready(db)
        row = db.execute('SELECT raw_json FROM local_inventory WHERE item_id=?', (item_id,)).fetchone()
        if not row: raise ItemNotFound('Item no longer exists; refresh the inventory')
        return json.loads(row[0])

    def _queue(self, db, item_id, kind, base, patch):
        operation = str(uuid.uuid4())
        now = utcnow()
        db.execute('INSERT INTO inventory_outbox(operation_id,item_id,kind,base_json,patch_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                   (operation, item_id, kind, json.dumps(base), json.dumps(patch), now, now))
        return operation

    def _product(self, raw, mapping, operation=None):
        product = PowerAutomateInventoryProvider._normalise(raw, mapping)
        if operation:
            product.update(sync_status='pending', sync_operation_id=operation)
        return product

    def get_inventory(self, force=False):
        if force: self.request_sync()
        mapping = get_mapping()
        with db_session() as db:
            products = [self._product(json.loads(row[0]), mapping) for row in db.execute('SELECT raw_json FROM local_inventory ORDER BY rowid')]
            pending = {}
            for row in self._active(db):
                if pending.get(row['item_id']) not in ('conflict', 'uncertain'):
                    pending[row['item_id']] = row['state']
        for product in products:
            product['sync_status'] = pending.get(product['id'], 'synced')
        return products

    def get_columns(self):
        with db_session() as db: return self._meta(db, 'columns', [])

    def adjust_stock(self, item_id, quantity, expected_current_soh):
        mapping = get_mapping()
        with db_session() as db:
            db.execute('BEGIN IMMEDIATE')
            raw = self._row(db, item_id)
            old = stock_number(raw.get(mapping['stock']))
            if old != expected_current_soh: raise StockConflict(f'Current local stock is {old}')
            if old + quantity < 0: raise InsufficientStock(f'Only {old} are currently available')
            patch = {mapping['stock']: old + quantity}
            operation = self._queue(db, item_id, 'stock', raw, patch)
            raw.update(patch)
            self._put(db, item_id, raw)
        return {**self._product(raw, mapping, operation), 'old_stock': old}

    def update_item(self, item_id, fields, base_fields=None):
        mapping = get_mapping()
        with db_session() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.update_item_in_transaction(db, item_id, fields, mapping, base_fields)

    def update_item_in_transaction(self, db, item_id, fields, mapping, base_fields=None):
        operation = None
        raw = self._row(db, item_id)
        allowed = set(self._meta(db, 'columns', []))
        if any(key not in allowed for key in fields):
            raise StockConflict('The available fields changed while this form was open. Reopen the item and try again.')
        if base_fields is not None and any(k in allowed and k in base_fields and not same(raw.get(k), base_fields[k]) and not same(v, raw.get(k)) for k, v in fields.items()):
            raise StockConflict('This item changed while the edit form was open. Close it, refresh, and try again.')
        patch = {k: v for k, v in fields.items() if k in allowed and k != mapping['id'] and not same(v, raw.get(k))}
        stock = mapping['stock']
        if stock in patch:
            value = stock_number(patch.pop(stock))
            if value != stock_number(raw.get(stock)):
                operation = self._queue(db, item_id, 'stock', raw, {stock: value})
                raw[stock] = value
        if patch:
            edit_operation = self._queue(db, item_id, 'update', raw, patch)
            operation = operation or edit_operation
            raw.update(patch)
        self._put(db, item_id, raw)
        return self._product(raw, mapping, operation)

    def add_item(self, fields):
        mapping = get_mapping()
        with db_session() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.add_item_in_transaction(db, fields, mapping)

    def add_item_in_transaction(self, db, fields, mapping):
        """Let request fulfilment, stock, audit and notification commit together."""
        self._require_ready(db)
        raw = {key: fields.get(key, '') for key in self._meta(db, 'columns', [])}
        item_id = str(raw.get(mapping['id']) or f'INV-{uuid.uuid4().hex}').strip()
        if db.execute('SELECT 1 FROM local_inventory WHERE item_id=?', (item_id,)).fetchone() or any(op['item_id'] == item_id for op in self._active(db)):
            raise StockConflict('That inventory ID already exists or has pending changes')
        raw[mapping['id']] = item_id
        raw[mapping['stock']] = stock_number(raw.get(mapping['stock']))
        operation = self._queue(db, item_id, 'add', None, raw)
        self._put(db, item_id, raw)
        return self._product(raw, mapping, operation)

    def delete_item(self, item_id):
        with db_session() as db:
            db.execute('BEGIN IMMEDIATE')
            raw = self._row(db, item_id)
            operation = self._queue(db, item_id, 'delete', raw, {})
            db.execute('DELETE FROM local_inventory WHERE item_id=?', (item_id,))
            db.execute("UPDATE procurement_orders SET status='cancelled' WHERE item_id=? AND status='ordered'", (item_id,))
        return operation

    def get_sync_status(self):
        interval = self.interval
        paused = not runtime_setting('inventory_sync_enabled', True)
        with db_session() as db:
            ops = self._active(db)
            error = self._meta(db, 'error')
            conflicts = sum(op['state'] in ('conflict', 'uncertain') for op in ops)
            return {'provider': 'power_automate', 'mode': 'local_first', 'ok': not error and not conflicts,
                    'ready': self._meta(db, 'initialised', False), 'last_sync': self._meta(db, 'last_sync'),
                    'error': error, 'cached': True, 'pending_count': len(ops), 'conflict_count': conflicts,
                    'syncing': self._syncing, 'interval_seconds': interval, 'paused': paused,
                    'next_sync': datetime.fromtimestamp(self._next_sync, timezone.utc).isoformat() if self._next_sync else None}

    def get_conflicts(self):
        mapping = get_mapping()
        with db_session() as db:
            result = []
            for op in self._active(db):
                if op['state'] not in ('conflict', 'uncertain'): continue
                row = db.execute('SELECT raw_json FROM excel_snapshot WHERE item_id=?', (op['item_id'],)).fetchone()
                remote = json.loads(row[0]) if row else None
                base, patch = json.loads(op['base_json']), json.loads(op['patch_json'])
                result.append({'operation_id': op['operation_id'], 'item_id': op['item_id'], 'state': op['state'],
                               'kind': op['kind'], 'message': op['message'], 'local': patch, 'excel': remote,
                               'name': (remote or base or patch).get(mapping['name'], op['item_id'])})
            return result

    def use_excel(self, item_id):
        # Keep the discarded outbox records for diagnosis/audit; never erase them.
        if not self._cycle_lock.acquire(blocking=False):
            raise StockConflict('A sync is in progress. Wait for it to finish before resolving this item.')
        try:
            with db_session() as db:
                db.execute('BEGIN IMMEDIATE')
                ops = [op for op in self._active(db) if op['item_id'] == item_id]
                if not any(op['state'] in ('conflict', 'uncertain') for op in ops):
                    raise StockConflict('This item no longer has a conflict; refresh the page')
                for op in ops: self._finish(db, op, 'discarded', 'User chose the Excel version')
                row = db.execute('SELECT raw_json FROM excel_snapshot WHERE item_id=?', (item_id,)).fetchone()
                if row: self._put(db, item_id, json.loads(row[0]))
                else: db.execute('DELETE FROM local_inventory WHERE item_id=?', (item_id,))
        finally: self._cycle_lock.release()
        self.request_sync()

    @staticmethod
    def _finish(db, op, state, message=None):
        db.execute('UPDATE inventory_outbox SET state=?,message=?,updated_at=? WHERE operation_id=?',
                   (state, message, utcnow(), op['operation_id']))
        status = 'synced' if state == 'done' else state
        db.execute('UPDATE transactions SET sync_status=? WHERE sync_operation_id=?', (status, op['operation_id']))

    @staticmethod
    def _matches(remote, patch):
        return remote is not None and all(same(remote.get(k), v) for k, v in patch.items())

    def _send(self, op, remote_rows, mapping):
        item_id, kind = op['item_id'], op['kind']
        current = remote_rows.get(item_id)
        base, patch = json.loads(op['base_json']), json.loads(op['patch_json'])
        if op['state'] in ('sending', 'uncertain'):
            confirmed = current is None if kind == 'delete' else self._matches(current, patch)
            return ('done', None) if confirmed else ('uncertain', 'Write outcome is unknown. Check the flow run and Excel before resolving; it will not be resent automatically.')
        if op['state'] == 'conflict': return ('conflict', op['message'])
        if kind == 'add':
            if current is not None: return ('conflict', 'Inventory ID already exists in Excel')
        elif kind == 'delete':
            if current is None: return ('done', None)
            if not self._matches(current, base) or set(current) != set(base):
                return ('conflict', 'Excel changed this item before the queued deletion')
        else:
            if current is None: return ('conflict', 'Item was deleted in Excel while local edits were queued')
            if kind == 'stock':
                stock = mapping['stock']
                if stock_number(current.get(stock)) != stock_number(base.get(stock)):
                    return ('conflict', 'Stock changed in both Excel and the app; no stock write was sent')
            else:
                if any(k not in current for k in patch):
                    return ('conflict', 'An edited column was removed from Excel')
                if any(not same(current.get(k), base.get(k)) and not same(current.get(k), v) for k, v in patch.items()):
                    return ('conflict', 'The same field changed in both Excel and the app')
                if self._matches(current, patch): return ('done', None)
        with db_session() as db: self._finish(db, op, 'sending')
        try:
            if kind == 'delete':
                self.remote.delete_item(item_id)
                remote_rows.pop(item_id, None)
            else:
                if kind == 'add': result = self.remote.add_item(patch)
                elif kind == 'stock':
                    stock = mapping['stock']
                    old = stock_number(current.get(stock))
                    result = self.remote.adjust_stock(item_id, stock_number(patch[stock]) - old, old)
                else: result = self.remote.update_item(item_id, patch)
                raw = result.get('raw_fields')
                if not isinstance(raw, dict) or not self._matches(raw, patch):
                    return ('uncertain', 'Excel did not confirm the requested values; inspect the flow before retrying')
                remote_rows[item_id] = raw
            return ('done', None)
        except (StockConflict, InsufficientStock):
            return ('conflict', 'Excel rejected the stock update because its current stock differs')
        except Exception:
            return ('uncertain', 'Excel did not confirm the write. The next sync will check its result; it will not be blindly retried.')

    def _reconcile(self, db, remote_rows):
        from ..notifications import queue_stock_availability
        previous = {row[0]: json.loads(row[1]) for row in db.execute('SELECT item_id,raw_json FROM excel_snapshot')}
        # This hook shares the snapshot transaction, so alerts cannot be lost or
        # emitted twice if the process stops during reconciliation.
        mapping = json.loads(db.execute("SELECT value FROM settings WHERE key='column_mapping'").fetchone()[0])
        queue_stock_availability(db, previous, remote_rows, mapping)
        db.execute('DELETE FROM excel_snapshot')
        db.executemany('INSERT INTO excel_snapshot VALUES (?,?)', [(key, json.dumps(row)) for key, row in remote_rows.items()])
        merged = {key: dict(row) for key, row in remote_rows.items()}
        for op in self._active(db):
            item_id = op['item_id']
            if op['kind'] == 'delete': merged.pop(item_id, None)
            else:
                base = json.loads(op['base_json']) or {}
                merged[item_id] = {**merged.get(item_id, base), **json.loads(op['patch_json'])}
        db.execute('DELETE FROM local_inventory')
        db.executemany('INSERT INTO local_inventory VALUES (?,?)', [(key, json.dumps(row)) for key, row in merged.items()])

    def sync_once(self):
        if not runtime_setting('inventory_sync_enabled', True): return
        if not self._cycle_lock.acquire(blocking=False): return
        lock_file = None
        self._syncing = True
        try:
            if not runtime_setting('inventory_sync_enabled', True): return
            # Prevent multiple processes from transmitting the same outbox.
            lock_file = settings.database_path.with_suffix('.sync.lock').open('a')
            try: fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            products = self.remote.get_live_inventory()
            mapping = get_mapping()
            remote_rows = {}
            for product in products:
                item_id = str(product['id']).strip()
                if not item_id or item_id in remote_rows:
                    raise SyncUnavailable('Excel has blank or duplicate Inventory IDs; sync paused without applying changes')
                raw = product['raw_fields']
                if mapping['id'] not in raw or mapping['stock'] not in raw:
                    raise SyncUnavailable('Excel is missing the mapped ID or stock column')
                stock_number(raw.get(mapping['stock']))
                remote_rows[item_id] = raw
            columns = list(next(iter(remote_rows.values()))) if remote_rows else self.remote.get_columns()
            with db_session() as db: operations = self._active(db)
            blocked = set()
            for op in operations[:50]:
                if self._stop.is_set(): break
                if op['item_id'] in blocked: continue
                state, message = self._send(op, remote_rows, mapping)
                with db_session() as db: self._finish(db, op, state, message)
                if state != 'done': blocked.add(op['item_id'])
            with db_session() as db:
                db.execute('BEGIN IMMEDIATE')
                self._reconcile(db, remote_rows)
                self._set_meta(db, 'columns', columns)
                self._set_meta(db, 'initialised', True)
                self._set_meta(db, 'last_sync', utcnow())
                self._set_meta(db, 'error', None)
        except Exception as exc:
            message = str(exc) if isinstance(exc, (SyncUnavailable, StockConflict)) else f'Sync failed ({type(exc).__name__}); local changes are retained'
            with db_session() as db: self._set_meta(db, 'error', message)
            log.warning('Inventory sync: %s', message)
        finally:
            if lock_file: lock_file.close()
            self._syncing = False
            self._next_sync = time.time() + self.interval
            self._cycle_lock.release()

    def request_sync(self):
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                self._wake.clear()
                self.sync_once()
                self._wake.wait(self.interval)
        self._thread = threading.Thread(target=run, name='inventory-sync', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread: self._thread.join(timeout=2)
