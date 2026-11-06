"""Password hashing with Argon2id.

Parameters follow the OWASP Password Storage Cheat Sheet minimum for Argon2id
(m=19 MiB, t=2, p=1), chosen to keep memory per login small on this server.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)

MIN_PASSWORD_LENGTH = 12

# A valid hash used to equalize timing when the username does not exist.
_DUMMY_HASH = _hasher.hash("timing-equalization-only")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification; returns False for unknown users (hash None)."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
