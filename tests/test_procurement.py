import copy
import csv
import io
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_notifications import client as base_client, request, jobs
from app import procurement_api
from app.database import db_session, get_mapping, set_mapping


@pytest.fixture
def client(base_client, monkeypatch):
    client = base_client
    client.app.include_router(procurement_api.router)
    monkeypatch.setattr(procurement_api, 'get_provider', lambda: client.provider)
    raw = client.provider.remote.rows['A']
    raw.update({'Min-Reorder Level': 20, 'Max': 100, 'Manufacturer': 'Example',
                'Master/Part No.': 'PART-123', 'On Order': '', 'Quantity On Order': '', 'Image': ''})
    client.provider.sync_once()
    return client


def listing(client):
    response = client.get('/api/admin/procurement')
    assert response.status_code == 200, response.text
    return response.json()['items']


def payload(client, action, selected=None):
    selected = selected if selected is not None else listing(client)
    return {'action': action, 'batch_id': str(uuid.uuid4()),
            'entries': [{'key': row['key'], 'version': row['version']} for row in selected]}


def action(client, status):
    response = client.post('/api/admin/procurement/actions', json=payload(client, status))
    assert response.status_code == 200, response.text
    return response.json()


def test_low_stock_strict_threshold_and_nonstock_exclusion(client):
    remote = client.provider.remote
    base = copy.deepcopy(remote.rows['A'])
    for identifier, stock, maximum in [('ZERO', 0, 0), ('EQUAL', 20, 100), ('ABOVE', 50, 100), ('FULL', 100, 100)]:
        remote.rows[identifier] = {**base, 'Inventory ID': identifier, 'SOH': stock, 'Max': maximum}
    client.provider.sync_once()
    rows = listing(client)
    assert len(rows) == 1 and rows[0]['key'] == 'stock:A' and rows[0]['quantity'] == 90


def test_csv_contains_procurement_fields_and_escapes_formulas_and_commas(client):
    client.provider.update_item('A', {'Manufacturer': '=UNSAFE()', 'Description': 'Cable, "long"\nsecond line'})
    request(client, manufacturer='Pacdata', manufacturer_model='NEW-001')
    selected = payload(client, 'ordered')
    response = client.post('/api/admin/procurement/export', json={'entries': selected['entries']})
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
    assert rows[0] == ['Manufacturer', 'Quantity', 'Master Number', 'Description']
    assert rows[1] == ["'=UNSAFE()", '90', 'PART-123', 'Cable, "long"\nsecond line']
    assert rows[2] == ['Pacdata', '200', 'NEW-001', '<Adapter>']
    assert all(row['status'] == 'new' for row in listing(client))


def test_bulk_orders_requests_and_receives_fixed_quantities_once(client):
    request(client, manufacturer='Supplier', manufacturer_model='R1', notify_available=True)
    order_payload = payload(client, 'ordered')
    response = client.post('/api/admin/procurement/actions', json=order_payload)
    assert response.status_code == 200 and response.json()['changed'] == 2
    assert client.post('/api/admin/procurement/actions', json=order_payload).json() == response.json()
    assert {r['status'] for r in listing(client)} == {'ordered'}
    product = client.provider.get_inventory()[0]
    assert product['on_order'] and product['quantity_on_order'] == 90 and product['stock'] == 10
    client.provider.adjust_stock('A', -5, 10)
    receive = payload(client, 'available')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post('/api/admin/procurement/actions', json=receive), range(2)))
    assert all(r.status_code == 200 for r in results)
    products = {p['id']: p for p in client.provider.get_inventory()}
    assert products['A']['stock'] == 95 and not products['A']['on_order']
    added = [p for p in products.values() if p['id'] != 'A']
    assert len(added) == 1 and added[0]['stock'] == 200 and added[0]['manufacturer'] == 'Supplier'
    assert len(jobs()) == 2  # Request received notification queued only once.
    assert listing(client) == []
    with db_session() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='order_received'").fetchone()[0] == 1
    client.provider.sync_once()
    assert client.provider.remote.rows['A']['SOH'] == 95
    assert client.provider.remote.rows['A']['Quantity On Order'] == 0


