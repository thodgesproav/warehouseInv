from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import settings
from app.database import initialise
from app.inventory.local_excel import LocalExcelInventoryProvider


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def provider(tmp_path):
    workbook = tmp_path / "inventory.xlsx"
    shutil.copy2(ROOT / "Warehouse Consumables.xlsx", workbook)
    object.__setattr__(settings, "database_path", tmp_path / "inventory.db")
    object.__setattr__(settings, "backup_path", tmp_path / "backups")
    initialise("unused")
    result = LocalExcelInventoryProvider(workbook, "Warehouse")
    result.prepare_workbook()
    return result

