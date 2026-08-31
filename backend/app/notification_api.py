from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pathlib import Path
import os
import sqlite3
import tempfile
import fcntl

from .auth import current_user, superadmin_user
from .config import settings
from .database import db_session, utcnow
from .inventory import get_provider
from .notifications import config_value, set_config, valid_email, valid_flow_url, delivery_settings

router = APIRouter(prefix='/api')


@router.get('/admin/database/export')
def export_database(_=Depends(superadmin_user)):
    # SQLite's backup API includes committed WAL changes in a consistent snapshot.
    descriptor, filename = tempfile.mkstemp(prefix='inventory-backup-', suffix='.sqlite')
    os.close(descriptor)
    path = Path(filename)
    try:
        with db_session() as source, sqlite3.connect(filename) as target:
            source.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
        return FileResponse(path, media_type='application/vnd.sqlite3',
                            filename='inventory-backup-' + utcnow().replace(':', '-') + '.sqlite',
                            headers={'Cache-Control': 'no-store'},
                            background=BackgroundTask(path.unlink, missing_ok=True))
    except Exception:
        path.unlink(missing_ok=True)
        raise


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email_notifications: bool = False


class DeliverySettings(BaseModel):
    admin_emails: list[str] = Field(default_factory=list, max_length=20)
    email_enabled: bool = False
    email_flow_url: str | None = Field(default=None, max_length=4000)
    transaction_export_enabled: bool = False


@router.get('/me/preferences')
def preferences(user=Depends(current_user)):
    with db_session() as db:
        row = db.execute('SELECT email,email_notifications FROM users WHERE id=?', (user['id'],)).fetchone()
        return {**dict(row), 'watching': [r[0] for r in db.execute('SELECT item_id FROM stock_watches WHERE user_id=? AND active=1', (user['id'],))]}


