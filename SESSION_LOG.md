# Session Log

## Session 1 — 2026-11-06 (UTC ~05:10–05:31)

**Type:** Architecture and technical planning only.
**Architecture status at end of session:** **PROPOSED — NOT APPROVED.** No implementation has been authorized.
**Implementation performed:** None. No dependencies installed, no containers or services started, stopped or modified, no infrastructure changes, no commits.

### 1. Work performed

1. Read `CLAUDE.md` and `MASTER_BRIEF.md` in full.
2. Read-only environment inspection (results in `TECHNICAL_BLUEPRINT.md` §0):
   - **Host:** 4-core x86-64, 7.6 GiB RAM, 64 GB free on a 128 GB SSD, Ubuntu-based Linux.
   - **Tooling:** Docker 29.1.3 / Compose 2.40.3, Python 3.12.3, Node 24.18.
   - **Existing services:** inventoried (details kept outside this repository). No changes made.
   - **Game servers:** on-demand game servers are installed but were stopped (Minecraft uses up to ~3.5 GB when running).
   - **Existing code:** earlier personal prototypes reviewed. Their code won't be reused; their lessons will.
3. Live probes of free data providers (read-only GET requests):
   - **Working:** SEC `data.sec.gov`.
   - **SEC ticker file:** 403 without a contact User-Agent.
   - **Yahoo chart:** works for a known symbol, including TSX.
   - **Stooq:** now blocked by a JavaScript challenge.
   - **Rates and reference data:** Treasury, Bank of Canada, FRED and Damodaran reachable.
   - **SEC XBRL coverage:** verified for AAPL, SHOP, CNI, RY and CNQ.
   - Probe files are in the session scratchpad only, not in the repository.
4. Wrote `TECHNICAL_BLUEPRINT.md` (Revision 1) and presented the architecture and trade-offs.
5. Received an architecture review. Revised the blueprint (Revision 2) and created `DECISIONS.md` and `KNOWN_LIMITATIONS.md`.

### 2. Revision 2 — the 14 corrections

**Mandatory corrections**

| # | Correction | Where |
|---|---|---|
| 1 | **FCFE:** equity value comes directly from FCFE discounted at Ke. Cash and non-operating assets are no longer added, and the EV→equity bridge is used only by FCFF. Interest income on cash is added to FCFE net income. | Blueprint §7.3, §7.7; D19 |
| 2 | **Memory:** hard caps reduced to 1,584 MB total (web 48 / api 384 / worker 576 / db 576 MB). Expected-typical, expected-peak and measured columns are kept separate (measured = pending). A Minecraft co-residency test is required at M0 and M5. | §1.2, §12.3; D3 |
| 3 | **TSX discovery:** ticker and exchange are entered manually, and the provider symbol is derived by a documented rule. The Yahoo chart endpoint is used only to *validate* the symbol. Automated discovery is optional and off until verified. | §0.4, §0.5, §4.3; D15 |
| 4 | **WACC/CRP:** mature-market ERP and CRP are separate inputs. A basis switch and validation rules prevent counting country risk twice. The beta regression and adjustment method is documented. | §7.5; D21 |
| 5 | **Stub periods:** Mode A (prorated) and Mode B (actual partial period) are distinguished. Mode B, the default, captures seasonality and working-capital timing. Mode A's limitations are documented. Valuation is done at the balance-sheet date and rolled forward to the valuation date. | §7.4; D20 |

**Additional refinements**

| # | Refinement | Where |
|---|---|---|
| 6 | 6-K interim XBRL is treated as conditional, with per-company interim-coverage detection | §0.5, §4.4; D12 |
| 7 | Deterministic first-reported vs latest-reported selection (total ordering, `facts_as_of` cut-off, ambiguous duplicates) | §4.4; D11 |
| 8 | Lease treatment covers IFRS 16 and ASC 842, applied consistently across FCFF, bridge, WACC and multiples | §7.7.1; D22 |
| 9 | Financial-business classification: bank / insurer / specialty lender / REIT / asset manager-broker / investment holding / non-financial, each with its own policy | §7.6.1; D24 |
| 10 | Dilution instrument register with double-count safeguards; weighted-average shares are never the base | §7.7.2; D23 |
| 11 | Material data conflicts block dataset locking until resolved or overridden with a recorded reason | §10.3; D13 |
| 12 | Secure cookies on every access path: HTTPS on Tailscale/LAN through Caddy's internal CA, plain HTTP on localhost only | §11.1.1; D5 |
| 13 | An off-server backup (restic) and a restore drill from it are required before production acceptance | §12.4; D7 |
| 14 | The reference DCF is mathematically independent of the engine (different formulas, `Fraction`/`mpmath` arithmetic, import-linter rule) | §13.1.1; D26 |

### 3. Additional requirement — valuation output types

The engine has three output kinds: `ENTERPRISE_VALUE`, `EQUITY_VALUE` and `PER_SHARE_VALUE`. Every methodology declares which one it produces.

