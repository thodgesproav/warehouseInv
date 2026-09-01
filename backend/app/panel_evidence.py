from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
import secrets
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import Request
from PIL import Image, UnidentifiedImageError

from .config import settings
from .database import db_session, utcnow


log = logging.getLogger("inventory.panel_evidence")
MAX_EVIDENCE_BYTES = 25_000_000
REDIRECT_CODES = {301, 302, 303, 307, 308}


def is_warehouse_panel_request(request: Request) -> bool:
    expected = settings.warehouse_panel_token
    supplied = request.headers.get("x-warehouse-panel-token", "")
    user_agent = request.headers.get("user-agent", "")
    return bool(expected and supplied and "Crestron Touchpanel" in user_agent and secrets.compare_digest(supplied, expected))


def _record_failure(transaction_id: int, message: str) -> None:
    with db_session() as db:
        db.execute("UPDATE transactions SET evidence_error=? WHERE id=?", (message, transaction_id))


def store_transaction_evidence(transaction_id: int, content: bytes) -> bool:
    """Validate and retain an original-resolution JPEG without resizing it."""
    try:
        if not content or len(content) > MAX_EVIDENCE_BYTES:
            raise ValueError("Camera returned an empty or oversized image")
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            if image.format != "JPEG":
                raise ValueError("Camera snapshot is not JPEG")
            width, height = image.size

        target = settings.evidence_path / f"transaction-{transaction_id}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".jpg.part")
        temporary.write_bytes(content)
        temporary.replace(target)
        with db_session() as db:
            db.execute(
                "UPDATE transactions SET evidence_path=?,evidence_captured_at=?,evidence_error=NULL,evidence_width=?,evidence_height=? WHERE id=?",
                (str(target), utcnow(), width, height, transaction_id),
            )
        return True
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        log.warning("Panel evidence validation failed for transaction %s: %s", transaction_id, exc)
        _record_failure(transaction_id, "Camera image was invalid")
        return False


def capture_transaction_evidence(transaction_id: int) -> bool:
    """Fetch and retain the panel's original JPEG snapshot without resizing it."""
    if not settings.warehouse_panel_snapshot_url:
        _record_failure(transaction_id, "Warehouse panel camera is not configured")
        return False

    auth = None
    if settings.warehouse_panel_username:
        auth = (settings.warehouse_panel_username, settings.warehouse_panel_password)
    try:
        response = httpx.get(
            settings.warehouse_panel_snapshot_url,
            auth=auth,
            follow_redirects=False,
            timeout=5.0,
            verify=settings.warehouse_panel_verify_tls,
        )
        location = response.headers.get("location", "")
        if response.status_code in REDIRECT_CODES and urlsplit(urljoin(settings.warehouse_panel_snapshot_url, location)).path == "/userlogin.html":
            login_url = urljoin(settings.warehouse_panel_snapshot_url, location)
            snapshot_parts = urlsplit(settings.warehouse_panel_snapshot_url)
            origin = f"{snapshot_parts.scheme}://{snapshot_parts.netloc}"
            with httpx.Client(
                follow_redirects=False,
                timeout=5.0,
                verify=settings.warehouse_panel_verify_tls,
            ) as client:
                client.get(login_url).raise_for_status()
                login_response = client.post(
                    login_url,
                    data={"login": settings.warehouse_panel_username, "passwd": settings.warehouse_panel_password},
                    headers={
                        "Origin": origin,
                        "Referer": login_url,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                login_response.raise_for_status()
                response = client.get(settings.warehouse_panel_snapshot_url)
        response.raise_for_status()
        if store_transaction_evidence(transaction_id, response.content):
            return True
        _record_failure(transaction_id, "Camera snapshot was unavailable")
        return False
    except (httpx.HTTPError, OSError, UnidentifiedImageError, ValueError) as exc:
        log.warning("Panel evidence capture failed for transaction %s: %s", transaction_id, exc)
        _record_failure(transaction_id, "Camera snapshot was unavailable")
        return False


def evidence_file(transaction_id: int) -> Path | None:
    with db_session() as db:
        row = db.execute("SELECT evidence_path FROM transactions WHERE id=?", (transaction_id,)).fetchone()
    if not row or not row[0]:
        return None
    target = Path(row[0]).resolve()
    root = settings.evidence_path.resolve()
    return target if target.is_relative_to(root) and target.is_file() else None
