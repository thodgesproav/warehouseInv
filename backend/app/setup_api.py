"""One-time, owner-authorised setup. No default accounts or public secret readback."""
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .auth import check_browser_request, hash_password
from .config import settings, DEFAULT_MAPPING
from .database import db_session, utcnow
from .notifications import valid_email, valid_flow_url, set_config

router = APIRouter(prefix='/api/setup')


def setup_required():
    with db_session() as db:
        return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None


def private_secret(path: Path):
    """Atomic creation supports restart and fails closed for symlinks or bad mounts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as existing:
            secret = existing.read().strip()
        if len(secret) < 32: raise RuntimeError('Invalid persistent security key; restore the key file from backup')
        return secret
    secret = secrets.token_urlsafe(48)
    with os.fdopen(fd, 'w') as target:
        target.write(secret + '\n')
        target.flush()
        os.fsync(target.fileno())
    return secret


def prepare_security():
    if settings.secret_key == 'development-only-change-me':
        object.__setattr__(settings, 'secret_key', private_secret(settings.database_path.parent / 'server-secret'))
    if settings.environment == 'production' and len(settings.secret_key) < 32:
        raise RuntimeError('SECRET_KEY must have at least 32 characters')
    if setup_required():
        private_secret(settings.database_path.parent / 'setup-token')


class SetupIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    setup_code: str = Field(min_length=32, max_length=200, repr=False)
    username: str = Field(min_length=3, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')
    display_name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(min_length=12, max_length=72, repr=False)
    read_url: str = Field(default='', max_length=4000, repr=False)
    update_url: str = Field(default='', max_length=4000, repr=False)
    api_key: str = Field(default='', max_length=1000, repr=False)
    configure_later: bool = False
    interval_seconds: int = Field(default=60, ge=10, le=3600)
    email_flow_url: str = Field(default='', max_length=4000, repr=False)
    admin_emails: list[str] = Field(default_factory=list, max_length=20)
    email_enabled: bool = False
    transaction_export_enabled: bool = False
    session_days: int = Field(default=30, ge=1, le=365)
    mapping: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_MAPPING), max_length=30)

    @field_validator('password')
    @classmethod
    def password_length(cls, value):
        if len(value.encode()) > 72: raise ValueError('Password must be at most 72 UTF-8 bytes')
        return value


@router.get('/status')
def status():
    required = setup_required()
    return {'required': required, 'mapping': DEFAULT_MAPPING if required else None,
            'provider': settings.inventory_provider if required else None}


@router.post('/complete', status_code=201)
def complete(data: SetupIn, request: Request):
    check_browser_request(request)
    if not setup_required(): raise HTTPException(409, 'Setup has already been completed')
    # Do not create missing security files in response to a web request.
    code_path = settings.database_path.parent / 'setup-token'
    if not code_path.is_file() or code_path.is_symlink(): raise HTTPException(503, 'Setup code is not ready; restart the server')
    if not secrets.compare_digest(data.setup_code, code_path.read_text().strip()):
        raise HTTPException(403, 'The setup code is incorrect')
    try:
        email = valid_email(data.email)
        recipients = list(dict.fromkeys(valid_email(e).lower() for e in (data.admin_emails or [email])))
        read_url = valid_flow_url(data.read_url.strip()) if data.read_url.strip() else ''
        update_url = valid_flow_url(data.update_url.strip()) if data.update_url.strip() else read_url
        email_url = valid_flow_url(data.email_flow_url.strip()) if data.email_flow_url.strip() else ''
    except ValueError as exc: raise HTTPException(422, str(exc)) from None
    if settings.inventory_provider == 'power_automate' and not data.configure_later and not read_url:
        raise HTTPException(422, 'Enter the inventory flow URL or choose Configure later')
    if data.email_enabled and not email_url: raise HTTPException(422, 'Enter an email flow URL before enabling delivery')
    if set(data.mapping) != set(DEFAULT_MAPPING) or any(len(value) > 200 for value in data.mapping.values()):
        raise HTTPException(422, 'Use the supplied mapping fields with valid column headings')
    if not data.mapping.get('name') or data.mapping.get('id') != 'Inventory ID' or data.mapping.get('stock') != 'SOH':
        raise HTTPException(422, 'A name heading is required; Inventory ID and SOH must retain their script headings')
    password_hash = hash_password(data.password)
    with db_session() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise HTTPException(409, 'Setup has already been completed')
        db.execute("INSERT INTO users(username,display_name,email,password_hash,role,access_level,created_at) VALUES(?,?,?,?,'admin','superadmin',?)",
                   (data.username, data.display_name, email, password_hash, utcnow()))
        for key, value in {
            'inventory_read_url': read_url, 'inventory_update_url': update_url, 'inventory_api_key': data.api_key,
            'inventory_sync_enabled': bool(read_url) and not data.configure_later,
            'sync_interval_seconds': data.interval_seconds, 'email_flow_url': email_url,
            'email_enabled': data.email_enabled, 'admin_request_emails': recipients,
            'transaction_export_enabled': data.transaction_export_enabled, 'session_days': data.session_days,
            'column_mapping': data.mapping, 'setup_completed_at': utcnow(),
        }.items(): set_config(db, key, value)
    # Workers start only after all account and configuration writes have committed.
    request.app.state.start_workers()
    return {'configured': True}
