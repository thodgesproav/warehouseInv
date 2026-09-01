"""Durable email delivery and idempotent Excel journal export.

Both integrations are disabled until an administrator completes Microsoft-side
setup. Email sends are not blindly retried after a timeout; journal exports can
be retried because the Office Script deduplicates stable transaction IDs.
"""
from __future__ import annotations

import fcntl
import html
import json
import re
import threading
import uuid
from urllib.parse import urlparse

from .config import settings
from .database import db_session, utcnow, runtime_setting
from .inventory.power_automate import PowerAutomateInventoryProvider


def config_value(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_config(db, key, value):
    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def request_recipient_users(db):
    """Resolve selected, active admin accounts at send time so stale emails never receive requests."""
    eligible = [dict(row) for row in db.execute(
        "SELECT id,username,display_name,email,access_level AS role FROM users "
        "WHERE role='admin' AND access_level IN ('superadmin','warehouse_admin') "
        "AND disabled=0 AND email!='' ORDER BY display_name COLLATE NOCASE,username COLLATE NOCASE"
    )]
    selected = config_value(db, 'admin_request_user_ids')
    if selected is None:
        # One-time compatibility for installations that stored recipient emails.
        legacy = {str(value).strip().lower() for value in config_value(db, 'admin_request_emails', [])}
        return [user for user in eligible if user['email'].lower() in legacy]
    selected_ids = {value for value in selected if isinstance(value, int) and not isinstance(value, bool)}
    return [user for user in eligible if user['id'] in selected_ids]


def valid_email(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise ValueError('Enter a valid email address')
    if '..' in value or value.startswith('.') or '.@' in value:
        raise ValueError('Enter a valid email address')
    return value


def valid_flow_url(value):
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443) or not any(host.endswith(suffix) for suffix in ('.powerplatform.com', '.logic.azure.com')):
        raise ValueError('Use the HTTPS trigger URL of a Microsoft Power Automate flow')
    return value


def queue_email(db, event_key, recipient_kind, subject, fields, user_id=None):
    body = '<h2>' + html.escape(subject) + '</h2><table>'
    for label, value in fields.items():
        body += '<tr><th align="left">' + html.escape(str(label)) + '</th><td>' + html.escape(str(value or '')) + '</td></tr>'
    body += '</table><p>Warehouse Inventory notification.</p>'
    now = utcnow()
    db.execute('INSERT OR IGNORE INTO notification_jobs(event_key,recipient_kind,user_id,subject,html_body,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
               (event_key, recipient_kind, user_id, subject, body, now, now))


def queue_stock_availability(db, old_rows, new_rows, mapping):
    """One notification per opt-in; only after a confirmed zero-to-positive transition."""
    def stock(row):
        try: return float(row.get(mapping['stock']) or 0)
        except (TypeError, ValueError): return 0
    for item_id, raw in new_rows.items():
        old = old_rows.get(item_id)
        if old is None or stock(old) > 0 or stock(raw) <= 0:
            continue
        watches = db.execute('SELECT w.user_id,w.created_at FROM stock_watches w JOIN users u ON u.id=w.user_id WHERE w.item_id=? AND w.active=1 AND u.disabled=0 AND u.email_notifications=1 AND u.email!=?', (item_id, '')).fetchall()
        for watch in watches:
            queue_email(db, f"stock:{item_id}:{watch['user_id']}:{watch['created_at']}", 'user', 'An item you follow is now available',
                        {'Item': raw.get(mapping['name'], item_id), 'Quantity available': raw.get(mapping['stock']), 'Item ID': item_id}, watch['user_id'])
            db.execute('UPDATE stock_watches SET active=0 WHERE user_id=? AND item_id=?', (watch['user_id'], item_id))


def delivery_settings():
    with db_session() as db:
        eligible = [dict(row) for row in db.execute(
            "SELECT id,username,display_name,email,access_level AS role FROM users "
            "WHERE role='admin' AND access_level IN ('superadmin','warehouse_admin') "
            "AND disabled=0 AND email!='' ORDER BY display_name COLLATE NOCASE,username COLLATE NOCASE"
        )]
        selected = request_recipient_users(db)
        return {'admin_emails': [user['email'] for user in selected],
                'recipient_users': eligible,
                'selected_user_ids': [user['id'] for user in selected],
                'email_enabled': config_value(db, 'email_enabled', False),
                'email_flow_configured': bool(config_value(db, 'email_flow_url', '')),
                'transaction_export_enabled': config_value(db, 'transaction_export_enabled', False),
                'journal_error': config_value(db, 'journal_error'),
                'last_journal_export': config_value(db, 'last_journal_export'),
                'unexported_transactions': db.execute('SELECT COUNT(*) FROM transactions WHERE excel_exported=0').fetchone()[0],
                'email_counts': {row[0]: row[1] for row in db.execute('SELECT state,COUNT(*) FROM notification_jobs GROUP BY state')},
                'recent_emails': [dict(row) for row in db.execute('SELECT id,subject,state,error,created_at FROM notification_jobs ORDER BY id DESC LIMIT 20')]}


class DeliveryWorker:
    def __init__(self, transport=None):
        self.transport = transport or PowerAutomateInventoryProvider()
        self.stop_event = threading.Event()
        self.thread = None

    def send_emails(self):
        with db_session() as db:
            enabled = config_value(db, 'email_enabled', False)
            url = config_value(db, 'email_flow_url', '')
            if not enabled or not url: return
            # A process may have stopped after Outlook sent the message.
            db.execute("UPDATE notification_jobs SET state='uncertain',error='Delivery was interrupted; check flow history before retrying' WHERE state='sending'")
            jobs = [dict(row) for row in db.execute("SELECT * FROM notification_jobs WHERE state='queued' ORDER BY id LIMIT 10")]
        for job in jobs:
            if self.stop_event.is_set(): return
            with db_session() as db:
                db.execute('BEGIN IMMEDIATE')
                if not config_value(db, 'email_enabled', False): return
                if job['recipient_kind'] == 'admins': recipients = [user['email'] for user in request_recipient_users(db)]
                else:
                    user = db.execute('SELECT email,email_notifications,disabled FROM users WHERE id=?', (job['user_id'],)).fetchone()
                    if not user or user['disabled'] or not user['email_notifications'] or not user['email']:
                        db.execute("UPDATE notification_jobs SET state='cancelled',updated_at=? WHERE id=? AND state='queued'", (utcnow(), job['id']))
                        continue
                    recipients = [user['email']]
                if not recipients: continue
                changed = db.execute("UPDATE notification_jobs SET state='sending',updated_at=? WHERE id=? AND state='queued'", (utcnow(), job['id']))
                if changed.rowcount != 1: continue
            try:
                result = self.transport._post(url, {'to': ';'.join(recipients), 'subject': job['subject'], 'htmlBody': job['html_body'], 'eventId': job['event_key']})
                if not isinstance(result, dict) or not result.get('ok'): raise ValueError('Delivery not confirmed')
                state, error = 'sent', None
            except Exception:
                state, error = 'uncertain', 'Delivery not confirmed. Check the email flow before retrying to avoid duplicate emails.'
            with db_session() as db:
                db.execute('UPDATE notification_jobs SET state=?,error=?,updated_at=? WHERE id=?', (state, error, utcnow(), job['id']))

    def export_transactions(self):
        if not runtime_setting('inventory_sync_enabled', True): return
        with db_session() as db:
            if not config_value(db, 'transaction_export_enabled', False): return
            if settings.inventory_provider != 'power_automate':
                set_config(db, 'journal_error', 'Transaction export requires the Power Automate provider')
                return
            identity = config_value(db, 'journal_identity')
            if not identity:
                identity = str(uuid.uuid4())
                set_config(db, 'journal_identity', identity)
            records = [dict(row) for row in db.execute("SELECT * FROM transactions WHERE excel_exported=0 AND (sync_operation_id IS NULL OR sync_status IN ('synced','discarded')) ORDER BY id LIMIT 100")]
        if not records: return
        payload = [{'transactionId': f"{identity}:{row['id']}", 'timestamp': row['created_at'], 'user': row['username'],
                    'itemId': row['item_id'], 'item': row['item_name'], 'quantity': row['quantity'],
                    'oldStock': row['old_soh'], 'newStock': row['new_soh'], 'type': row['transaction_type'],
                    'outcome': 'discarded' if row['sync_status'] == 'discarded' else 'applied' if row['success'] else 'rejected'} for row in records]
        try:
            response = self.transport._post(runtime_setting('inventory_update_url', settings.update_url), {'action': 'logTransactions', 'fields': {'transactions': payload}})
            confirmed = set(response.get('loggedIds', []))
            if not response.get('ok') or not confirmed.issuperset(record['transactionId'] for record in payload):
                raise ValueError('Journal acknowledgement missing')
            with db_session() as db:
                db.executemany('UPDATE transactions SET excel_exported=1 WHERE id=?', [(row['id'],) for row in records])
                set_config(db, 'journal_error', None)
                set_config(db, 'last_journal_export', utcnow())
        except Exception:
            with db_session() as db:
                set_config(db, 'journal_error', 'Transaction export was not confirmed. Install the updated Office Script; a later pass will retry safely using transaction IDs.')

    def run_once(self):
        with settings.database_path.with_suffix('.delivery.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            self.send_emails()
            self.export_transactions()

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try: self.run_once()
                except Exception: pass  # The next pass retries DB/file availability; never log signed URLs.
                self.stop_event.wait(15)
        self.thread = threading.Thread(target=run, daemon=True, name='inventory-delivery')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
