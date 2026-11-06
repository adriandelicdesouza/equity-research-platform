"""Independent reference valuation (TECHNICAL_BLUEPRINT.md §13.1.1, decision D26).

Written from ``docs/methodology/dcf.md`` only. Independence rules, enforced where possible:

* Must not import ``erp_engine`` / ``erp_backend`` (import-linter contract in pyproject.toml).
* Must not use ``decimal``: arithmetic is ``fractions.Fraction`` (exact) and ``mpmath`` at
  50 significant digits for fractional exponents (also an import-linter contract).
* Different formulation from the engine: discounting by **backward recursion** over cash
  events (no cumulative discount factors), an **economic-profit identity** cross-check, and
  **brute-force** subset search for the treasury-stock method.
* Inputs are plain JSON fixtures (strings), not engine types.
"""
