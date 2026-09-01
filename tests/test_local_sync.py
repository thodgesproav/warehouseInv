from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import DEFAULT_MAPPING, settings
from app.database import db_session, get_mapping, initialise, set_mapping
from app.inventory.base import InsufficientStock, StockConflict, SyncUnavailable
from app.inventory.local_sync import LocalSyncInventoryProvider
from app.inventory.power_automate import PowerAutomateInventoryProvider


class FakeExcel:
    def __init__(self):
        self.rows = {'A': {'Inventory ID': 'A', 'Description': 'Adapter', 'SOH': 10, 'Notes': '', 'Bin Name': 'Shelf 1'}}
        self.calls = []
        self.offline = False
        self.lose_reply = False
        self.fail_before_write = False
        self.on_read = None
        self.on_write = None

    def product(self, row):
        return PowerAutomateInventoryProvider._normalise(copy.deepcopy(row), get_mapping())

    def get_live_inventory(self):
        self.calls.append('read')
        if self.offline: raise SyncUnavailable('offline')
        snapshot = [self.product(row) for row in self.rows.values()]
        if self.on_read: self.on_read()
        return snapshot

    def get_columns(self): return list(next(iter(self.rows.values()), {'Inventory ID': '', 'Description': '', 'SOH': 0, 'Notes': ''}))

    def _before(self, kind):
        self.calls.append(kind)
        if self.on_write: self.on_write()
        if self.fail_before_write: raise SyncUnavailable('request outcome unknown')

    def _reply(self, row):
        if self.lose_reply: raise SyncUnavailable('response lost after commit')
        return self.product(row)

    def adjust_stock(self, item_id, quantity, expected):
        self._before('stock')
        row = self.rows[item_id]
        stock = get_mapping()['stock']
        if row[stock] != expected: raise StockConflict('changed')
        row[stock] += quantity
        return self._reply(row)

    def update_item(self, item_id, fields):
        self._before('update')
        self.rows[item_id].update(fields)
        return self._reply(self.rows[item_id])

    def add_item(self, fields):
        self._before('add')
        self.rows[fields[get_mapping()['id']]] = copy.deepcopy(fields)
        return self._reply(fields)

    def delete_item(self, item_id):
        self._before('delete')
        del self.rows[item_id]
        if self.lose_reply: raise SyncUnavailable('response lost')


@pytest.fixture
def synced(tmp_path):
    previous = settings.database_path
    object.__setattr__(settings, 'database_path', tmp_path / 'inventory.db')
    initialise('unused')
    remote = FakeExcel()
    local = LocalSyncInventoryProvider(remote)
    local.sync_once()
    try:
        yield local, remote
    finally:
        object.__setattr__(settings, 'database_path', previous)


def test_reads_and_local_updates_never_call_excel(synced):
    local, remote = synced
    remote.calls.clear()
    assert local.get_inventory()[0]['stock'] == 10
    result = local.adjust_stock('A', -2, 10)
    assert result['stock'] == 8 and result['sync_status'] == 'pending'
    local.update_item('A', {'Notes': 'local note'})
    assert local.get_inventory()[0]['raw_fields']['Notes'] == 'local note'
    assert remote.calls == []
    assert local.get_sync_status()['pending_count'] == 2
    local.sync_once()
    assert remote.rows['A']['SOH'] == 8
    assert remote.rows['A']['Notes'] == 'local note'
    assert local.get_sync_status()['pending_count'] == 0


def test_columns_refresh_and_removed_edit_is_rejected(synced):
    local, remote = synced
    remote.rows['A']['Added heading'] = 'new'
    del remote.rows['A']['Notes']
    local.sync_once()
    assert 'Added heading' in local.get_columns()
    assert 'Notes' not in local.get_columns()
    with pytest.raises(StockConflict): local.update_item('A', {'Notes': 'obsolete edit'})
    local.update_item('A', {'Added heading': 'edited'})
    assert local.get_inventory()[0]['raw_fields']['Added heading'] == 'edited'


