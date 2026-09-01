from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, Request, Response
from fastapi.responses import FileResponse

from .auth import admin_user, superadmin_user, effective_user, create_token, current_user, hash_password, verify_password
from .auth import start_session, end_session, check_browser_request
from .config import settings
from .database import db_session, get_mapping, rows, set_mapping, utcnow
from .inventory import get_provider
from .inventory.base import InsufficientStock, InventoryError, StockConflict, SyncUnavailable
from .inventory.local_sync import LocalSyncInventoryProvider
from .images.thumbnails import InvalidImage, write_thumbnail
from .notifications import queue_email, valid_email, queue_stock_availability
from .panel_evidence import capture_transaction_evidence, evidence_file, is_warehouse_panel_request, store_transaction_evidence
from .schemas import ItemFields, ItemRequestIn, LoginIn, MappingIn, RequestStatus, StockAdjustment, UserIn, UserUpdate
from .request_workflow import fulfil_request

log = logging.getLogger("inventory.api")
router = APIRouter(prefix="/api")


def public_user(user: dict, request: Request | None = None) -> dict:
    user = effective_user(user)
    result = {key: user[key] for key in ("id", "username", "display_name", "role", "disabled", "email") if key in user}
    result["warehouse_panel"] = bool(request and is_warehouse_panel_request(request))
    return result


@router.post("/auth/login")
def login(data: LoginIn, request: Request, response: Response):
    if data.remember_me is not None or request.headers.get('origin'):
        check_browser_request(request)
    with db_session() as db:
        row = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (data.username,)).fetchone()
    if not row or row["disabled"] or not verify_password(data.password, row["password_hash"]):
        log.warning("Failed login for %s", data.username)
        raise HTTPException(401, "Username or password is incorrect")
    user = dict(row); log.info("Login: %s", user["username"])
    response.headers['Cache-Control'] = 'no-store'
    if data.remember_me is not None:
        start_session(request, response, user, data.remember_me)
        return {'user': public_user(user, request)}
    return {"access_token": create_token(user), "token_type": "bearer", "user": public_user(user, request)}


@router.post('/auth/logout', status_code=204)
def logout(request: Request, response: Response):
    check_browser_request(request)
    end_session(request, response)
    response.headers['Cache-Control'] = 'no-store'


@router.get("/auth/me")
def me(request: Request, user: dict = Depends(current_user)): return public_user(user, request)


@router.get("/inventory")
def inventory(q: str = Query(default="", max_length=200), user: dict = Depends(current_user)):
    try: products = get_provider().get_inventory()
    except SyncUnavailable as exc: raise HTTPException(503, str(exc)) from exc
    needle = q.strip().casefold()
    if needle:
        products = [p for p in products if needle in " ".join(str(v) for v in [p.get("name"), p.get("manufacturer"), p.get("model"), p.get("sku"), p.get("category"), p.get("location"), *p.get("raw_fields", {}).values()]).casefold()]
    with db_session() as db:
        watched = [row[0] for row in db.execute('SELECT item_id FROM stock_watches WHERE user_id=? AND active=1', (user['id'],))]
    return {"items": products, "sync": get_provider().get_sync_status(), 'columns': get_provider().get_columns(), 'watched_ids': watched, 'discontinued_column': get_mapping().get('discontinued',''), 'mapping': get_mapping()}


