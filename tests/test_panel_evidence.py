from io import BytesIO

from fastapi import Request
from PIL import Image

from app import api as inventory_api
from app.auth import hash_password
from app.config import settings
from app.database import db_session, initialise, utcnow
from app import panel_evidence
from test_notifications import client


PANEL_TOKEN = "a" * 64
PANEL_UA = "Mozilla/5.0 (Linux; Android 5.1.1; Crestron Touchpanel Build/LMY47V; wv)"


def request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
    })


def jpeg(size=(1920, 1080)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(output, format="JPEG", quality=94)
    return output.getvalue()


def transaction(tmp_path, monkeypatch) -> int:
    monkeypatch.setitem(settings.__dict__, "database_path", tmp_path / "inventory.db")
    monkeypatch.setitem(settings.__dict__, "evidence_path", tmp_path / "evidence")
    initialise("unused")
    with db_session() as db:
        return db.execute(
            "INSERT INTO transactions(created_at,user_id,username,item_id,item_name,quantity,old_soh,new_soh,transaction_type,success,sync_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), 1, "worker", "A", "Adapter", -1, 10, 9, "take", 1, "synced"),
        ).lastrowid


def test_panel_identity_requires_token_and_crestron_user_agent(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    assert panel_evidence.is_warehouse_panel_request(request({"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}))
    assert not panel_evidence.is_warehouse_panel_request(request({"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": "Desktop"}))
    assert not panel_evidence.is_warehouse_panel_request(request({"X-Warehouse-Panel-Token": "b" * 64, "User-Agent": PANEL_UA}))


def test_panel_token_permanently_authenticates_configured_panel_user(client, monkeypatch):
    client.app.dependency_overrides.pop(inventory_api.current_user, None)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_user", "panel-user")
    with db_session() as db:
        db.execute(
            "INSERT INTO users(username,display_name,password_hash,role,access_level,email,disabled,created_at) VALUES(?,?,?,?,?,?,0,?)",
            ("panel-user", "Warehouse Panel", hash_password("unused-panel-password"), "standard", "standard", "", utcnow()),
        )

    panel_headers = {"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}
    response = client.get('/api/auth/me', headers=panel_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "panel-user"
    assert response.json()["warehouse_panel"] is True
    assert client.get('/api/auth/me', headers={**panel_headers, "User-Agent": "Desktop"}).status_code == 401

    with db_session() as db:
        db.execute("UPDATE users SET disabled=1 WHERE username='panel-user'")
    assert client.get('/api/auth/me', headers=panel_headers).status_code == 401


def test_panel_token_mutations_still_require_app_request_header(client, monkeypatch):
    client.app.dependency_overrides.pop(inventory_api.current_user, None)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_user", "panel-user")
    with db_session() as db:
        db.execute(
            "INSERT INTO users(username,display_name,password_hash,role,access_level,email,disabled,created_at) VALUES(?,?,?,?,?,?,0,?)",
            ("panel-user", "Warehouse Panel", hash_password("unused-panel-password"), "standard", "standard", "", utcnow()),
        )
    headers = {"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}
    assert client.post('/api/requests', headers=headers, json={
        'item_requested': 'Cable', 'manufacturer': '', 'manufacturer_model': '',
        'quantity': 1, 'notes': '', 'notify_available': False,
    }).status_code == 403


def test_capture_saves_original_full_resolution_jpeg(tmp_path, monkeypatch):
    transaction_id = transaction(tmp_path, monkeypatch)
    original = jpeg()
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_snapshot_url", "http://panel/camera/snapshot.jpg")
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_username", "panel")
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_password", "secret")

    class Response:
        content = original
        headers = {}
        status_code = 200
        def raise_for_status(self): return None

    calls = []
    monkeypatch.setattr(panel_evidence.httpx, "get", lambda *args, **kwargs: calls.append((args, kwargs)) or Response())

    assert panel_evidence.capture_transaction_evidence(transaction_id)
    target = panel_evidence.evidence_file(transaction_id)
    assert target and target.read_bytes() == original
    assert calls[0][1]["auth"] == ("panel", "secret")
    with db_session() as db:
        row = db.execute("SELECT evidence_width,evidence_height,evidence_error FROM transactions WHERE id=?", (transaction_id,)).fetchone()
    assert tuple(row) == (1920, 1080, None)


def test_invalid_camera_response_records_failure(tmp_path, monkeypatch):
    transaction_id = transaction(tmp_path, monkeypatch)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_snapshot_url", "http://panel/camera/snapshot.jpg")

    class Response:
        content = b"not a jpeg"
        headers = {}
        status_code = 200
        def raise_for_status(self): return None

    monkeypatch.setattr(panel_evidence.httpx, "get", lambda *args, **kwargs: Response())
    assert not panel_evidence.capture_transaction_evidence(transaction_id)
    with db_session() as db:
        row = db.execute("SELECT evidence_path,evidence_error FROM transactions WHERE id=?", (transaction_id,)).fetchone()
    assert row[0] is None and row[1] == "Camera snapshot was unavailable"


def test_capture_uses_panel_web_session_when_snapshot_redirects_to_login(tmp_path, monkeypatch):
    transaction_id = transaction(tmp_path, monkeypatch)
    original = jpeg((1280, 720))
    snapshot_url = "https://panel/camera/snapshot.jpg"
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_snapshot_url", snapshot_url)
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_username", "admin")
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_password", "secret")
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_verify_tls", False)

    class Response:
        def __init__(self, content=b"", status_code=200, headers=None):
            self.content, self.status_code, self.headers = content, status_code, headers or {}
        def raise_for_status(self):
            if self.status_code >= 400: raise panel_evidence.httpx.HTTPStatusError("failed", request=None, response=None)

    monkeypatch.setattr(panel_evidence.httpx, "get", lambda *args, **kwargs: Response(status_code=301, headers={"location": "/userlogin.html"}))
    calls = []

    class Client:
        def __init__(self, **kwargs): calls.append(("client", kwargs))
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def get(self, url):
            calls.append(("get", url))
            return Response(original if url == snapshot_url else b"")
        def post(self, url, data, headers):
            calls.append(("post", url, data, headers))
            return Response()

    monkeypatch.setattr(panel_evidence.httpx, "Client", Client)
    assert panel_evidence.capture_transaction_evidence(transaction_id)
    assert panel_evidence.evidence_file(transaction_id).read_bytes() == original
    login = next(call for call in calls if call[0] == "post")
    assert login[2] == {"login": "admin", "passwd": "secret"}
    assert login[3]["X-Requested-With"] == "XMLHttpRequest"


def test_stock_take_captures_only_for_registered_panel(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    captured = []
    monkeypatch.setattr(inventory_api, "capture_transaction_evidence", captured.append)

    panel_headers = {"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}
    response = client.post('/api/inventory/A/adjust', headers=panel_headers, json={'quantity': -1, 'expected_current_soh': 10})
    assert response.status_code == 200 and len(captured) == 1

    response = client.post('/api/inventory/A/adjust', json={'quantity': -1, 'expected_current_soh': 9})
    assert response.status_code == 200 and len(captured) == 1


def test_panel_browser_can_upload_full_resolution_evidence(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    monkeypatch.setattr(inventory_api, "capture_transaction_evidence", lambda _transaction_id: False)
    panel_headers = {"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}
    response = client.post(
        '/api/inventory/A/adjust', headers=panel_headers,
        json={'quantity': -1, 'expected_current_soh': 10},
    )
    assert response.status_code == 200
    transaction_id = response.json()['transaction_id']
    original = jpeg((1920, 1080))

    rejected = client.post(
        f'/api/panel/transactions/{transaction_id}/evidence',
        headers={"Content-Type": "image/jpeg"}, content=original,
    )
    assert rejected.status_code == 403

    uploaded = client.post(
        f'/api/panel/transactions/{transaction_id}/evidence',
        headers={**panel_headers, "Content-Type": "image/jpeg"}, content=original,
    )
    assert uploaded.status_code == 204
    target = panel_evidence.evidence_file(transaction_id)
    assert target and target.read_bytes() == original


def test_panel_request_can_select_user_for_availability_notification(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "warehouse_panel_token", PANEL_TOKEN)
    with db_session() as db:
        selected_id = db.execute(
            "INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES('requester','Requesting User','unused','standard','standard','requester@example.com',?)",
            (utcnow(),),
        ).lastrowid
        db.execute(
            "INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES('no-email','No Email','unused','standard','standard','',?)",
            (utcnow(),),
        )
    panel_headers = {"X-Warehouse-Panel-Token": PANEL_TOKEN, "User-Agent": PANEL_UA}

    assert client.get('/api/panel/notification-users').status_code == 403
    users = client.get('/api/panel/notification-users', headers=panel_headers).json()
    assert selected_id in [user['id'] for user in users]
    assert all(set(user) == {'id', 'username', 'display_name'} for user in users)

    payload = {'item_requested': 'Panel request', 'notify_available': True, 'notify_user_id': selected_id}
    assert client.post('/api/requests', json=payload).status_code == 403
    assert client.post('/api/requests', headers=panel_headers, json={**payload, 'notify_user_id': None}).status_code == 422
    created = client.post('/api/requests', headers=panel_headers, json=payload)
    assert created.status_code == 200
    request_id = created.json()['id']
    with db_session() as db:
        request_row = db.execute('SELECT requested_by,notify_user_id FROM item_requests WHERE id=?', (request_id,)).fetchone()
        notifications_enabled = db.execute('SELECT email_notifications FROM users WHERE id=?', (selected_id,)).fetchone()[0]
    assert tuple(request_row) == (client.actor['id'], selected_id)
    assert notifications_enabled == 1

    assert client.put(f'/api/admin/requests/{request_id}', json={'status': 'available'}).status_code == 200
    with db_session() as db:
        availability = db.execute("SELECT user_id FROM notification_jobs WHERE event_key=?", (f'request:{request_id}:available',)).fetchone()
    assert availability[0] == selected_id
