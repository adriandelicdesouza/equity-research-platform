"""Roles and permissions (MASTER_BRIEF §19, Blueprint §11.2).

Permissions are fixed strings in code; role → permission mapping is code as well, so it
is reviewed and tested. Roles themselves are rows in ``auth.roles`` (seeded by migration).
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    COMPANY_READ = "company:read"
    COMPANY_WRITE = "company:write"
    VALUATION_READ = "valuation:read"
    VALUATION_WRITE = "valuation:write"
    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    REPORT_GENERATE = "report:generate"
    AUDIT_READ = "audit:read"
    PROVIDER_CONFIGURE = "provider:configure"
    SYSTEM_CONFIGURE = "system:configure"
    DATA_MANAGE = "data:manage"
    USER_MANAGE = "user:manage"


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    REVIEWER = "reviewer"


_READ = {
    Permission.COMPANY_READ,
    Permission.VALUATION_READ,
    Permission.DOCUMENT_READ,
    Permission.AUDIT_READ,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.ANALYST: frozenset(
        _READ
        | {
            Permission.COMPANY_WRITE,
            Permission.VALUATION_WRITE,
            Permission.DOCUMENT_WRITE,
            Permission.REPORT_GENERATE,
        }
    ),
    Role.REVIEWER: frozenset(_READ),
}

ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.ADMIN: "Full system access, user management, provider and system configuration.",
    Role.ANALYST: "Create and edit research and valuations, manage documents, generate reports.",
    Role.REVIEWER: "Read-only: view research, inspect models, calculations and outputs.",
}


def permissions_for(role_codes: frozenset[str]) -> frozenset[Permission]:
    perms: set[Permission] = set()
    for code in role_codes:
        try:
            perms |= ROLE_PERMISSIONS[Role(code)]
        except ValueError:
            continue  # unknown role codes grant nothing
    return frozenset(perms)
