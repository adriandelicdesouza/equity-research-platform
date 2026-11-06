import pytest
from erp_backend.auth.passwords import hash_password, needs_rehash, verify_password
from erp_backend.auth.ratelimit import SlidingWindowLimiter


def test_hash_roundtrip_and_argon2id() -> None:
    h = hash_password("a-sufficiently-long-pw")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "a-sufficiently-long-pw")
    assert not verify_password(h, "wrong-password-value")
    assert not needs_rehash(h)


def test_unknown_user_never_verifies() -> None:
    assert not verify_password(None, "timing-equalization-only")


def test_short_password_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("short")


def test_limiter_blocks_and_resets() -> None:
    lim = SlidingWindowLimiter(2, 60)
    lim.record_failure("k")
    assert not lim.is_blocked("k")
    lim.record_failure("k")
    assert lim.is_blocked("k")
    lim.reset("k")
    assert not lim.is_blocked("k")
