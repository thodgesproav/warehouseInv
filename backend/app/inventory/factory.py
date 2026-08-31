from functools import lru_cache

from ..config import settings
from .base import InventoryProvider
from .local_excel import LocalExcelInventoryProvider
from .local_sync import LocalSyncInventoryProvider


@lru_cache
def get_provider() -> InventoryProvider:
    if settings.inventory_provider == "power_automate": return LocalSyncInventoryProvider()
    return LocalExcelInventoryProvider()
