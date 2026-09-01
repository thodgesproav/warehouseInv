from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.auth import admin_user
from app.config import settings
from app.database import initialise
from app.inventory.base import SyncUnavailable
from app.inventory.power_automate import PowerAutomateInventoryProvider


def test_standard_user_cannot_use_admin_dependency():
    with pytest.raises(HTTPException) as exc:
        admin_user({"role": "standard"})
    assert exc.value.status_code == 403


def test_image_assignment_is_persisted_in_excel(provider):
    item = provider.get_inventory()[0]
    provider.update_item(item["id"], {"Image": f"/api/images/{item['id']}.webp"})
    updated = next(x for x in provider.get_inventory() if x["id"] == item["id"])
    assert updated["image"].endswith(".webp")


def test_power_automate_cache_failure_and_recovery(tmp_path, monkeypatch):
    object.__setattr__(settings, "database_path", tmp_path / "inventory.db")
    initialise('unused')
    provider = PowerAutomateInventoryProvider()
    monkeypatch.setattr(provider, "_post", lambda *_: {"items": [{"id": "A", "name": "Adapter", "stock": 2}]})
    assert provider.get_inventory()[0]["stock"] == 2
    assert json.loads(provider.cache.read_text())[0]["id"] == "A"

    def fail(*_):
        raise SyncUnavailable("offline")

    monkeypatch.setattr(provider, "_post", fail)
    assert provider.get_inventory()[0]["id"] == "A"
    monkeypatch.setattr(provider, "_post", lambda *_: {"items": [{"id": "A", "name": "Adapter", "stock": 5}]})
    assert provider.get_inventory(force=True)[0]["stock"] == 5


def test_power_automate_sends_selected_identity_headings(tmp_path, monkeypatch):
    from app.database import set_mapping
    from app.config import DEFAULT_MAPPING
    object.__setattr__(settings, "database_path", tmp_path / "inventory.db")
    initialise('unused')
    set_mapping({**DEFAULT_MAPPING, 'id': 'Asset Key', 'stock': 'Quantity'})
    provider = PowerAutomateInventoryProvider()
    seen = []
    monkeypatch.setattr(provider, '_post', lambda _url, payload: seen.append(payload) or {'items': []})
    provider.get_live_inventory()
    assert seen[0]['fields']['__inventoryMapping'] == {'id': 'Asset Key', 'stock': 'Quantity'}
    provider.get_columns()
    assert seen[1]['fields']['__inventoryMapping'] == {'id': 'Asset Key', 'stock': 'Quantity'}


def test_power_automate_follows_asynchronous_response(monkeypatch, provider):
    class Response:
        def __init__(self, status_code, body=None, headers=None):
            self.status_code = status_code
            self._body = body
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    monkeypatch.setattr(
        "app.inventory.power_automate.httpx.post",
        lambda *args, **kwargs: Response(202, headers={"Location": "https://flow/status/1", "Retry-After": "0"}),
    )
    monkeypatch.setattr(
        "app.inventory.power_automate.httpx.get",
        lambda url, **kwargs: Response(200, {"items": [{"id": "A", "name": "Adapter", "stock": 2}]}),
    )
    monkeypatch.setattr("app.inventory.power_automate.time.sleep", lambda *_: None)

    result = PowerAutomateInventoryProvider()._post("https://flow/trigger", {"action": "readInventory"})
    assert result["items"][0]["id"] == "A"
