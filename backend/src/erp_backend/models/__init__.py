"""ORM models. Import all model modules here so Alembic sees the full metadata."""

from erp_backend.models.audit import AuditEvent
from erp_backend.models.auth import Role, User, UserRole, UserSession
from erp_backend.models.base import Base
from erp_backend.models.fin import (
    ConflictResolution,
    DataConflict,
    FinancialDataset,
    FinancialValue,
    FiscalPeriodRow,
    ValidationResult,
)
from erp_backend.models.market import (
    Company,
    CompanyClassification,
    CompanyIdentifier,
    CorporateAction,
    PriceObservation,
    ProviderListing,
    Security,
)
from erp_backend.models.source import (
    Filing,
    Provider,
    ProviderFetch,
    ReportedFact,
    SecTicker,
)

__all__ = [
    "AuditEvent",
    "Base",
    "Company",
    "CompanyClassification",
    "CompanyIdentifier",
    "ConflictResolution",
    "CorporateAction",
    "DataConflict",
    "Filing",
    "FinancialDataset",
    "FinancialValue",
    "FiscalPeriodRow",
    "PriceObservation",
    "Provider",
    "ProviderFetch",
    "ProviderListing",
    "ReportedFact",
    "Role",
    "SecTicker",
    "Security",
    "User",
    "UserRole",
    "UserSession",
    "ValidationResult",
]
