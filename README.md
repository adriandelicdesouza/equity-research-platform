# Equity Research Platform

A self-hosted platform for institutional-grade equity research and valuation. Scope and principles are in `MASTER_BRIEF.md`.

**Current status:** Release 1, milestone **M0 Foundation** (in progress on branch `m0-foundation`). No valuation functionality exists yet. The DCF engine starts in M1.

| Document | Purpose |
|---|---|
| `MASTER_BRIEF.md` | Product specification (authoritative) |
| `TECHNICAL_BLUEPRINT.md` | Approved architecture (Revision 2) |
| `DECISIONS.md` | Architectural decisions and trade-offs |
| `KNOWN_LIMITATIONS.md` | Data coverage, provider, methodological and operational limitations |
| `docs/DEVELOPMENT.md` | Setup, commands, tests |
| `docs/operations/memory.md` | Measured memory vs. estimates |
| `NEXT_SESSION.md` / `SESSION_LOG.md` | Session handoff and history |

Quick start (dev): `make setup && make up-dev && make create-admin-dev`, then open `http://localhost:8091`.