- **Conversions:** only `bridge/` converts EV→Equity, and only `dilution/` converts Equity→Per share.
- **Reconciliation:** methods are compared only at the same output kind, with the basis recorded (minority or control).
- **Mapping:** a methodology table covers DDM, residual income, comps, precedents, SOTP, NAV, LBO and reverse DCF.
- **Where:** Blueprint §6.6; D18.

### 4. Roadmap changes (Blueprint §14.1)

- **M0:** memory measurement added, plus Tailscale HTTPS and `__Host-` secure sessions.
- **M1:** FCFE moved here from M4. Also added: typed output kinds, stub modes A/B with roll-forward, lease treatments, the dilution register, ERP/CRP separation, beta methods, and the independent reference valuation. The methodology document is written first.
- **M2:** added deterministic fact selection, interim-coverage detection, conflict materiality and lock blocking, the financial-business classifier, manual TSX listing entry with symbol validation, and Damodaran reference datasets.
- **M3:** the end-to-end test now covers both a US company and a TSX-only company.
- **M5:** gates added for an off-server backup with a restore drill from it, and memory re-measurement with Minecraft running.
- **Release 1 acceptance criteria:** updated (items 2, 7, 9, 10 and the new item 11).

### 5. Other changes in Revision 2

- The default discount timing for the Gordon terminal value changed from mid-period to end-of-period (D27).
- SIC codes I couldn't verify were removed. The SIC lists are still to be checked against the SEC list in M2.
- Prod ports are now `127.0.0.1:8090` (HTTP) plus `<tailscale-ip>:8443` (HTTPS). Both were free at inspection.

### 6. Repository state at end of session

| Item | Value |
|---|---|
| Branch | `main` |
| Latest commit | `a7b6556986ff037c5eecabb68db56d78e0947997` — "chore: establish project specification and instructions" (2026-11-06 05:07:18 +0000) |
| Staged | nothing |
| Untracked (not committed, not staged) | `TECHNICAL_BLUEPRINT.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`, `SESSION_LOG.md`, `NEXT_SESSION.md` |
| Modified tracked files | none |

SHA-256 of the architecture documents at handoff:

```
749e8fe3faca40d50f92cce787d167099f72649532c9d0221917c6d30ec0b58a  TECHNICAL_BLUEPRINT.md
4af901a3f3333825b06002dcd332d595e538e777ceca9246b85e495c7a659b0c  DECISIONS.md
cb326c3c76f2c69b536c9aaa48ffc2977189c4a1afc8c5fbed366d3c2d072f9a  KNOWN_LIMITATIONS.md
```

## Session 2 — 2026-11-06 (UTC ~12:25–)

- Handoff verified: branch `main`, HEAD `a7b6556`, the three document hashes matched. Only difference: `CLAUDE.md` modified by the owner (new "Token Efficiency and Session Continuity" section).
- **Owner decisions:**
  - (a) **Architecture Revision 2 APPROVED**, and M0 authorized.
  - (b) SEC User-Agent deferred to M2.
  - (c) Commit all five docs plus `CLAUDE.md` to `main`.
- Status lines in the three architecture documents changed to APPROVED. Docs checkpoint committed. M0 work is on branch `m0-foundation`.
- **M0 Foundation implemented** on `m0-foundation`:
  - Workspace: uv workspace with `engine` and `backend`, resolved with `exclude-newer`; uv pinned in `.tools/`.
  - Backend: FastAPI app with Argon2id auth, `__Host-` server-side sessions, CSRF token plus Origin check, rate limiting, RBAC (3 roles, 12 permissions), append-only audit log, structured errors, request IDs.
  - Database: Alembic baseline `0001`; 3-role DB model (D31).
  - Frontend: React login/home shell.
  - Infra: Compose `erp-dev` (`web`/`api`/`db`/`migrate`); Caddy with HTTP on loopback and internal-CA HTTPS; Makefile; memory and load scripts; docs.
- **Checks:** `make check` green (ruff, eslint, mypy strict, tsc, 29 pytest, 2 vitest). Smoke and HTTPS end-to-end flows pass.
- **Fixes found during M0:**
  - IP-address HTTPS needed Caddy `default_sni` (D30).
  - API retained 279 MiB after load; `MALLOC_ARENA_MAX=2` brought it to 199 MiB (D33).
  - The empty editable install and `uv run` re-sync behaviour now need `--all-packages`.
- **Decisions added:** D29–D34. D29 deviates from Blueprint §12.2: data and secrets live in the git-ignored repo dirs (CLAUDE.md "work within repository").
- **Memory:** idle 184 MiB, load peak 365 MiB (sum of per-container peaks). Details in `docs/operations/memory.md`.
- **Still open in M0:**
  - Minecraft co-residency test (permission pending).
  - Tailscale-IP listener, verified at prod deploy (M5). Same mechanism tested on loopback.
- **Incident:** one foreground command was killed (exit 137) during the restart. Not OOM: the host had about 6 GB free and the API showed `OOMKilled=false`. It left `web` in "Created"; resolved by re-running `make up-dev`. Long runs now go to background jobs.
