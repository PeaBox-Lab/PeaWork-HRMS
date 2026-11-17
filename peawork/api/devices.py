"""HMAC-signed machine endpoints (biometric devices) for R2.

Mounted separately from the session-authenticated ``/api/v1`` router: the
device is authenticated by an HMAC-SHA256 signature over the raw request body
(``X-Device-Code`` + ``X-Signature`` headers), so cookie CSRF does not apply.
The shared secret is shown once at device creation and never serialized after.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BiometricDevice
from ..services import attendance as attendance_service
from ..services.bootstrap import get_organization
from ..services.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("/punches")
async def device_punches(request: Request, db: Session = Depends(get_db)):
    device_code = (request.headers.get("x-device-code") or "").strip().upper()
    signature = (request.headers.get("x-signature") or "").strip().lower()
    if not device_code or not signature:
        raise HTTPException(status_code=401, detail="X-Device-Code and X-Signature headers are required")

    org = get_organization(db)
    if org is None:
        raise NotFoundError("Organisation not initialised")

    device = db.scalar(
        select(BiometricDevice).where(
            BiometricDevice.org_id == org.id,
            BiometricDevice.device_code == device_code,
            BiometricDevice.active,
        )
    )
    # Identical 401 for unknown device and bad signature: no device oracle.
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid device credentials")

    body = await request.body()
    expected = hmac.new(device.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid device credentials")

    try:
        payload = await request.json()
    except Exception:
        raise ValidationError("Request body must be JSON") from None

    punches = payload.get("punches") if isinstance(payload, dict) else None
    if not isinstance(punches, list) or not punches:
        raise ValidationError("Body must be {'punches': [...]} with at least one punch")

    result = attendance_service.upsert_biometric_punches(
        db, org_id=org.id, device_code=device.device_code, punches=punches
    )
    device.last_sync_at = datetime.now(timezone.utc)
    db.add(device)
    return {"status": "ok", **result}
