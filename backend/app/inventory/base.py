from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class InventoryError(Exception): pass
class ItemNotFound(InventoryError): pass
class InsufficientStock(InventoryError): pass
class StockConflict(InventoryError): pass
class SyncUnavailable(InventoryError): pass


class InventoryProvider(ABC):
    @abstractmethod
    def get_inventory(self, force: bool = False) -> list[dict[str, Any]]: ...
    @abstractmethod
    def adjust_stock(self, item_id: str, quantity: int, expected_current_soh: int) -> dict[str, Any]: ...
    @abstractmethod
    def update_item(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]: ...
    @abstractmethod
    def add_item(self, fields: dict[str, Any]) -> dict[str, Any]: ...
    @abstractmethod
    def delete_item(self, item_id: str) -> None: ...
    @abstractmethod
    def get_sync_status(self) -> dict[str, Any]: ...
    @abstractmethod
    def get_columns(self) -> list[str]: ...

