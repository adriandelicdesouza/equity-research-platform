"""Validation checks and reports (Blueprint §6.2, §7.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Check:
    code: str
    severity: Severity
    message: str
    blocks_save: bool = False
    details: tuple[tuple[str, str], ...] = ()


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        blocks_save: bool = False,
        **details: object,
    ) -> None:
        self.checks.append(
            Check(
                code,
                severity,
                message,
                blocks_save or severity is Severity.ERROR,
                tuple(sorted((k, str(v)) for k, v in details.items())),
            )
        )

    @property
    def has_errors(self) -> bool:
        return any(c.severity is Severity.ERROR for c in self.checks)

    @property
    def blocks_save(self) -> bool:
        return any(c.blocks_save for c in self.checks)

    def codes(self, severity: Severity | None = None) -> list[str]:
        return [c.code for c in self.checks if severity is None or c.severity is severity]
