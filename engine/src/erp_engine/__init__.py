"""Authoritative financial calculation engine.

Pure Python, no I/O, no database, no clock, no randomness (TECHNICAL_BLUEPRINT.md §6).
Calculation modules are implemented from milestone M1; this package currently only
establishes the package boundary and engine version.
"""

from erp_engine.version import ENGINE_VERSION

__all__ = ["ENGINE_VERSION"]
