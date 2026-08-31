import time

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, create_token, digest, hash_password
from app.config import settings
from app.database import db_session, initialise, utcnow
from app.main import app


PASSWORD = 'Session-test-only-123!'
HEADERS = {'X-Inventory-Request': '1'}


@pytest.fixture
def client(tmp_path):
    previous = settings.database_path
    object.__setattr__(settings, 'database_path', tmp_path / 'session-test.db')
    initialise(hash_password(PASSWORD))
    with db_session() as db:
        db.execute('INSERT INTO users(username,display_name,password_hash,role,created_at,email) VALUES(?,?,?,?,?,?)',
                   ('cookie-worker', 'Worker', hash_password(PASSWORD), 'standard', utcnow(), 'worker@example.com'))
    # No lifespan: these tests must never start real sync or email workers.
    instance = TestClient(app)
    try:
        yield instance
    finally:
        instance.close()
        object.__setattr__(settings, 'database_path', previous)


def sign_in(client, remember=True, username='cookie-worker'):
    return client.post('/api/auth/login', headers=HEADERS,
                       json={'username': username, 'password': PASSWORD, 'remember_me': remember})


def test_persistent_cookie_is_protected_and_survives_new_client_and_database_init(client):
    response = sign_in(client)
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/api' in cookie
    assert f'Max-Age={settings.session_days * 86400}' in cookie
    assert 'access_token' not in response.json()
    token = client.cookies.get(SESSION_COOKIE)
    with db_session() as db:
        session = dict(db.execute('SELECT * FROM login_sessions').fetchone())
    assert session['token_hash'] == digest(token)
    assert token not in str(session)
    initialise('unused')
    reopened = TestClient(app, cookies={SESSION_COOKIE: token})
    assert reopened.get('/api/auth/me').json()['username'] == 'cookie-worker'
    reopened.close()


def test_unchecked_option_uses_nonpersistent_cookie_with_bounded_server_expiry(client):
    response = sign_in(client, remember=False)
    assert response.status_code == 200
    assert 'Max-Age=' not in response.headers['set-cookie']
    assert 'expires=' not in response.headers['set-cookie'].lower()
    with db_session() as db:
        expiry = db.execute('SELECT expires_at FROM login_sessions').fetchone()[0]
    assert abs(expiry - time.time() - settings.access_token_minutes * 60) < 5


def test_cookie_is_secure_over_https(client):
    secure_client = TestClient(app, base_url='https://testserver')
    response = sign_in(secure_client)
    assert 'Secure' in response.headers['set-cookie']
    assert secure_client.get('/api/auth/me').status_code == 200
    secure_client.close()


def test_logout_clears_cookie_and_revokes_replayed_session(client):
    sign_in(client)
    token = client.cookies.get(SESSION_COOKIE)
    response = client.post('/api/auth/logout', headers=HEADERS)
    assert response.status_code == 204
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/auth/me', headers={'Cookie': f'{SESSION_COOKIE}={token}'}).status_code == 401
    assert client.post('/api/auth/logout', headers=HEADERS).status_code == 204


def test_expired_and_invalid_sessions_are_rejected(client):
    assert client.get('/api/auth/me', headers={'Cookie': f'{SESSION_COOKIE}=invalid'}).status_code == 401
    sign_in(client)
    with db_session() as db:
        db.execute('UPDATE login_sessions SET expires_at=0')
    assert client.get('/api/auth/me').status_code == 401


def test_wrong_password_does_not_create_session(client):
    response = client.post('/api/auth/login', headers=HEADERS,
                           json={'username': 'cookie-worker', 'password': 'wrong', 'remember_me': True})
    assert response.status_code == 401
    assert SESSION_COOKIE not in client.cookies


def test_csrf_protection_on_login_logout_and_authenticated_writes(client):
    payload = {'username': 'cookie-worker', 'password': PASSWORD, 'remember_me': True}
    assert client.post('/api/auth/login', json=payload).status_code == 403
    for origin in ('https://evil.example', 'http://sibling.testserver', 'null'):
        assert client.post('/api/auth/login', json=payload, headers={**HEADERS, 'Origin': origin}).status_code == 403
    sign_in(client)
    assert client.post('/api/auth/logout').status_code == 403
    assert client.post('/api/auth/logout', headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.post('/api/requests', json={'item_requested': 'Blocked', 'quantity': 1}).status_code == 403
    assert client.get('/api/auth/me').status_code == 200
    assert client.post('/api/auth/logout', headers={**HEADERS, 'Origin': 'http://testserver'}).status_code == 204


@pytest.mark.parametrize('change', [{'password': 'Changed-password-123!'}, {'disabled': True}])
def test_admin_reset_or_disable_revokes_existing_cookie_sessions(client, change):
    sign_in(client)
    worker = client.get('/api/auth/me').json()
    with db_session() as db:
        admin = dict(db.execute('SELECT * FROM users WHERE username=?', (settings.admin_username,)).fetchone())
    response = client.put(f"/api/admin/users/{worker['id']}", json=change,
                          headers={'Authorization': f'Bearer {create_token(admin)}'})
    assert response.status_code == 200
    assert client.get('/api/auth/me').status_code == 401
    with db_session() as db:
        assert db.execute('SELECT COUNT(*) FROM login_sessions WHERE user_id=?', (worker['id'],)).fetchone()[0] == 0


def test_role_permissions_are_current_and_api_responses_are_not_cached(client):
    sign_in(client)
    assert client.get('/api/admin/users').status_code == 403
    with db_session() as db:
        db.execute("UPDATE users SET role='admin',access_level='superadmin' WHERE username='cookie-worker'")
    assert client.get('/api/admin/users').status_code == 200
    response = client.get('/api/auth/me')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['role'] == 'superadmin'
    assert 'password_hash' not in response.json()


def test_relogin_rotates_cookie_and_old_bearer_clients_remain_compatible(client):
    sign_in(client)
    original = client.cookies.get(SESSION_COOKIE)
    sign_in(client)
    assert client.cookies.get(SESSION_COOKIE) != original
    assert client.get('/api/auth/me', headers={'Cookie': f'{SESSION_COOKIE}={original}'}).status_code == 401
    legacy = client.post('/api/auth/login', json={'username': 'cookie-worker', 'password': PASSWORD})
    assert legacy.status_code == 200
    assert client.get('/api/auth/me', headers={'Authorization': f"Bearer {legacy.json()['access_token']}"}).status_code == 200
