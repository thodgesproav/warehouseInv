from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class LoginIn(BaseModel):
    username: str
    password: str
    # Omitted for backwards-compatible bearer API clients; browser sends a bool.
    remember_me: bool | None = None
class StockAdjustment(BaseModel): quantity: int = Field(lt=0); expected_current_soh: int = Field(ge=0)
class ItemFields(BaseModel):
    fields: dict[str, Any]
    base_fields: dict[str, Any] | None = None
class ItemRequestIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item_requested: str = Field(min_length=2, max_length=300)
    manufacturer_model: str = Field(default="", max_length=300)
    manufacturer: str = Field(default="", max_length=300)
    quantity: int = Field(default=1, ge=1, le=10000)
    notes: str = Field(default="", max_length=2000)
    notify_available: bool = False
class RequestStatus(BaseModel): status: Literal["ordered", "available"]
class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: Literal["warehouse_admin", "superadmin", "standard"] = "standard"
    email: str = Field(min_length=3, max_length=254)
class UserUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=10)
    role: Literal["warehouse_admin", "superadmin", "standard"] | None = None
    email: str | None = Field(default=None, min_length=3, max_length=254)
    disabled: bool | None = None
class MappingIn(BaseModel): mapping: dict[str, str]
