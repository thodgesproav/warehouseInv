from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import time
from urllib.parse import urlsplit

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .database import db_session, runtime_setting

bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE = 'inventory_session'


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check_browser_request(request: Request) -> None:
    """Custom header prevents cross-site forms; origin check also rejects sibling sites."""
    origin = request.headers.get('origin')
    if request.headers.get('x-inventory-request') != '1' or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Please use the inventory app for this action')
    if origin:
        source = urlsplit(origin)
        if (source.scheme, source.netloc) != (request.url.scheme, request.url.netloc):
            raise HTTPException(403, 'Cross-site request blocked')


def start_session(request: Request, response: Response, user: dict, remember: bool) -> None:
    lifetime = int(runtime_setting('session_days', settings.session_days)) * 86400 if remember else settings.access_token_minutes * 60
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db_session() as db:
        db.execute('DELETE FROM login_sessions WHERE expires_at<=?', (now,))
        previous = request.cookies.get(SESSION_COOKIE)
        if previous: db.execute('DELETE FROM login_sessions WHERE token_hash=?', (digest(previous),))
        db.execute('INSERT INTO login_sessions(token_hash,user_id,password_tag,expires_at) VALUES(?,?,?,?)',
                   (digest(token), user['id'], digest(user['password_hash']), now + lifetime))
    response.set_cookie(SESSION_COOKIE, token, max_age=lifetime if remember else None,
                        httponly=True, secure=settings.session_cookie_secure or request.url.scheme == 'https',
                        samesite='lax', path='/api')


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db_session() as db:
            db.execute('DELETE FROM login_sessions WHERE token_hash=?', (digest(token),))
    response.delete_cookie(SESSION_COOKIE, path='/api', httponly=True, samesite='lax',
                           secure=settings.session_cookie_secure or request.url.scheme == 'https')


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user["id"]), "username": user["username"], "role": user["role"], "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)},
        settings.secret_key,
        algorithm="HS256",
    )


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token and not credentials:
        if request.method not in ('GET', 'HEAD', 'OPTIONS'): check_browser_request(request)
        with db_session() as db:
            row = db.execute('SELECT u.*,s.password_tag FROM login_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',
                             (digest(token), int(time.time()))).fetchone()
        if not row or row['disabled'] or not secrets.compare_digest(row['password_tag'], digest(row['password_hash'])):
            raise HTTPException(401, 'Your session has expired. Please sign in again.')
        user = dict(row)
        user.pop('password_tag'); user.pop('password_hash')
        return effective_user(user)
    if not credentials:
        raise HTTPException(401, "Please log in")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Your session has expired") from exc
    with db_session() as db:
        row = db.execute("SELECT id,username,display_name,role,access_level,email,disabled FROM users WHERE id=?", (payload["sub"],)).fetchone()
    if not row or row["disabled"]:
        raise HTTPException(401, "This account is unavailable")
    return effective_user(dict(row))


def effective_user(user: dict) -> dict:
    result = dict(user)
    if result['role'] == 'admin':
        result['role'] = 'superadmin' if result.get('access_level') == 'superadmin' else 'warehouse_admin'
    return result


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ('warehouse_admin', 'superadmin'):
        raise HTTPException(403, "Warehouse administrator access required")
    return user


def superadmin_user(user: dict = Depends(current_user)) -> dict:
    if user['role'] != 'superadmin':
        raise HTTPException(403, 'Superadmin access required')
    return user
