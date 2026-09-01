from concurrent.futures import ThreadPoolExecutor
from test_notifications import client, request, jobs
from app.auth import effective_user, create_token
from app.database import db_session, get_mapping, initialise, runtime_setting
from app.config import settings, DEFAULT_MAPPING
from app.inventory.power_automate import PowerAutomateInventoryProvider


def test_warehouse_can_edit_delete_and_order_but_not_maintain(client):
    client.actor['role'] = 'warehouse_admin'
    for path in ['/api/admin/database/export','/api/admin/columns','/api/admin/users','/api/admin/delivery-settings','/api/admin/connections','/api/admin/status','/api/admin/sync/conflicts']:
        assert client.get(path).status_code == 403, path
    assert client.put('/api/admin/mapping',json={'mapping':DEFAULT_MAPPING}).status_code == 403
    assert client.put('/api/admin/connections',json={}).status_code == 403
    assert client.put('/api/admin/users/1',json={'email':'new@example.com'}).status_code == 403
    assert client.post('/api/admin/sync').status_code == 403
    assert client.put('/api/admin/inventory/A',json={'fields':{'Notes':'Warehouse edit'}}).status_code == 200
    identifier=request(client).json()['id']
    assert client.put(f'/api/admin/requests/{identifier}',json={'status':'ordered'}).status_code == 200
    assert client.delete('/api/admin/inventory/A').status_code == 204
    assert not client.provider.get_inventory()
    with db_session() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='delete_item'").fetchone()[0] == 1


def test_standard_cannot_change_inventory_or_admin_access(client):
    client.actor['role']='standard'
    assert client.delete('/api/admin/inventory/A').status_code == 403
    assert client.put('/api/admin/inventory/A',json={'fields':{'SOH':999}}).status_code == 403
    assert client.put('/api/admin/users/1',json={'role':'superadmin'}).status_code == 403
    assert client.put('/api/admin/requests/1',json={'status':'available'}).status_code == 403


def test_user_emails_assigned_only_by_superadmin(client):
    response=client.post('/api/admin/users',json={'username':'newuser','display_name':'New User','password':'testing-password','role':'warehouse_admin','email':'warehouse@example.com'})
    assert response.status_code == 200
    record=next(u for u in client.get('/api/admin/users').json() if u['id']==response.json()['id'])
    assert record['role']=='warehouse_admin' and record['email']=='warehouse@example.com'
    assert client.put('/api/me/preferences',json={'email':'override@example.com'}).status_code == 422
    assert request(client,notify_available=True,notification_email='override@example.com').status_code == 422
    assert client.get('/api/me/preferences').json()['email']=='owner@example.com'
    assert client.post('/api/admin/users',json={'username':'noemail','display_name':'No Email','password':'testing-password'}).status_code == 422


def test_superadmin_cannot_remove_last_superadmin(client):
    assert client.put('/api/admin/users/1',json={'role':'warehouse_admin'}).status_code == 409
    assert client.put('/api/admin/users/1',json={'disabled':True}).status_code == 400
    assert client.put('/api/admin/users/999',json={'email':'valid@example.com'}).status_code == 404


def test_fulfilment_is_atomic_idempotent_and_uses_requested_quantity(client):
    identifier=request(client,notify_available=True).json()['id']
    def fulfil(_): return client.put(f'/api/admin/requests/{identifier}',json={'status':'available'})
    with ThreadPoolExecutor(max_workers=2) as pool: responses=list(pool.map(fulfil,range(2)))
    assert all(r.status_code==200 for r in responses)
    assert responses[0].json()['inventory_item_id']==responses[1].json()['inventory_item_id']
    items=client.provider.get_inventory()
    new=[i for i in items if i['id']!='A']
    assert len(new)==1 and new[0]['stock']==200
    assert len(jobs())==2
    with db_session() as db:
        assert db.execute("SELECT COUNT(*) FROM inventory_outbox WHERE kind='add'").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='request_received'").fetchone()[0]==1
    client.provider.sync_once()
    assert client.provider.remote.rows[new[0]['id']]['SOH']==200


def test_fulfil_failure_does_not_mark_available(client,monkeypatch):
    identifier=request(client).json()['id']
    def fail(*args): raise RuntimeError('simulated failure after starting fulfilment')
    monkeypatch.setattr(client.provider,'_put',fail)
    import pytest
    with pytest.raises(RuntimeError): client.put(f'/api/admin/requests/{identifier}',json={'status':'available'})
    with db_session() as db:
        assert db.execute('SELECT status FROM item_requests WHERE id=?',(identifier,)).fetchone()[0]=='new'
        assert db.execute('SELECT COUNT(*) FROM inventory_outbox').fetchone()[0]==0


def test_discontinued_uses_selected_column_not_position(client):
    mapping={**DEFAULT_MAPPING,'discontinued':'Retired'}
    for flag in ['Yes',True,1,'discontinued']:
        item=PowerAutomateInventoryProvider._normalise({'Inventory ID':'B','Description':'Old model','SOH':3,'Retired':flag,'Discontinued':False},mapping)
        assert item['discontinued'] and item['stock']==3
    assert not PowerAutomateInventoryProvider._normalise({'Inventory ID':'B','Retired':'No','Discontinued':True},mapping)['discontinued']


