from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request

from .config import get_settings

# Argon2id is the default in argon2-cffi. Parameters target ~50-100 ms on a
# modern server and are stored inside each hash, allowing rehash-on-login.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def password_problems(password: str) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.islower() for c in password):
        problems.append("must contain a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("must contain an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("must contain a digit")
    return problems


def generate_password(length: int = 24) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*-_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_problems(value):
            return value


def new_token() -> str:
    """Opaque bearer/session token. Only the hash is persisted."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalise to aware UTC for comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def client_ip(request: Request) -> str | None:
    settings = get_settings()
    if settings.behind_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    return request.client.host[:64] if request.client else None


# ---------------------------------------------------------------------------
# TOTP (Time-based One-Time Password) per RFC 6238
# ---------------------------------------------------------------------------

def _base32_decode(secret: str) -> bytes:
    """Decode a base32-encoded secret key."""
    return base64.b32decode(secret.upper())


def compute_totp(secret: str, interval: int = 30, digits: int = 6) -> str:
    """Compute a TOTP value per RFC 6238.

    Args:
        secret: Base32-encoded secret key
        interval: Time step in seconds (default 30)
        digits: Number of digits in the OTP (default 6)

    Returns:
        A string of <digits> numeric characters.
    """
    secret_bytes = _base32_decode(secret)
    steps = int(time.time()) // interval
    counter = steps.to_bytes(8, byteorder="big")
    hmac_sha1 = hmac.new(secret_bytes, counter, hashlib.sha1).digest()
    offset = hmac_sha1[-1] & 0xF
    code = hmac_sha1[offset:offset + 4]
    code_int = struct.unpack(">I", code)[0]
    code_int = code_int & 0x7FFFFFFF
    code_int = code_int % (10 ** digits)
    return str(code_int).zfill(digits)


def compute_hotp(secret: str, counter: int, digits: int = 6) -> str:
    """Compute an HOTP value per RFC 4226 (counter-based OTP).

    Args:
        secret: Base32-encoded secret key
        counter: The counter value (incremented each use)
        digits: Number of digits in the OTP (default 6)

    Returns:
        A string of <digits> numeric characters.
    """
    secret_bytes = _base32_decode(secret)
    counter_bytes = counter.to_bytes(8, byteorder="big")
    hmac_sha1 = hmac.new(secret_bytes, counter_bytes, hashlib.sha1).digest()
    offset = hmac_sha1[-1] & 0xF
    code = hmac_sha1[offset:offset + 4]
    code_int = struct.unpack(">I", code)[0]
    code_int = code_int & 0x7FFFFFFF
    code_int = code_int % (10 ** digits)
    return str(code_int).zfill(digits)


def generate_backup_codes(count: int = 8, digits: int = 8) -> List[str]:
    """Generate a list of one-time backup codes for account recovery.

    Args:
        count: Number of backup codes to generate (default 8)
        digits: Number of digits per code (default 8)

    Returns:
        A list of random digit strings.
    """
    codes = []
    alphabet = "0123456789"
    for _ in range(count):
        code = "".join(secrets.choice(alphabet) for _ in range(digits))
        codes.append(code)
    return codes


# ---------------------------------------------------------------------------
# MFA Enforcement
# ---------------------------------------------------------------------------

def enforce_mfa(user) -> bool:
    """Check if MFA is enforced for this user.

    Returns True if MFA is required for this user's next login.
    - Admins and HR are always exempt.
    - Users with mfa_enabled=true must complete MFA on next login.
    - Users without mfa_enabled are exempt unless enforced at org level.
    """
    from .models import User as UserModel

    # Admins and HR are always exempt from MFA
    from .domain import Role
    if user.role in {Role.ADMIN, Role.HR}:
        return False

    # If user has mfa_enabled=true, MFA is required
    if user.mfa_enabled:
        return True

    return False


def verify_mfa_token(user, token: str) -> bool:
    """Verify an MFA TOTP token for the given user.

    Args:
        user: The User object
        token: The 6-digit TOTP token string

    Returns:
        True if the token is valid, False otherwise.
    """
    from datetime import timezone as dt_timezone

    if not user.mfa_secret:
        return False

    from . import compute_totp
    expected = compute_totp(user.mfa_secret, interval=30, digits=6)
    return expected == token


def generate_mfa_backup_code(user, used: bool = True) -> str | None:
    """Generate or rotate a single backup code for the user.

    Args:
        user: The User object
        used: If True, mark the used code as consumed (remove from list).
              If False, just return a new code without modifying the list.

    Returns:
        A backup code string, or None if no codes available.
    """
    import json

    codes = []
    if user.mfa_backup_codes:
        codes = json.loads(user.mfa_backup_codes)

    if not codes:
        return None

    if used:
        # Remove the first code from the list
        codes = codes[1:]
        user.mfa_backup_codes = json.dumps(codes)
    else:
        # Add a new code at the beginning
        from . import generate_backup_codes as _generate
        new_code = _generate(count=1, digits=6)[0]
        codes.insert(0, new_code)
        user.mfa_backup_codes = json.dumps(codes)

    return codes[0]


# ---------------------------------------------------------------------------
# Exported symbols
# ---------------------------------------------------------------------------

__all__ = [
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "password_problems",
    "generate_password",
    "new_token",
    "hash_token",
    "constant_time_equals",
    "client_ip",
    "compute_totp",
    "compute_hotp",
    "generate_backup_codes",
    "enforce_mfa",
    "verify_mfa_token",
    "generate_mfa_backup_code",
]
