import json
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import api as inventory_api
from app import notification_api
from app.auth import current_user
from app.config import settings, DEFAULT_MAPPING
from app.database import initialise, db_session, utcnow
from app.notifications import DeliveryWorker, queue_email, queue_stock_availability, set_config, delivery_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'database_path', tmp_path / 'inventory.db')
    initialise('unused')
    with db_session() as db: db.execute("UPDATE users SET email='owner@example.com' WHERE id=1")
    from test_local_sync import FakeExcel
    from app.inventory.local_sync import LocalSyncInventoryProvider
    local = LocalSyncInventoryProvider(FakeExcel())
    local.sync_once()
    monkeypatch.setattr(inventory_api, 'get_provider', lambda: local)
    monkeypatch.setattr(notification_api, 'get_provider', lambda: local)
    app = FastAPI()
    app.include_router(inventory_api.router)
    app.include_router(notification_api.router)
    actor = {'id': 1, 'username': 'admin', 'display_name': 'Administrator', 'role': 'superadmin'}
    app.dependency_overrides[current_user] = lambda: actor
    result = TestClient(app)
    result.actor = actor
    result.provider = local
    return result


def jobs():
    with db_session() as db: return [dict(row) for row in db.execute('SELECT * FROM notification_jobs')]


def request(client, **extra):
    return client.post('/api/requests', json={'item_requested': '<Adapter>', 'quantity': 200, **extra})


class FakeMail:
    def __init__(self): self.calls = []; self.fail = False
    def _post(self, url, payload):
        self.calls.append(payload)
        if self.fail: raise TimeoutError('no acknowledgement')
        if payload.get('action') == 'logTransactions':
            return {'ok': True, 'loggedIds': [r['transactionId'] for r in payload['fields']['transactions']]}
        return {'ok': True}


def enable_email():
    with db_session() as db:
        manager = db.execute("INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES('manager','Warehouse Manager','unused','admin','warehouse_admin','manager@example.com',?)", (utcnow(),))
        set_config(db, 'email_enabled', True)
        set_config(db, 'email_flow_url', 'https://test.environment.api.powerplatform.com/flow')
        set_config(db, 'admin_request_user_ids', [1, manager.lastrowid])


def test_request_queue_escapes_html_and_waits_for_setup(client):
    assert request(client).status_code == 200
    assert len(jobs()) == 1 and '&lt;Adapter&gt;' in jobs()[0]['html_body']
    mail = FakeMail(); DeliveryWorker(mail).send_emails()
    assert mail.calls == [] and jobs()[0]['state'] == 'queued'
    enable_email(); DeliveryWorker(mail).send_emails()
    assert mail.calls[0]['to'] == 'owner@example.com;manager@example.com'
    assert jobs()[0]['state'] == 'sent'
    DeliveryWorker(mail).send_emails()
    assert len(mail.calls) == 1


def test_uncertain_delivery_requires_explicit_retry(client):
    request(client); enable_email()
    mail = FakeMail(); mail.fail = True
    worker = DeliveryWorker(mail); worker.send_emails(); worker.send_emails()
    assert len(mail.calls) == 1 and jobs()[0]['state'] == 'uncertain'
    assert client.post('/api/admin/notifications/1/retry').status_code == 200
    mail.fail = False; worker.send_emails()
    assert len(mail.calls) == 2 and jobs()[0]['state'] == 'sent'
    assert client.post('/api/admin/notifications/1/retry').status_code == 409


def test_request_available_optin_and_optout(client):
    response = request(client, notify_available=True)
    assert response.status_code == 200
    identifier = response.json()['id']
    for _ in range(2): assert client.put(f'/api/admin/requests/{identifier}', json={'status': 'available'}).status_code == 200
    assert len(jobs()) == 2
    assert client.put('/api/me/preferences', json={'email_notifications': False}).status_code == 200
    assert jobs()[1]['state'] == 'cancelled'
    assert client.put('/api/admin/requests/999', json={'status': 'available'}).status_code == 404


def test_validation_and_admin_permissions(client):
    assert request(client, notify_available=True, notification_email='bad').status_code == 422
    assert jobs() == []
    with db_session() as db: db.execute("UPDATE users SET email='' WHERE id=1")
    assert client.put('/api/me/preferences', json={'email_notifications': True}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'email_enabled': True, 'recipient_user_ids': [1]}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'email_flow_url': 'http://localhost/'}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'email_flow_url': 'https://evil.example.com/'}).status_code == 422
    client.actor['role'] = 'standard'
    for route in ['/api/admin/database/export', '/api/admin/delivery-settings']:
        assert client.get(route).status_code == 403
    assert client.put('/api/admin/delivery-settings', json={}).status_code == 403


def test_settings_preserve_secret_without_returning_it(client):
    secret = 'https://test.environment.api.powerplatform.com/flow?test_signature=not-real'
    response = client.put('/api/admin/delivery-settings', json={'email_flow_url': secret, 'recipient_user_ids': [1]})
    assert response.status_code == 200 and 'private-test' not in response.text
    assert response.json()['admin_emails'] == ['owner@example.com']
    assert response.json()['selected_user_ids'] == [1]
    assert client.put('/api/admin/delivery-settings', json={'email_enabled': True, 'recipient_user_ids': [1]}).status_code == 200
    assert client.get('/api/admin/delivery-settings').json()['email_flow_configured']