def test_stale_selection_rejected_without_mutation(client):
    selected = payload(client, 'ordered')
    client.provider.adjust_stock('A', -1, 10)
    assert client.post('/api/admin/procurement/actions', json=selected).status_code == 409
    with db_session() as db:
        assert db.execute('SELECT COUNT(*) FROM procurement_orders').fetchone()[0] == 0


def test_cannot_receive_inventory_before_ordering(client):
    assert client.post('/api/admin/procurement/actions', json=payload(client, 'available')).status_code == 409
    assert client.provider.get_inventory()[0]['stock'] == 10


def test_request_can_be_received_directly_and_then_removed_from_list(client):
    request(client)
    selected = [r for r in listing(client) if r['kind'] == 'request']
    assert client.post('/api/admin/procurement/actions', json=payload(client, 'available', selected)).status_code == 200
    assert all(r['kind'] == 'inventory' for r in listing(client))


def test_failed_bulk_rolls_back_stock_requests_orders_and_notifications(client, monkeypatch):
    request(client, notify_available=True)
    before_jobs = len(jobs())
    original = client.provider._put
    def fail_on_request(db, item_id, raw):
        if item_id != 'A': raise RuntimeError('test failure')
        original(db, item_id, raw)
    action(client, 'ordered')
    monkeypatch.setattr(client.provider, '_put', fail_on_request)
    with pytest.raises(RuntimeError):
        client.post('/api/admin/procurement/actions', json=payload(client, 'available'))
    assert client.provider.get_inventory()[0]['stock'] == 10
    assert all(r['status'] == 'ordered' for r in listing(client))
    assert len(jobs()) == before_jobs


def test_mapped_columns_and_existing_external_order(client):
    raw = client.provider.remote.rows['A']
    raw.update({'Custom Min': 15, 'Custom Max': 120, 'Quantity On Order': 30, 'On Order': 'Yes'})
    client.provider.sync_once()
    set_mapping({**get_mapping(), 'reorder_trigger': 'Custom Min', 'max_quantity': 'Custom Max'})
    row = listing(client)[0]
    assert row['quantity'] == 30 and row['key'] == 'external:A'
    action(client, 'available')
    assert client.provider.get_inventory()[0]['stock'] == 40


def test_delete_cancels_open_order_and_reaches_remote(client):
    action(client, 'ordered')
    assert client.delete('/api/admin/inventory/A').status_code == 204
    assert listing(client) == []
    with db_session() as db:
        assert db.execute('SELECT status FROM procurement_orders').fetchone()[0] == 'cancelled'
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type='delete_item'").fetchone()[0] == 1
    client.provider.sync_once()
    assert 'A' not in client.provider.remote.rows


def test_access_control_and_readonly_editor_fields(client):
    for field in ['Image', 'On Order', 'Quantity On Order']:
        assert client.put('/api/admin/inventory/A', json={'fields': {field: 'changed'}}).status_code == 400
    client.actor['role'] = 'warehouse_admin'
    assert client.get('/api/admin/procurement').status_code == 200
    data = payload(client, 'ordered')
    client.actor['role'] = 'standard'
    assert client.get('/api/admin/procurement').status_code == 403
    assert client.post('/api/admin/procurement/actions', json=data).status_code == 403
    assert client.post('/api/admin/procurement/export', json={'entries': data['entries']}).status_code == 403


def test_duplicate_selections_and_reused_batch_id_rejected(client):
    data = payload(client, 'ordered')
    duplicate = {**data, 'entries': data['entries'] * 2}
    assert client.post('/api/admin/procurement/actions', json=duplicate).status_code == 422
    assert client.post('/api/admin/procurement/actions', json=data).status_code == 200
    data['action'] = 'available'
    assert client.post('/api/admin/procurement/actions', json=data).status_code == 409
