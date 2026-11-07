"""Valuation workflow: projects, assumptions, input resolution and immutable runs (M3 phase 5).

Design and approved decisions: ``docs/architecture/valuation-api.md`` (P16, resolved). The
authoritative calculations stay in ``erp_engine``; this package selects and validates inputs,
records provenance, calls the engine and persists the result.
"""