def test_request_recipients_are_selected_admin_accounts(client):
    with db_session() as db:
        manager = db.execute("INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES('manager','Warehouse Manager','unused','admin','warehouse_admin','manager@example.com',?)", (utcnow(),)).lastrowid
        standard = db.execute("INSERT INTO users(username,display_name,password_hash,role,access_level,email,created_at) VALUES('worker','Worker','unused','standard','standard','worker@example.com',?)", (utcnow(),)).lastrowid
        disabled = db.execute("INSERT INTO users(username,display_name,password_hash,role,access_level,email,disabled,created_at) VALUES('disabled','Disabled Admin','unused','admin','superadmin','disabled@example.com',1,?)", (utcnow(),)).lastrowid
    settings_response = client.get('/api/admin/delivery-settings').json()
    assert [user['id'] for user in settings_response['recipient_users']] == [1, manager]
    assert client.put('/api/admin/delivery-settings', json={'recipient_user_ids': [standard]}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'recipient_user_ids': [disabled]}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'recipient_user_ids': [999]}).status_code == 422
    assert client.put('/api/admin/delivery-settings', json={'recipient_user_ids': [manager]}).status_code == 200
    with db_session() as db:
        db.execute("UPDATE users SET email='new-manager@example.com' WHERE id=?", (manager,))
        set_config(db, 'email_enabled', True)
        set_config(db, 'email_flow_url', 'https://test.environment.api.powerplatform.com/flow')
    request(client)
    mail = FakeMail(); DeliveryWorker(mail).send_emails()
    assert mail.calls[0]['to'] == 'new-manager@example.com'
    request(client)
    with db_session() as db:
        db.execute("UPDATE users SET role='standard',access_level='standard' WHERE id=?", (manager,))
    DeliveryWorker(mail).send_emails()
    assert len(mail.calls) == 1 and jobs()[-1]['state'] == 'queued'


def test_stock_watches_are_one_shot_and_cancellable(client, monkeypatch):
    class Provider:
        def get_inventory(self): return [{'id': 'A', 'stock': 0}]
    monkeypatch.setattr(notification_api, 'get_provider', lambda: Provider())
    assert client.post('/api/inventory/A/watch').status_code == 200
    assert client.get('/api/me/preferences').json()['watching'] == ['A']
    with db_session() as db:
        old = {'A': {'SOH': 0, 'Description': 'Adapter'}}
        new = {'A': {'SOH': 12, 'Description': 'Adapter'}}
        queue_stock_availability(db, old, new, DEFAULT_MAPPING)
        queue_stock_availability(db, old, new, DEFAULT_MAPPING)
    assert len(jobs()) == 1
    assert client.get('/api/me/preferences').json()['watching'] == []
    assert client.delete('/api/inventory/A/watch').status_code == 200
    assert jobs()[0]['state'] == 'cancelled'


def test_database_export_is_valid_complete_snapshot(client):
    request(client)
    with db_session() as db:
        db.execute('INSERT OR REPLACE INTO local_inventory VALUES (?,?)', ('A', json.dumps({'SOH': 500})))
    response = client.get('/api/admin/database/export')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert '.sqlite' in response.headers['content-disposition']
    with sqlite3.connect(':memory:') as backup:
        backup.deserialize(response.content)
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert backup.execute('SELECT COUNT(*) FROM notification_jobs').fetchone()[0] == 1
        assert backup.execute('SELECT COUNT(*) FROM local_inventory').fetchone()[0] == 1
        assert backup.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_transaction_export_acknowledged_and_retry_stable(client, monkeypatch):
    monkeypatch.setitem(settings.__dict__, 'inventory_provider', 'power_automate')
    with db_session() as db: set_config(db, 'transaction_export_enabled', True)
    inventory_api.record_transaction(client.actor, 'A', 'Adapter', -200, 500, 300, True, 'synced')
    inventory_api.record_transaction(client.actor, 'A', 'Adapter', -1, 300, 299, True, 'pending', sync_operation_id='pending-op')
    mail = FakeMail(); mail.fail = True
    worker = DeliveryWorker(mail); worker.export_transactions()
    first_id = mail.calls[0]['fields']['transactions'][0]['transactionId']
    assert len(mail.calls[0]['fields']['transactions']) == 1
    assert delivery_settings()['unexported_transactions'] == 2
    mail.fail = False; worker.export_transactions()
    assert mail.calls[1]['fields']['transactions'][0]['transactionId'] == first_id
    assert delivery_settings()['unexported_transactions'] == 1
    worker.export_transactions()
    assert len(mail.calls) == 2


def test_notification_import_package(tmp_path):
    import runpy
    module = runpy.run_path('scripts/build_notification_package.py')
    files = module['build'](tmp_path / 'notifications.zip')
    manifest = files['manifest.json']
    flow = module['FLOW']
    definition = files[f'Microsoft.Flow/flows/{flow}/definition.json']['properties']['definition']
    assert 'runtimeConfiguration' not in definition['triggers']['manual']
    send = definition['actions']['Try']['actions']['Send_email']
    assert send['inputs']['host']['operationId'] == 'SendEmailV2'
    assert send['inputs']['retryPolicy'] == {'type': 'none'}
    assert set(manifest['resources'][flow]['dependsOn']).issubset(manifest['resources'])