def test_mapping_accepts_removed_optional_heading_and_allows_core_renames(client):
    mapping = {**DEFAULT_MAPPING, 'image': 'Deleted Photo Heading'}
    response = client.put('/api/admin/mapping', json={'mapping': mapping})
    assert response.status_code == 200
    assert response.json()['image'] == 'Deleted Photo Heading'
    assert get_mapping()['image'] == 'Deleted Photo Heading'

    row = client.provider.remote.rows['A']
    row['Asset Key'] = row.pop('Inventory ID')
    row['Item Title'] = row.pop('Description')
    row['Quantity'] = row.pop('SOH')
    client.provider.sync_once()
    renamed = {**get_mapping(), 'id': 'Asset Key', 'name': 'Item Title', 'stock': 'Quantity'}
    response = client.put('/api/admin/mapping', json={'mapping': renamed})
    assert response.status_code == 200
    client.provider.sync_once()
    assert client.provider.get_inventory()[0]['name'] == 'Adapter'


def test_mapping_rejects_only_missing_core_headings(client):
    response = client.put('/api/admin/mapping', json={'mapping': {**DEFAULT_MAPPING, 'stock': 'No such heading'}})
    assert response.status_code == 400
    assert 'stock' in response.json()['detail']


def test_connection_settings_persist_without_exposing_urls(client,monkeypatch):
    url='https://test.environment.api.powerplatform.com/invoke?test_signature=not-real'
    response=client.put('/api/admin/connections',json={'read_url':url,'update_url':url,'interval_seconds':120,'sync_enabled':False})
    assert response.status_code==200 and 'secret-test' not in response.text
    assert response.json()['read_configured'] and response.json()['interval_seconds']==120
    assert runtime_setting('inventory_read_url')==url
    client.provider.remote.calls.clear();client.provider.sync_once()
    assert client.provider.remote.calls==[]
    assert client.provider.get_sync_status()['paused']
    assert client.put('/api/admin/connections',json={'read_url':'http://localhost/'}).status_code==422
    assert client.put('/api/admin/connections',json={'interval_seconds':1}).status_code==422


def test_migration_keeps_accounts_and_superadmin_after_restart(client):
    with db_session() as db: before=[tuple(r) for r in db.execute('SELECT id,username,password_hash FROM users')]
    initialise('different-hash')
    with db_session() as db:
        assert [tuple(r) for r in db.execute('SELECT id,username,password_hash FROM users')]==before
        assert effective_user(dict(db.execute('SELECT * FROM users WHERE id=1').fetchone()))['role']=='superadmin'


def test_blank_excel_ids_pause_sync_without_inventing_ids(client):
    remote=client.provider.remote
    remote.rows['blank']={'Inventory ID':'','Description':'Added in Excel','SOH':1}
    client.provider.sync_once()
    assert 'blank or duplicate' in client.provider.get_sync_status()['error']
    assert len(client.provider.get_inventory())==1
    remote.rows['blank']['Inventory ID']='INV-NEW-UNIQUE'
    client.provider.sync_once()
    assert len(client.provider.get_inventory())==2


def test_real_auth_uses_database_role_not_token_role(client):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import router
    app=FastAPI();app.include_router(router)
    session=TestClient(app)
    token=create_token({'id':1,'username':'admin','role':'superadmin'})
    session.headers['Authorization']='Bearer '+token
    assert session.get('/api/auth/me').json()['role']=='superadmin'
    with db_session() as db: db.execute("UPDATE users SET access_level='warehouse_admin' WHERE id=1")
    assert session.get('/api/auth/me').json()['role']=='warehouse_admin'
    assert session.get('/api/admin/users').status_code==403
    session.headers.clear()
    assert session.get('/api/admin/users').status_code==401


def test_pending_changes_block_url_repointing(client):
    client.provider.adjust_stock('A',-1,10)
    response=client.put('/api/admin/connections',json={'read_url':'https://test.environment.api.powerplatform.com/new'})
    assert response.status_code==409
    assert runtime_setting('inventory_read_url') is None


def test_legacy_accounts_migrate_without_losing_passwords(tmp_path,monkeypatch):
    import sqlite3
    path=tmp_path/'legacy.db'
    monkeypatch.setitem(settings.__dict__,'database_path',path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,display_name TEXT,password_hash TEXT,role TEXT CHECK(role IN ('admin','standard')),disabled INTEGER DEFAULT 0,created_at TEXT)")
        db.executemany('INSERT INTO users VALUES(?,?,?,?,?,0,?)',[(1,settings.admin_username,'Primary','preserved-hash','admin','date'),(2,'manager','Manager','other-hash','admin','date'),(3,'worker','Worker','worker-hash','standard','date')])
    initialise('not-used')
    with db_session() as db:
        users=[effective_user(dict(row)) for row in db.execute('SELECT * FROM users ORDER BY id')]
    assert [u['role'] for u in users]==['superadmin','warehouse_admin','standard']
    assert users[0]['password_hash']=='preserved-hash'
