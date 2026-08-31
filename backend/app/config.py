from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Warehouse Inventory")
    environment: str = os.getenv("ENVIRONMENT", "development")
    secret_key: str = field(default=os.getenv("SECRET_KEY", "development-only-change-me"), repr=False)
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = field(default=os.getenv("ADMIN_PASSWORD", "ChangeMe-Immediately-123!"), repr=False)
    database_path: Path = Path(os.getenv("DATABASE_PATH", "data/inventory.db"))
    workbook_path: Path = Path(os.getenv("WORKBOOK_PATH", "data/runtime/Warehouse Consumables.xlsx"))
    source_workbook_path: Path = Path(os.getenv("SOURCE_WORKBOOK_PATH", "Warehouse Consumables.xlsx"))
    image_path: Path = Path(os.getenv("IMAGE_PATH", "data/images"))
    backup_path: Path = Path(os.getenv("BACKUP_PATH", "data/backups"))
    inventory_provider: str = os.getenv("INVENTORY_PROVIDER", "local_excel")
    inventory_sheet: str = os.getenv("INVENTORY_SHEET", "Warehouse")
    sync_interval_seconds: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "60"))
    read_url: str = field(default=os.getenv("POWER_AUTOMATE_READ_URL", ""), repr=False)
    update_url: str = field(default=os.getenv("POWER_AUTOMATE_UPDATE_URL", ""), repr=False)
    api_key: str = field(default=os.getenv("POWER_AUTOMATE_API_KEY", ""), repr=False)
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "480"))
    session_days: int = max(1, min(365, int(os.getenv("SESSION_DAYS", "30"))))
    session_cookie_secure: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"


settings = Settings()

DEFAULT_MAPPING = {
    "id": "Inventory ID",
    "name": "Description",
    "manufacturer": "Manufacturer",
    "model": "Master/Part No.",
    "sku": "Master/Part No.",
    "stock": "SOH",
    "location": "Bin Name",
    "on_order": "On Order",
    "quantity_on_order": "Quantity On Order",
    "image": "Image",
    "category": "TYPE",
    "discontinued": "Discontinued",
    "reorder_trigger": "Min-Reorder Level",
    "max_quantity": "Max",
    "description": "Description",
}
