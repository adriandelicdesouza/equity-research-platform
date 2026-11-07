"""Valuation refusals: a business rule forbids the request now (mapped to ``409`` + code)."""

from __future__ import annotations

from typing import Any


class ValuationRefused(Exception):  # noqa: N818 - domain name, like the other refusals
    """Raised by valuation services; ``workflow.perform`` turns it into ``409 <code>``.

    ``details`` lists the affected keys or inputs so the UI can explain the refusal."""

    def __init__(
        self, code: str, message: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
