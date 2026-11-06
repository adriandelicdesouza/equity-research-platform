"""Provider registry and append-only fetch log (schema ``source``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from erp_backend.models.base import Base


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_limit_per_second: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    markets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ProviderFetch(Base):
    """One HTTP retrieval. The payload itself lives in the raw archive (content-addressed)."""

    __tablename__ = "provider_fetches"
    __table_args__ = {"schema": "source"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("source.providers.code"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    http_status: Mapped[int | None] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(128))
    payload_path: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    stored_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
