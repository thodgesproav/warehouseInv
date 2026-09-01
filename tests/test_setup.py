import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.auth import verify_password
from app.config import settings, DEFAULT_MAPPING
from app.database import initialise, db_session
from app.main import app
from app.setup_api import prepare_security, private_secret


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'database_path', tmp_path / 'inventory.db')
    monkeypatch.setitem(settings.__dict__, 'secret_key', 'development-only-change-me')
    monkeypatch.setitem(settings.__dict__, 'environment', 'production')
    monkeypatch.setitem(settings.__dict__, 'inventory_provider', 'power_automate')
    initialise()
    prepare_security()
    started = []
    monkeypatch.setattr(app.state, 'start_workers', lambda: started.append(True), raising=False)
    client = TestClient(app)
    client.started = started
    client.setup_data = {
        'setup_code': (tmp_path / 'setup-token').read_text().strip(),
        'username': 'owner', 'display_name': 'Test Owner', 'email': 'owner@example.com',
        'password': 'Fresh-owner-password-123!', 'configure_later': True,
        'mapping': copy.deepcopy(DEFAULT_MAPPING),
    }
    return client


def complete(client, **changes):
    return client.post('/api/setup/complete', json={**client.setup_data, **changes}, headers={'X-Inventory-Request':'1'})


def test_fresh_install_has_no_default_user_and_gates_app(fresh):
    assert fresh.get('/api/setup/status').json()['required']
    assert fresh.get('/healthz').json()['status'] == 'setup_required'
    assert fresh.get('/api/inventory').status_code == 503
    assert fresh.post('/api/auth/login', json={'username':'admin','password':'ChangeMe-Immediately-123!'}).status_code == 503
    assert fresh.started == []


def test_setup_requires_code_and_same_origin_header(fresh):
    assert complete(fresh, setup_code='x'*48).status_code == 403
    assert fresh.post('/api/setup/complete', json=fresh.setup_data).status_code == 403
    assert fresh.post('/api/setup/complete', json=fresh.setup_data, headers={'X-Inventory-Request':'1','Origin':'https://evil.example'}).status_code == 403
    assert fresh.get('/api/setup/status').json()['required']


def test_setup_creates_superadmin_saves_urls_and_cannot_be_reclaimed(fresh):
    url = 'https://test.environment.api.powerplatform.com/invoke?test_signature=not-real'
    response = complete(fresh, read_url=url, configure_later=False, session_days=60)
    assert response.status_code == 201
    assert 'private-test-signature' not in response.text
    with db_session() as db:
        user = db.execute('SELECT * FROM users').fetchone()
        assert user['access_level'] == 'superadmin' and user['role'] == 'admin'
        assert json.loads(db.execute("SELECT value FROM settings WHERE key='admin_request_user_ids'").fetchone()[0]) == [user['id']]
        assert verify_password(fresh.setup_data['password'], user['password_hash'])
        assert db.execute("SELECT value FROM settings WHERE key='inventory_update_url'").fetchone()[0] == '"'+url+'"'
    assert fresh.started == [True]
    assert not fresh.get('/api/setup/status').json()['required']
    assert complete(fresh).status_code == 409
    assert fresh.get('/api/setup/status').json()['mapping'] is None
    login = fresh.post('/api/auth/login', json={'username':'owner','password':fresh.setup_data['password'],'remember_me':True}, headers={'X-Inventory-Request':'1'})
    assert login.status_code == 200
    assert 'Max-Age=5184000' in login.headers['set-cookie']
    assert fresh.get('/api/auth/me').json()['role'] == 'superadmin'


def test_setup_creates_token_only_panel_identity_when_configured(fresh, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'warehouse_panel_token', 'a' * 64)
    monkeypatch.setitem(settings.__dict__, 'warehouse_panel_user', 'panel')
    assert complete(fresh).status_code == 201
    with db_session() as db:
        panel = dict(db.execute("SELECT * FROM users WHERE username='panel'").fetchone())
        assert panel['display_name'] == 'Warehouse Panel'
        assert panel['role'] == 'standard' and panel['access_level'] == 'standard'
        assert panel['email'] == '' and not panel['disabled']
    response = fresh.get('/api/auth/me', headers={
        'X-Warehouse-Panel-Token': 'a' * 64,
        'User-Agent': 'Mozilla/5.0 (Linux; Android 5.1.1; Crestron Touchpanel Build/LMY47V; wv)',
    })
    assert response.status_code == 200
    assert response.json()['username'] == 'panel'
    assert response.json()['warehouse_panel'] is True


def test_setup_commit_is_atomic_and_race_safe(fresh):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(fresh), range(2)))
    assert sorted(r.status_code for r in results) == [201,409]
    with db_session() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_validation_does_not_echo_secrets_and_leaves_setup_open(fresh):
    response = complete(fresh, password='tiny-key')
    assert response.status_code == 422
    assert 'tiny-key' not in response.text
    assert complete(fresh, read_url='http://localhost/private', configure_later=False).status_code == 422
    assert complete(fresh, configure_later=False).status_code == 422
    assert complete(fresh, email_enabled=True).status_code == 422
    assert fresh.get('/api/setup/status').json()['required']
    assert fresh.started == []


def test_security_files_persist_and_existing_accounts_do_not_reopen_setup(fresh):
    secret = settings.secret_key
    code = fresh.setup_data['setup_code']
    prepare_security()
    assert settings.secret_key == secret
    assert (settings.database_path.parent/'setup-token').read_text().strip() == code
    assert (settings.database_path.parent/'server-secret').stat().st_mode & 0o777 == 0o600
    complete(fresh)
    initialise()
    assert not fresh.get('/api/setup/status').json()['required']


def test_security_secret_rejects_symlink(tmp_path):
    original = tmp_path/'original'
    original.write_text('x'*48)
    link = tmp_path/'link'
    link.symlink_to(original)
    with pytest.raises(OSError): private_secret(link)