@router.put('/me/preferences')
def save_preferences(data: Preferences, user=Depends(current_user)):
    with db_session() as db:
        email = db.execute('SELECT email FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        if data.email_notifications and not email: raise HTTPException(422, 'Ask your administrator to assign an email address')
        db.execute('UPDATE users SET email_notifications=? WHERE id=?', (int(data.email_notifications), user['id']))
        if not data.email_notifications:
            db.execute("UPDATE notification_jobs SET state='cancelled',updated_at=? WHERE user_id=? AND state='queued'", (utcnow(), user['id']))
    return {'email': email, 'email_notifications': data.email_notifications}


@router.post('/inventory/{item_id}/watch')
def watch_item(item_id: str, user=Depends(current_user)):
    item = next((item for item in get_provider().get_inventory() if item['id'] == item_id), None)
    if item is None: raise HTTPException(404, 'Item not found')
    if item['stock'] > 0: raise HTTPException(409, 'This item is already available')
    with db_session() as db:
        email = db.execute('SELECT email FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        if not email: raise HTTPException(422, 'Ask your administrator to assign an email address')
        db.execute('UPDATE users SET email_notifications=1 WHERE id=?', (user['id'],))
        db.execute('INSERT INTO stock_watches VALUES (?,?,1,?) ON CONFLICT(user_id,item_id) DO UPDATE SET active=1,created_at=excluded.created_at', (user['id'], item_id, utcnow()))
    return {'watching': True}


@router.delete('/inventory/{item_id}/watch')
def unwatch_item(item_id: str, user=Depends(current_user)):
    with db_session() as db:
        watch = db.execute('SELECT created_at FROM stock_watches WHERE user_id=? AND item_id=?', (user['id'], item_id)).fetchone()
        db.execute('UPDATE stock_watches SET active=0 WHERE user_id=? AND item_id=?', (user['id'], item_id))
        if watch:
            db.execute("UPDATE notification_jobs SET state='cancelled',updated_at=? WHERE event_key=? AND state='queued'", (utcnow(), f"stock:{item_id}:{user['id']}:{watch[0]}"))
    return {'watching': False}


@router.get('/admin/delivery-settings')
def get_delivery_settings(_=Depends(superadmin_user)): return delivery_settings()


@router.put('/admin/delivery-settings')
def save_delivery_settings(data: DeliverySettings, _=Depends(superadmin_user)):
    try:
        emails = list(dict.fromkeys(valid_email(value).lower() for value in data.admin_emails))
        url = valid_flow_url(data.email_flow_url.strip()) if data.email_flow_url and data.email_flow_url.strip() else None
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    with db_session() as db:
        if data.email_enabled and not (url or config_value(db, 'email_flow_url', '')):
            raise HTTPException(422, 'Add the notification flow URL before enabling emails')
        if data.email_enabled and not emails: raise HTTPException(422, 'Add at least one admin request recipient')
        set_config(db, 'admin_request_emails', emails)
        set_config(db, 'email_enabled', data.email_enabled)
        set_config(db, 'transaction_export_enabled', data.transaction_export_enabled)
        if url: set_config(db, 'email_flow_url', url)
    return delivery_settings()


@router.post('/admin/notifications/{job_id}/retry')
def retry_notification(job_id: int, _=Depends(superadmin_user)):
    with db_session() as db:
        changed = db.execute("UPDATE notification_jobs SET state='queued',error=NULL,updated_at=? WHERE id=? AND state='uncertain'", (utcnow(), job_id))
        if not changed.rowcount: raise HTTPException(409, 'Only an uncertain notification can be retried')
    return {'queued': True}


class ConnectionSettings(BaseModel):
    read_url: str | None = Field(default=None, max_length=4000)
    update_url: str | None = Field(default=None, max_length=4000)
    api_key: str | None = Field(default=None, max_length=1000)
    sync_enabled: bool = True
    interval_seconds: int = Field(default=60, ge=10, le=3600)
    session_days: int | None = Field(default=None, ge=1, le=365)


@router.get('/admin/connections')
def connection_settings(_=Depends(superadmin_user)):
    with db_session() as db:
        return {'read_configured': bool(config_value(db,'inventory_read_url',settings.read_url)),
                'update_configured': bool(config_value(db,'inventory_update_url',settings.update_url)),
                'api_key_configured': bool(config_value(db,'inventory_api_key',settings.api_key)),
                'sync_enabled': config_value(db,'inventory_sync_enabled',True),
                'interval_seconds': config_value(db,'sync_interval_seconds',settings.sync_interval_seconds),
                'session_days': config_value(db,'session_days',settings.session_days)}


@router.put('/admin/connections')
def save_connections(data: ConnectionSettings, _=Depends(superadmin_user)):
    urls = {}
    for field in ('read_url','update_url'):
        value = getattr(data, field)
        if value and value.strip():
            try: urls['inventory_'+field] = valid_flow_url(value.strip())
            except ValueError as exc: raise HTTPException(422,str(exc)) from None
    provider = get_provider()
    lock = getattr(provider, '_cycle_lock', None)
    if lock and not lock.acquire(blocking=False):
        raise HTTPException(409, 'A connection is in use. Wait for the current pass to finish and try again.')
    delivery_lock = None
    try:
        delivery_lock = settings.database_path.with_suffix('.delivery.lock').open('a')
        try: fcntl.flock(delivery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise HTTPException(409,'A delivery pass is running. Wait for it to finish and try again.') from None
        with db_session() as db:
            db.execute('BEGIN IMMEDIATE')
            if urls and db.execute("SELECT 1 FROM inventory_outbox WHERE state IN ('pending','sending','uncertain','conflict') LIMIT 1").fetchone():
                raise HTTPException(409,'Resolve pending inventory changes before changing workbook connections')
            for key,value in urls.items(): set_config(db,key,value)
            if data.api_key is not None: set_config(db,'inventory_api_key',data.api_key)
            set_config(db,'inventory_sync_enabled',data.sync_enabled)
            set_config(db,'sync_interval_seconds',data.interval_seconds)
            if data.session_days is not None: set_config(db,'session_days',data.session_days)
    finally:
        if delivery_lock: delivery_lock.close()
        if lock: lock.release()
    return connection_settings(_)