def record_transaction(user: dict, item_id: str, item_name: str, quantity: int, old: int | None, new: int | None, success: bool, sync: str, error: str | None = None, kind: str = "take", sync_operation_id: str | None = None):
    with db_session() as db:
        if sync_operation_id:
            operation = db.execute('SELECT state FROM inventory_outbox WHERE operation_id=?', (sync_operation_id,)).fetchone()
            if operation: sync = 'synced' if operation[0] == 'done' else operation[0]
        cursor = db.execute("INSERT INTO transactions(created_at,user_id,username,item_id,item_name,quantity,old_soh,new_soh,transaction_type,success,sync_status,error,sync_operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (utcnow(), user["id"], user["username"], item_id, item_name, quantity, old, new, kind, int(success), sync, error, sync_operation_id))
        return int(cursor.lastrowid)


@router.post("/inventory/{item_id}/adjust")
def adjust_stock(item_id: str, data: StockAdjustment, request: Request, user: dict = Depends(current_user)):
    name = item_id
    try:
        item = next((p for p in get_provider().get_inventory() if p["id"] == item_id), None); name = item["name"] if item else item_id
        updated = get_provider().adjust_stock(item_id, data.quantity, data.expected_current_soh)
        transaction_id = record_transaction(user, item_id, name, data.quantity, updated["old_stock"], updated["stock"], True, updated.get('sync_status', 'synced'), sync_operation_id=updated.get('sync_operation_id'))
        if data.quantity < 0 and is_warehouse_panel_request(request):
            capture_transaction_evidence(transaction_id)
        return {**updated, "transaction_id": transaction_id}
    except InsufficientStock as exc:
        record_transaction(user, item_id, name, data.quantity, data.expected_current_soh, None, False, "rejected", str(exc)); raise HTTPException(409, str(exc)) from exc
    except StockConflict as exc:
        record_transaction(user, item_id, name, data.quantity, data.expected_current_soh, None, False, "conflict", str(exc)); raise HTTPException(409, f"Stock changed while you were taking this item. Refresh and try again. {exc}") from exc
    except InventoryError as exc:
        record_transaction(user, item_id, name, data.quantity, data.expected_current_soh, None, False, "failed", str(exc)); raise HTTPException(503, "Couldn't update stock. No changes were made.") from exc


@router.get("/activity")
def activity(user: dict = Depends(current_user)):
    return rows("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],))


@router.post("/panel/transactions/{transaction_id}/evidence", status_code=204, include_in_schema=False)
async def upload_panel_evidence(transaction_id: int, request: Request, user: dict = Depends(current_user)):
    if not is_warehouse_panel_request(request):
        raise HTTPException(403, "Warehouse panel authentication is required")
    with db_session() as db:
        transaction = db.execute(
            "SELECT user_id,success,quantity FROM transactions WHERE id=?", (transaction_id,)
        ).fetchone()
    if not transaction or transaction["user_id"] != user["id"] or not transaction["success"] or transaction["quantity"] >= 0:
        raise HTTPException(404, "Transaction is not available for panel evidence")
    content = await request.body()
    if not store_transaction_evidence(transaction_id, content):
        raise HTTPException(422, "Camera image was invalid")


@router.post("/requests")
def create_request(data: ItemRequestIn, request: Request, user: dict = Depends(current_user)):
    now = utcnow()
    panel_request = is_warehouse_panel_request(request)
    if data.notify_user_id is not None and not panel_request:
        raise HTTPException(403, "Only the warehouse panel can select another notification user")
    if panel_request and data.notify_available and data.notify_user_id is None:
        raise HTTPException(422, "Select your user account for the availability notification")
    with db_session() as db:
        db.execute('BEGIN IMMEDIATE')
        notify_user = None
        if data.notify_available:
            notification_user_id = data.notify_user_id if panel_request else user['id']
            profile = db.execute('SELECT id,display_name,email FROM users WHERE id=? AND disabled=0', (notification_user_id,)).fetchone()
            try: email = valid_email(profile['email'] if profile else '')
            except ValueError as exc: raise HTTPException(422, 'Ask your administrator to assign an email address') from None
            notify_user = profile
            db.execute('UPDATE users SET email_notifications=1 WHERE id=?', (notification_user_id,))
        cursor = db.execute("INSERT INTO item_requests(item_requested,manufacturer_model,quantity,notes,requested_by,requested_by_name,status,created_at,updated_at,notify_available) VALUES(?,?,?,?,?,?, 'new',?,?,?)",
                            (data.item_requested, data.manufacturer_model, data.quantity, data.notes, user["id"], user["display_name"], now, now, int(data.notify_available)))
        request_id = cursor.lastrowid
        db.execute('UPDATE item_requests SET manufacturer=?,notify_user_id=? WHERE id=?', (data.manufacturer, notify_user['id'] if notify_user else None, request_id))
        queue_email(db, f'request:{request_id}:created', 'admins', 'New inventory item request',
                    {'Request number': request_id, 'Requested by': user['display_name'], 'Item': data.item_requested,
                     'Notify when available': notify_user['display_name'] if notify_user else 'Not requested',
                     'Manufacturer': data.manufacturer, 'Master / part number': data.manufacturer_model, 'Quantity': data.quantity, 'Notes': data.notes, 'Time (UTC)': now})
    return {"id": request_id, "status": "new"}


@router.get("/panel/notification-users", include_in_schema=False)
def panel_notification_users(request: Request, _: dict = Depends(current_user)):
    if not is_warehouse_panel_request(request):
        raise HTTPException(403, "Warehouse panel authentication is required")
    return rows(
        "SELECT id,username,display_name FROM users WHERE disabled=0 AND email!='' ORDER BY display_name COLLATE NOCASE,username COLLATE NOCASE"
    )


@router.get("/requests")
def list_requests(user: dict = Depends(current_user)):
    query = "SELECT r.*,u.display_name AS notify_user_name FROM item_requests r LEFT JOIN users u ON u.id=r.notify_user_id"
    if user["role"] in ("warehouse_admin", "superadmin"): return rows(query + " ORDER BY r.id DESC")
    return rows(query + " WHERE r.requested_by=? ORDER BY r.id DESC", (user["id"],))


@router.get("/admin/transactions")
def transactions(user: dict = Depends(admin_user)):
    result = rows("SELECT * FROM transactions ORDER BY id DESC LIMIT 500")
    for transaction in result:
        transaction["has_evidence"] = bool(transaction.pop("evidence_path", None)) if user["role"] == "superadmin" else False
        if user["role"] != "superadmin":
            transaction.pop("evidence_error", None)
            transaction.pop("evidence_captured_at", None)
            transaction.pop("evidence_width", None)
            transaction.pop("evidence_height", None)
    return result


@router.get("/admin/transactions/{transaction_id}/evidence", include_in_schema=False)
def transaction_evidence(transaction_id: int, _: dict = Depends(superadmin_user)):
    target = evidence_file(transaction_id)
    if not target: raise HTTPException(404, "No evidence image is available for this transaction")
    return FileResponse(target, media_type="image/jpeg", filename=f"transaction-{transaction_id}.jpg", content_disposition_type="inline")


@router.get("/admin/columns")
def columns(_: dict = Depends(superadmin_user)): return {"columns": get_provider().get_columns(), "mapping": get_mapping()}


@router.put("/admin/mapping")
def mapping(data: MappingIn, _: dict = Depends(superadmin_user)):
    provider = get_provider()
    columns = set(provider.get_columns())
    required = {"id", "name", "stock"}
    if any(not data.mapping.get(key) for key in required): raise HTTPException(400, "ID, name, and stock mappings are required")
    missing_required = [key for key in required if data.mapping[key] not in columns]
    if missing_required:
        raise HTTPException(400, f"Choose an existing Excel heading for: {', '.join(sorted(missing_required))}")
    # Excel owns the schema. Stale optional mappings are harmless and remain
    # visible as "missing" so a temporary removal can recover automatically and
    # a rename can be remapped deliberately.
    if isinstance(provider, LocalSyncInventoryProvider):
        if provider.get_sync_status()['pending_count']:
            raise HTTPException(409, 'Wait until queued changes are synced or resolved before changing mappings')
    set_mapping(data.mapping)
    if isinstance(provider, LocalSyncInventoryProvider): provider.request_sync()
    return data.mapping


@router.put("/admin/inventory/{item_id}")
def update_item(item_id: str, data: ItemFields, user: dict = Depends(admin_user)):
    protected = {get_mapping().get(key) for key in ('image', 'on_order', 'quantity_on_order')}
    if any(key in protected for key in data.fields):
        raise HTTPException(400, 'Use image upload or the Ordering page to change image and order fields')
    before = next((p for p in get_provider().get_inventory() if p["id"] == item_id), None)
    provider = get_provider()
    updated = provider.update_item(item_id, data.fields, data.base_fields) if isinstance(provider, LocalSyncInventoryProvider) else provider.update_item(item_id, data.fields)
    if before and before["stock"] != updated["stock"]: record_transaction(user, item_id, updated["name"], updated["stock"] - before["stock"], before["stock"], updated["stock"], True, updated.get('sync_status', 'synced'), kind="admin_adjustment", sync_operation_id=updated.get('sync_operation_id'))
    if before and not isinstance(provider, LocalSyncInventoryProvider):
        field_mapping = get_mapping()
        with db_session() as db: queue_stock_availability(db, {item_id: before['raw_fields']}, {item_id: updated['raw_fields']}, field_mapping)
    return updated


@router.post("/admin/inventory")
def add_item(data: ItemFields, user: dict = Depends(admin_user)):
    item = get_provider().add_item(data.fields); record_transaction(user, item["id"], item["name"], item["stock"], 0, item["stock"], True, item.get('sync_status', 'synced'), kind="add_item", sync_operation_id=item.get('sync_operation_id')); return item


@router.delete("/admin/inventory/{item_id}", status_code=204)
def delete_item(item_id: str, user: dict = Depends(admin_user)):
    item = next((p for p in get_provider().get_inventory() if p["id"] == item_id), None)
    operation = get_provider().delete_item(item_id)
    record_transaction(user, item_id, item["name"] if item else item_id, 0, item["stock"] if item else None, None, True, 'pending' if operation else 'synced', kind="delete_item", sync_operation_id=operation)


@router.put("/admin/requests/{request_id}")
def update_request(request_id: int, data: RequestStatus, user: dict = Depends(admin_user)):
    provider = get_provider()
    mapping = get_mapping()
    with db_session() as db:
        db.execute('BEGIN IMMEDIATE')
        return fulfil_request(db, provider, mapping, request_id, data.status, user)


@router.post("/admin/requests/{request_id}/add-to-inventory")
def request_to_inventory(request_id: int, user: dict = Depends(admin_user)):
    # Old clients use the same idempotent fulfilment operation.
    return update_request(request_id, RequestStatus(status='available'), user)


@router.get("/admin/users")
def users(_: dict = Depends(superadmin_user)):
    return [public_user(row) for row in rows("SELECT id,username,display_name,role,access_level,email,disabled,created_at FROM users ORDER BY username")]


@router.post("/admin/users")
def add_user(data: UserIn, _: dict = Depends(superadmin_user)):
    try: email = valid_email(data.email)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    import sqlite3
    try:
        with db_session() as db:
            cursor = db.execute("INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES(?,?,?,?,?,?,?)",
                                (data.username,data.display_name,hash_password(data.password),'standard' if data.role=='standard' else 'admin',data.role,email,utcnow()))
        return {"id": cursor.lastrowid}
    except sqlite3.IntegrityError: raise HTTPException(409,"That username already exists") from None


@router.put("/admin/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, actor: dict = Depends(superadmin_user)):
    values = data.model_dump(exclude_none=True)
    if 'email' in values:
        try: values['email'] = valid_email(values['email'])
        except ValueError as exc: raise HTTPException(422,str(exc)) from None
    if 'password' in values: values['password_hash'] = hash_password(values.pop('password'))
    if 'role' in values:
        level = values['role']
        values.update(role='standard' if level=='standard' else 'admin',access_level=level)
    if 'disabled' in values: values['disabled'] = int(values['disabled'])
    with db_session() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
        if not old: raise HTTPException(404,'User not found')
        next_row = {**dict(old),**values}
        active_super = lambda row: row['role']=='admin' and row['access_level']=='superadmin' and not row['disabled']
        if user_id==actor['id'] and values.get('disabled'): raise HTTPException(400,'You cannot disable your own account')
        if active_super(old) and not active_super(next_row):
            count = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND access_level='superadmin' AND disabled=0").fetchone()[0]
            if count <= 1: raise HTTPException(409,'Keep at least one enabled Superadmin account')
        if values:
            db.execute(f"UPDATE users SET {','.join(f'{key}=?' for key in values)} WHERE id=?",(*values.values(),user_id))
        if 'password_hash' in values or values.get('disabled'):
            db.execute('DELETE FROM login_sessions WHERE user_id=?', (user_id,))
    return {"id":user_id}


@router.post("/admin/images/{item_id}")
async def upload_image(item_id: str, file: UploadFile = File(...), _: dict = Depends(admin_user)):
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed: raise HTTPException(400, "Use a JPG, PNG, or WebP image")
    content = await file.read(5_000_001)
    if len(content) > 5_000_000: raise HTTPException(413, "Image must be 5 MB or smaller")
    target = settings.image_path / f"{item_id}.webp"
    try: write_thumbnail(content, target)
    except InvalidImage as exc: raise HTTPException(400, str(exc)) from exc
    mapping = get_mapping(); get_provider().update_item(item_id, {mapping["image"]: f"/api/images/{target.name}"})
    with db_session() as db: db.execute("INSERT INTO image_metadata(item_id,local_path,updated_at) VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE SET local_path=excluded.local_path,updated_at=excluded.updated_at", (item_id, str(target), utcnow()))
    return {"image": f"/api/images/{target.name}"}


@router.get("/images/{filename}", include_in_schema=False)
def image(filename: str):
    target = (settings.image_path / Path(filename).name).resolve()
    if target.parent != settings.image_path.resolve() or not target.exists(): raise HTTPException(404)
    return FileResponse(target)


@router.post("/admin/sync")
def force_sync(_: dict = Depends(superadmin_user)):
    items = get_provider().get_inventory(force=True); status = get_provider().get_sync_status()
    with db_session() as db: db.execute("INSERT INTO sync_events(created_at,status,message,product_count) VALUES(?,?,?,?)", (utcnow(), "queued" if status.get('mode') == 'local_first' else "ok", "Manual synchronization requested", len(items)))
    return {**status, "product_count": len(items)}


@router.get("/admin/status")
def status(_: dict = Depends(superadmin_user)):
    try: count = len(get_provider().get_inventory())
    except Exception: count = 0
    return {**get_provider().get_sync_status(), "product_count": count, "recent_events": rows("SELECT * FROM sync_events ORDER BY id DESC LIMIT 20")}


@router.get('/admin/sync/conflicts')
def sync_conflicts(_: dict = Depends(superadmin_user)):
    provider = get_provider()
    return provider.get_conflicts() if hasattr(provider, 'get_conflicts') else []


@router.post('/admin/sync/conflicts/{item_id}/use-excel')
def use_excel_version(item_id: str, _: dict = Depends(superadmin_user)):
    provider = get_provider()
    if not hasattr(provider, 'use_excel'): raise HTTPException(400, 'Background sync is not enabled')
    provider.use_excel(item_id)
    return {'ok': True}
