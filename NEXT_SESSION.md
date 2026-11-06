# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3 (after the public-release remediation). Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`.

## State at close (verified)

- **Branches:**
  - **`main`** @ `e3c17bc`: M0 + M1 (valuation engine), merged.
  - **`m2-ingestion`** @ `92e31ed` (current branch): M2 ingestion foundation + **SEC adapter (complete, do not rebuild)**. Two commits ahead of `main`. **Not merged.**
- **History rewritten twice (owner-approved, D47).** Author/committer is `Adrian Delic de Souza <contact@delicsouza.com>`, and infrastructure details are redacted (D48). All hashes changed; the mapping is in `SESSION_LOG.md`. Backups (timestamped `20261106T141410Z` and `20261106T142726Z`) are kept outside the repository.
- **Committed:** the Session 3 handoff updates, `README.md`, the new `docs/CONFIGURATION.md` and the game-server wording generalization are committed on `m2-ingestion`. Working tree clean.
- **Remote:** `origin` = `github.com/adriandelicdesouza/equity-research-platform` (public, **empty**). **Nothing has been pushed** (P12).
- **Dev stack stopped** (`make down-dev`). Data is in `data/dev/`: DB at Alembic `0004`, 6 SEC companies, 88,160 facts.
- **SEC User-Agent:** in git-ignored `.env.dev`, worker container only. **Never print, log or commit it.**

## Rules for this session (owner instructions)

- Keep all M2 work on `m2-ingestion`. **Do not merge, push or rewrite Git history** without explicit approval.
- Don't change Git identity settings (the repository-local `user.email` is already `contact@delicsouza.com`).
- **Keep home-server infrastructure details out of every tracked file (D48).** Before any commit, run the `FORBIDDEN` patterns from the backup `sanitize.py`.
- **Ask for explicit approval before the first live request to each new provider** (P9).
- Don't expose credentials or the SEC User-Agent email.
- The off-server backup (P2) and the game-server co-residency test (P4) stay **deferred to M5**. Never start the game server without approval.
- Run the complete `make check` at each milestone, and update `SESSION_LOG.md` / `NEXT_SESSION.md` as work progresses.

## Start-up sequence

```bash
cd ~/equity-research-platform
git switch m2-ingestion && git status --short && git log --oneline -4   # expect 92e31ed on top
make up-dev            # run as a background job; takes several minutes to build
make check             # must be green before new work (backend tests need the dev DB)
docker exec erp-dev-worker-1 python -m erp_backend.cli sec-check-config   # expect "configured"
```

Then ask the owner about the open decisions below. Only the commit question matters before starting work.

## Remaining M2 work, in order (Blueprint §4–§5, §10.3, §14.1)

1. **SEC concept → standard statement mapping (v1).**
   - Versioned YAML in `mappings/` (us-gaap, ifrs-full): line-item code, ordered concept candidates, sign.
   - `ref.standard_line_items` and `ref.concept_mappings` loaded by migration.
   - Fallback is chosen per period (Blueprint §4.4, §5.2).
2. **Versioned financial datasets.**
   - `fin.fiscal_periods`, `fin.financial_datasets` (`selection_policy`, `facts_as_of`, `interim_coverage`, draft/locked), `fin.financial_values`.
   - Built from `ingestion.selection` (D11). Derived values are flagged (gross profit, EBITDA, Q4 = FY − 9M).
   - Rebuilding from archived facts must be byte-identical.
3. **Balance and consistency checks** (§5.3): balance sheet balances, subtotals, cash roll-forward, cross-statement and share checks. Stored in `fin.validation_results`.
4. **Data-conflict detection and handling** (§10.3, D13): materiality thresholds, resolve/override with reason, lock blocking, audit.
5. **Financial-business classification** (§7.6.1, D24): SIC plus XBRL evidence, suggested vs confirmed. **RY must classify as `bank`** (DCF blocked). Handle insurers, specialty lenders, REITs, asset managers/brokers and holdings. Verify the SIC lists against the SEC list. Fix the bank gap in interim-coverage templates (KNOWN_LIMITATIONS §4c).
6. **Yahoo Finance (unofficial chart endpoint)** (§4.2, §4.5, D15, D16):
   - prices, splits and dividends;
   - manual TSX listing entry with symbol validation;
   - adjusted-close verification.
   **Ask approval before the first live request.**
7. **US Treasury** par yield curve (§4.2). **Ask approval first.**
8. **Bank of Canada** Valet: GoC yields and FX (§4.2). **Ask approval first.**
9. **Damodaran** reference datasets (ERP, CRP, betas, spreads) as `suggested` values (§4.2, §10.1). **Ask approval first.**
10. **Excel/CSV import** template and importer (§4.6), with a hash-stored original file.

**M2 exit criteria (Blueprint §14.1):**
- AAPL, MSFT, SHOP, CNI and CNQ load with ≥ 8–15 years and passing balance checks.
- RY is classified `bank`.
- A TSX-only company is entered manually, its symbol validated, and its financials imported via template.
- Dataset rebuild is byte-identical.
- `make check` green.

Then request owner review, and only after approval, the merge to `main`.

## Open owner decisions (`DECISIONS.md` → Pending)

- **P12 Publication:** push all four branches (`main`, `m2-ingestion`, `m0-foundation`, `m1-engine`) to `origin`. Only with explicit approval.
- **P10 License:** owner's choice. Don't pick one.
- **P11 Public vs internal docs:** a recommended structure was given in the Session 3 report. Don't move or exclude files without approval.
- **P9:** first live request per new provider (items 6–9).
- **Host security follow-up:** the owner holds a separate host-security checklist, kept outside this repository. It concerns host configuration, which is out of scope: don't change it.
- P2/P4 deferred to M5; P3/P5/P6 later.

## Useful commands

- **Tooling:** `make` targets, or `.tools/bin/uv run --all-packages …`. Plain `uv run` removes the workspace packages.
- **SEC:**
  - enqueue: `docker exec erp-dev-api-1 python -m erp_backend.cli sec-ingest --ticker XYZ`
  - rebuild without network: `docker exec erp-dev-worker-1 python -m erp_backend.cli sec-rebuild-from-archive`
- **Memory:** `make measure-memory LABEL="…"`. Results go in `docs/operations/memory.md`.
- **Engine golden outputs:** after any intended calculation change, bump `erp_engine/version.py`, then run `ERP_UPDATE_GOLDEN=1 make test-engine`.
