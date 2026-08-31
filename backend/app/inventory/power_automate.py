from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..database import get_mapping, runtime_setting
from .base import InsufficientStock, InventoryProvider, StockConflict, SyncUnavailable


class PowerAutomateInventoryProvider(InventoryProvider):
    """HTTP boundary for Microsoft flows; it never calls Graph directly."""
    def __init__(self):
        self.cache = settings.database_path.parent / "inventory-cache.json"
        self.last_sync = None; self.error = None

    def _headers(self):
        key = runtime_setting('inventory_api_key', settings.api_key)
        return {"Content-Type": "application/json", **({"x-api-key": key} if key else {})}

    def _post(self, url: str, payload: dict) -> dict:
        if not url: raise SyncUnavailable("Power Automate URL is not configured")
        try:
            deadline = time.monotonic() + 90
            response = httpx.post(url, json=payload, headers=self._headers(), timeout=45)
            status_url = response.headers.get("Location")
            while response.status_code == 202:
                if not status_url:
                    raise SyncUnavailable("Power Automate accepted the request but did not provide a status URL")
                if time.monotonic() >= deadline:
                    raise SyncUnavailable("Power Automate did not finish within 90 seconds")
                try:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                except ValueError:
                    retry_after = 1
                time.sleep(max(0.25, min(retry_after, 5)))
                response = httpx.get(status_url, headers=self._headers(), timeout=45)
                status_url = response.headers.get("Location", status_url)
            if response.status_code == 409:
                detail = response.json()
                if detail.get("error") == "insufficient_stock": raise InsufficientStock(f"Only {detail.get('stock', 0)} are currently available")
                raise StockConflict(f"Current stock is {detail.get('stock', 'different')}")
            if response.status_code >= 400:
                raise SyncUnavailable(f"Power Automate returned HTTP {response.status_code}; check the flow run history")
            self.error = None
            return response.json()
        except (InsufficientStock, StockConflict):
            raise
        except Exception as exc:
            # HTTP client exception strings can contain the signed trigger URL.
            self.error = str(exc) if isinstance(exc, SyncUnavailable) else f"Power Automate request failed ({type(exc).__name__})"
            raise SyncUnavailable(self.error) from exc

    @staticmethod
    def _normalise(item: dict[str, Any], mapping: dict[str, str] | None = None) -> dict[str, Any]:
        if "id" in item and "name" in item:
            return {**item, "raw_fields": item.get("raw_fields", item)}
        mapping = mapping if mapping is not None else get_mapping()
        raw = {key: value for key, value in item.items() if not key.startswith("@") and key != "ItemInternalId"}
        def value(key: str, default: Any = "") -> Any:
            return raw.get(mapping.get(key, ""), default)
        try: stock = int(value("stock", 0) or 0)
        except (TypeError, ValueError): stock = 0
        try: quantity_on_order = int(value("quantity_on_order", 0) or 0)
        except (TypeError, ValueError): quantity_on_order = 0
        on_order = str(value("on_order", "")).strip().lower() in {"1", "true", "yes", "y", "on order", "ordered"}
        return {
            "id": str(value("id") or "").strip(), "name": str(value("name") or "Unnamed item").strip(),
            "manufacturer": str(value("manufacturer") or "").strip(), "model": str(value("model") or "").strip(),
            "sku": str(value("sku") or "").strip(), "stock": stock, "location": str(value("location") or "").strip(),
            "on_order": on_order, "quantity_on_order": quantity_on_order,
            "image": str(value("image") or "").strip(), "category": str(value("category") or "").strip(),
            "discontinued": str(value('discontinued', '')).strip().lower() in {'1','true','yes','y','x','discontinued'},
            "raw_fields": raw,
        }

    def get_live_inventory(self, force: bool = True) -> list[dict[str, Any]]:
        """A sync read must fail closed: cached rows must never drive remote writes."""
        data = self._post(runtime_setting('inventory_read_url', settings.read_url), {"action": "readInventory", "force": force})
        raw_products = data if isinstance(data, list) else data.get("items")
        if not isinstance(raw_products, list) or any(not isinstance(row, dict) for row in raw_products):
            raise SyncUnavailable("Power Automate did not return an inventory array")
        mapping = get_mapping() if any('id' not in item or 'name' not in item for item in raw_products) else {}
        return [self._normalise(item, mapping) for item in raw_products]

    def get_inventory(self, force: bool = False) -> list[dict[str, Any]]:
        try:
            products = self.get_live_inventory(force)
            self.cache.parent.mkdir(parents=True, exist_ok=True); self.cache.write_text(json.dumps(products), encoding="utf-8")
            self.last_sync = datetime.now(timezone.utc).isoformat(); return products
        except SyncUnavailable:
            if self.cache.exists(): return json.loads(self.cache.read_text(encoding="utf-8"))
            raise

    def adjust_stock(self, item_id: str, quantity: int, expected_current_soh: int) -> dict[str, Any]:
        result = self._post(runtime_setting('inventory_update_url', settings.update_url), {"action": "adjustStock", "itemId": item_id, "quantity": quantity, "expectedCurrentSOH": expected_current_soh})
        if result.get("item"):
            return {**self._normalise(result["item"]), "old_stock": result.get("old_stock", expected_current_soh)}
        return result
    def update_item(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        result = self._post(runtime_setting('inventory_update_url', settings.update_url), {"action": "updateItem", "itemId": item_id, "fields": fields})
        return self._normalise(result["item"]) if result.get("item") else result
    def add_item(self, fields: dict[str, Any]) -> dict[str, Any]:
        result = self._post(runtime_setting('inventory_update_url', settings.update_url), {"action": "addItem", "fields": fields})
        return self._normalise(result["item"]) if result.get("item") else result
    def delete_item(self, item_id: str) -> None: self._post(runtime_setting('inventory_update_url', settings.update_url), {"action": "deleteItem", "itemId": item_id})
    def get_columns(self) -> list[str]:
        data = self._post(runtime_setting('inventory_read_url', settings.read_url), {"action": "getColumns"})
        if data.get("columns"): return data["columns"]
        items = data.get("items", [])
        return [key for key in items[0] if not key.startswith("@") and key != "ItemInternalId"] if items else []
    def get_sync_status(self) -> dict[str, Any]: return {"provider": "power_automate", "ok": self.error is None, "last_sync": self.last_sync, "error": self.error, "cached": self.cache.exists()}