def test_removed_optional_mapping_does_not_block_sync(synced):
    local, remote = synced
    assert get_mapping()['location'] == 'Bin Name'
    del remote.rows['A']['Bin Name']
    local.sync_once()
    assert get_mapping()['location'] == 'Bin Name'
    assert local.get_sync_status()['ok']


def test_core_headings_can_be_remapped_after_excel_rename(synced):
    local, remote = synced
    row = remote.rows['A']
    row['Asset Key'] = row.pop('Inventory ID')
    row['Item Title'] = row.pop('Description')
    row['Quantity'] = row.pop('SOH')
    local.sync_once()
    assert local.get_columns() == ['Notes', 'Bin Name', 'Asset Key', 'Item Title', 'Quantity']
    assert 'remap: id, name, stock' in local.get_sync_status()['error']
    assert local.get_inventory()[0]['name'] == 'Adapter'
    set_mapping({**get_mapping(), 'id': 'Asset Key', 'name': 'Item Title', 'stock': 'Quantity'})
    local.sync_once()
    assert local.get_sync_status()['ok']
    assert local.get_inventory()[0]['name'] == 'Adapter'
    local.adjust_stock('A', -1, 10)
    local.sync_once()
    assert remote.rows['A']['Quantity'] == 9


def test_sheet_changes_and_deletions_are_pulled(synced):
    local, remote = synced
    remote.rows['A']['Notes'] = 'Excel edit'
    local.sync_once()
    assert local.get_inventory()[0]['raw_fields']['Notes'] == 'Excel edit'
    remote.rows['B'] = {'Inventory ID': 'B', 'Description': 'Excel row', 'SOH': 2, 'Notes': '', 'Bin Name': 'Shelf 2'}
    local.sync_once()
    assert {item['id'] for item in local.get_inventory()} == {'A', 'B'}
    remote.rows.clear()
    local.sync_once()
    assert local.get_inventory() == []
    assert local.get_sync_status()['ready']


