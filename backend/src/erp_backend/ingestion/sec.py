"""SEC EDGAR access gate (Blueprint §4.2; D42).

The SEC requires a User-Agent that identifies the requester with contact information.
The value is supplied by the owner via ``ERP_SEC_USER_AGENT`` (``.env`` only). There is
no default and no placeholder: without a valid value, no SEC client can be constructed,
so no request can be sent.
"""

from __future__ import annotations

import re

import httpx

from erp_backend.config import Settings
from erp_backend.ingestion.http import ProviderClient, ProviderNotConfiguredError

SEC_DATA_BASE = "https://data.sec.gov"
SEC_WWW_BASE = "https://www.sec.gov"
SEC_RATE_PER_SECOND = 5.0  # SEC fair-access limit is 10/s; stay well below

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
_RESERVED_DOMAINS = re.compile(
    r"@(?:[\w-]+\.)*(example\.(com|org|net)|[\w-]+\.(test|invalid|example|localhost))\b",
    re.IGNORECASE,
)
_PLACEHOLDER_WORDS = re.compile(
    r"placeholder|changeme|your[-_ .]?(name|email)|todo|xxx", re.IGNORECASE
)


def validate_user_agent(value: str | None) -> str:
    if value is None or not value.strip():
        raise ProviderNotConfiguredError(
            "SEC User-Agent is not configured. Set ERP_SEC_USER_AGENT in .env to the owner's "
            "contact string (e.g. 'Organisation Name contact@domain'). No default is provided."
        )
    text = value.strip()
    if not _EMAIL.search(text):
        raise ProviderNotConfiguredError("SEC User-Agent must include a contact email address.")
    if _RESERVED_DOMAINS.search(text) or _PLACEHOLDER_WORDS.search(text):
        raise ProviderNotConfiguredError(
            "SEC User-Agent looks like a placeholder; use real contact details."
        )
    return text


def sec_client(settings: Settings, transport: httpx.BaseTransport | None = None) -> ProviderClient:
    """The only way to obtain an SEC client; refuses without a valid owner-supplied UA."""
    return ProviderClient(
        provider_code="sec_edgar",
        user_agent=validate_user_agent(settings.sec_user_agent),
        per_second=SEC_RATE_PER_SECOND,
        transport=transport,
    )