def test_queue_and_local_snapshot_survive_restart(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    restarted = LocalSyncInventoryProvider(remote)
    assert restarted.get_inventory()[0]['stock'] == 9
    assert restarted.get_sync_status()['pending_count'] == 1
    restarted.sync_once()
    assert remote.rows['A']['SOH'] == 9


def test_offline_pull_never_flushes_queue_or_discards_edits(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.offline = True
    remote.calls.clear()
    local.sync_once()
    assert remote.calls == ['read']
    assert local.get_inventory()[0]['stock'] == 9
    assert local.get_sync_status()['pending_count'] == 1
    assert not local.get_sync_status()['ok']
    remote.offline = False
    local.sync_once()
    assert remote.rows['A']['SOH'] == 9
    assert local.get_sync_status()['ok']


def test_nonoverlapping_changes_merge(synced):
    local, remote = synced
    local.update_item('A', {'Notes': 'app edit'})
    remote.rows['A']['Bin Name'] = 'Excel bin'
    local.sync_once()
    assert remote.rows['A']['Notes'] == 'app edit'
    assert local.get_inventory()[0]['location'] == 'Excel bin'


def test_overlapping_edits_conflict_and_use_excel_resolution(synced):
    local, remote = synced
    local.update_item('A', {'Notes': 'app edit'})
    remote.rows['A']['Notes'] = 'Excel edit'
    local.sync_once()
    assert local.get_sync_status()['conflict_count'] == 1
    assert remote.rows['A']['Notes'] == 'Excel edit'
    assert local.get_inventory()[0]['raw_fields']['Notes'] == 'app edit'
    local.use_excel('A')
    assert local.get_sync_status()['pending_count'] == 0
    assert local.get_inventory()[0]['raw_fields']['Notes'] == 'Excel edit'


def test_independent_identical_stock_deduction_is_a_conflict(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.rows['A']['SOH'] = 9
    local.sync_once()
    assert local.get_sync_status()['conflict_count'] == 1
    assert 'stock' not in remote.calls


def test_conflict_blocks_later_writes_for_that_item(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    local.update_item('A', {'Notes': 'later edit'})
    remote.rows['A']['SOH'] = 2
    local.sync_once()
    assert remote.rows['A']['Notes'] == ''
    assert local.get_sync_status()['pending_count'] == 2


def test_stock_server_check_catches_race_after_snapshot(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.on_write = lambda: remote.rows['A'].update(SOH=5)
    local.sync_once()
    assert remote.rows['A']['SOH'] == 5
    assert local.get_sync_status()['conflict_count'] == 1


def test_lost_stock_reply_is_verified_not_resent(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.lose_reply = True
    local.sync_once()
    assert remote.rows['A']['SOH'] == 9
    assert local.get_sync_status()['conflict_count'] == 1
    remote.lose_reply = False
    local.sync_once()
    assert remote.rows['A']['SOH'] == 9
    assert remote.calls.count('stock') == 1
    assert local.get_sync_status()['pending_count'] == 0


def test_uncertain_write_not_retried_when_remote_is_unchanged(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.fail_before_write = True
    local.sync_once()
    remote.fail_before_write = False
    local.sync_once()
    assert remote.calls.count('stock') == 1
    assert local.get_conflicts()[0]['state'] == 'uncertain'


def test_crashed_sending_operation_is_not_replayed(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    with db_session() as db: db.execute("UPDATE inventory_outbox SET state='sending'")
    restarted = LocalSyncInventoryProvider(remote)
    restarted.sync_once()
    assert remote.rows['A']['SOH'] == 10
    assert restarted.get_conflicts()[0]['state'] == 'uncertain'


def test_edits_during_slow_pull_are_preserved_and_do_not_block(synced):
    local, remote = synced
    entered, release = threading.Event(), threading.Event()
    def wait():
        entered.set()
        assert release.wait(5)
    remote.on_read = wait
    with ThreadPoolExecutor() as pool:
        future = pool.submit(local.sync_once)
        assert entered.wait(2)
        try:
            local.adjust_stock('A', -1, 10)
            assert local.get_inventory()[0]['stock'] == 9
        finally: release.set()
        future.result(timeout=5)
    assert local.get_inventory()[0]['stock'] == 9


def test_edits_queued_during_write_survive_reconciliation(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    remote.on_write = lambda: local.update_item('A', {'Notes': 'queued during write'})
    local.sync_once()
    assert local.get_inventory()[0]['raw_fields']['Notes'] == 'queued during write'
    assert local.get_sync_status()['pending_count'] == 1
    remote.on_write = None
    local.sync_once()
    assert remote.rows['A']['Notes'] == 'queued during write'


def test_concurrent_local_stock_edits_only_one_wins(synced):
    local, _ = synced
    def take(_):
        try: return local.adjust_stock('A', -1, 10)['stock']
        except StockConflict: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(map(str, pool.map(take, range(2)))) == ['9', 'conflict']


def test_add_and_delete_are_local_then_sync(synced):
    local, remote = synced
    item = local.add_item({'Description': 'New', 'SOH': 3})
    assert item['id'] not in remote.rows
    local.sync_once()
    assert remote.rows[item['id']]['SOH'] == 3
    local.delete_item(item['id'])
    assert item['id'] in remote.rows
    local.sync_once()
    assert item['id'] not in remote.rows


def test_lost_add_reply_does_not_duplicate_row(synced):
    local, remote = synced
    item = local.add_item({'Description': 'New', 'SOH': 3})
    remote.lose_reply = True
    local.sync_once()
    remote.lose_reply = False
    local.sync_once()
    assert remote.calls.count('add') == 1
    assert item['id'] in remote.rows
    assert local.get_sync_status()['pending_count'] == 0


def test_delete_conflicts_with_remote_edit(synced):
    local, remote = synced
    local.delete_item('A')
    remote.rows['A']['Notes'] = 'do not delete'
    local.sync_once()
    assert 'A' in remote.rows
    assert local.get_sync_status()['conflict_count'] == 1


def test_duplicate_remote_ids_abort_sync_without_touching_local(synced):
    local, remote = synced
    remote.rows['B'] = copy.deepcopy(remote.rows['A'])
    local.sync_once()
    assert not local.get_sync_status()['ok']
    assert len(local.get_inventory()) == 1


def test_cannot_write_before_first_download(synced):
    local, _ = synced
    with db_session() as db: db.execute('DELETE FROM inventory_sync_meta')
    with pytest.raises(SyncUnavailable): local.add_item({'Description': 'no'})


def test_negative_and_fractional_local_stock_are_rejected(synced):
    local, _ = synced
    with pytest.raises(InsufficientStock): local.adjust_stock('A', -11, 10)
    with pytest.raises(StockConflict): local.update_item('A', {'SOH': -1})
    with pytest.raises(StockConflict): local.update_item('A', {'SOH': 1.5})


def test_audit_status_follows_outbox(synced):
    from app.api import record_transaction
    local, _ = synced
    result = local.adjust_stock('A', -1, 10)
    record_transaction({'id': 1, 'username': 'test'}, 'A', 'Adapter', -1, 10, 9, True, 'pending', sync_operation_id=result['sync_operation_id'])
    local.sync_once()
    with db_session() as db:
        assert db.execute('SELECT sync_status FROM transactions').fetchone()[0] == 'synced'


def test_force_sync_only_schedules_network_work(synced):
    local, remote = synced
    remote.calls.clear()
    assert local.get_inventory(force=True)[0]['id'] == 'A'
    assert local._wake.is_set()
    assert remote.calls == []


def test_stale_form_cannot_overwrite_new_local_value(synced):
    local, _ = synced
    form = local.get_inventory()[0]['raw_fields']
    local.update_item('A', {'Notes': 'first save'})
    with pytest.raises(StockConflict):
        local.update_item('A', {'Notes': 'stale save'}, form)


def test_stale_unedited_field_does_not_conflict(synced):
    local, _ = synced
    form = local.get_inventory()[0]['raw_fields']
    local.adjust_stock('A', -1, 10)
    local.update_item('A', {'Notes': 'only notes edited'}, form)
    assert local.get_inventory()[0]['stock'] == 9


def test_card_keeps_conflict_state_when_later_edits_are_pending(synced):
    local, remote = synced
    local.adjust_stock('A', -1, 10)
    local.update_item('A', {'Notes': 'later'})
    remote.rows['A']['SOH'] = 4
    local.sync_once()
    assert local.get_inventory()[0]['sync_status'] == 'conflict'


def test_worker_initial_sync_and_manual_wake(synced):
    local, remote = synced
    seen = threading.Event()
    remote.on_read = seen.set
    local.start()
    try:
        assert seen.wait(3)
        # Wait on the worker's cycle lock, not on elapsed wall clock sleeps.
        with local._cycle_lock: pass
        seen.clear()
        local.request_sync()
        assert seen.wait(3)
    finally:
        local.stop()
    assert not local._thread.is_alive()


def test_read_only_api_uses_local_data(synced, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api as api_module
    from app.main import app
    from app.auth import current_user
    local, remote = synced
    monkeypatch.setattr(api_module, 'get_provider', lambda: local)
    app.dependency_overrides[current_user] = lambda: {'id': 1, 'username': 'test', 'role': 'superadmin'}
    remote.calls.clear()
    try:
        # No lifespan: the fixture owns the isolated DB and fake provider.
        client = TestClient(app)
        result = client.get('/api/inventory')
        assert result.status_code == 200
        assert result.json()['sync']['mode'] == 'local_first'
        updated = client.post('/api/inventory/A/adjust', json={'quantity': -1, 'expected_current_soh': 10})
        assert updated.status_code == 200
        assert updated.json()['sync_status'] == 'pending'
        assert remote.calls == []
        status = client.get('/api/admin/status')
        assert status.json()['pending_count'] == 1
    finally:
        app.dependency_overrides.clear()
