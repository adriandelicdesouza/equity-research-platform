# Next Session Handoff

**Updated:** 2026-11-06, end of Session 3 (after the public-release remediation). Authoritative entry point (`CLAUDE.md`). Details: `SESSION_LOG.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`.

## State at close (verified)

- **Branches:**
  - **`main`** @ `9ef5594`: M0 + M1 (valuation engine), plus the LICENSE/README commit. Default branch on GitHub.
  - **`m2-ingestion`** (current branch): M2 ingestion foundation + **SEC adapter (complete, do not rebuild)**. **Not merged.** Merging will conflict on `README.md`: keep the `m2-ingestion` version.
  - Published state: see `SESSION_LOG.md` → "initial GitHub publication".
- **History rewritten twice (owner-approved, D47).** Author/committer is `Adrian Delic de Souza <contact@delicsouza.com>`, and infrastructure details are redacted (D48). All hashes changed; the mapping is in `SESSION_LOG.md`. Backups (timestamped `20261106T141410Z` and `20261106T142726Z`) are kept outside the repository.
- **Committed:** the Session 3 handoff updates, `README.md`, the new `docs/CONFIGURATION.md` and the game-server wording generalization are committed on `m2-ingestion`. Working tree clean.
- **Remote:** `origin` = `github.com/adriandelicdesouza/equity-research-platform` (public). All four branches published 2026-11-06 with upstream tracking (P12). Push new commits normally; never force-push.
- **Dev stack stopped** (`make down-dev`). Data is in `data/dev/`: DB at Alembic `0008`, 6 SEC companies, 88,160 facts. Per company, a verified format-3 **draft** with conflicts: MSFT v4, others v3; earlier versions discarded. One demonstration resolution (MSFT FY2016 equity). None locked: locking is the analyst's call, and MSFT/RY/AAPL have open material conflicts.
- **Unpushed commits on `m2-ingestion`:** mapping v1 (D49), versioned datasets (D50), integrity checks and derivations (D51), data conflicts (D52). Pushing needs owner approval.
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

1. ~~SEC concept → standard statement mapping (v1)~~ **done** (D49). `mappings/v1/` is frozen (hash pinned in `0005`); changes go into `v2/` with a new migration. Gaps per company: KNOWN_LIMITATIONS §4d.
2. ~~Versioned financial datasets~~ **done** (D50): `erp_backend.datasets` (service), `standardization/{periods,dataset}.py` (pure), migration `0006`, CLI `dataset-build|list|verify|lock|discard`. Limitations: KNOWN_LIMITATIONS §4e.
3. ~~Balance and consistency checks; derivations~~ **done** (D51): `standardization/{validation,derivations}.py`, `fin.validation_results`, mapping v2 (`CF.FCF`), migration `0007`. Real findings (splits, mixed vintages, RY EPS numerator): KNOWN_LIMITATIONS §4f.
4. ~~Data-conflict detection and handling~~ **done** (D52): `standardization/conflicts.py`, `fin.data_conflicts`, `fin.conflict_resolutions`, migration `0008`, CLI `conflict-list`, `conflict-resolve`, `dataset-rebuild`. Limitations: KNOWN_LIMITATIONS §4g.
5. **Financial-business classification (next)** (§7.6.1, D24): SIC plus XBRL evidence, suggested vs confirmed. **RY must classify as `bank`** (DCF blocked). Handle insurers, specialty lenders, REITs, asset managers/brokers and holdings. Verify the SIC lists against the SEC list. Fix the bank gap in interim-coverage templates (KNOWN_LIMITATIONS §4c).
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

- The repository is **public**: anything committed and pushed is published. Keep the D48 rules.
- **P9:** first live request per new provider (items 6–9).
- **Host security follow-up:** the owner holds a separate host-security checklist, kept outside this repository. It concerns host configuration, which is out of scope: don't change it.
- P2/P4 deferred to M5; P3/P5/P6 later.

## Useful commands

- **Tooling:** `make` targets, or `.tools/bin/uv run --all-packages …`. Plain `uv run` removes the workspace packages.
- **SEC:**
  - enqueue: `docker exec erp-dev-api-1 python -m erp_backend.cli sec-ingest --ticker XYZ`
  - rebuild without network: `docker exec erp-dev-worker-1 python -m erp_backend.cli sec-rebuild-from-archive`
- **Conflicts:** `conflict-list --id DATASET [--all]`, then `conflict-resolve --id DATASET --key KEY (--select FACT | --value V --source S | --override | --revoke) --reason "…"`. This rebuilds the draft.
- **Datasets:** `docker exec erp-dev-worker-1 python -m erp_backend.cli dataset-build --ticker AAPL`, then `dataset-verify --id …`, `dataset-lock --id … --reason …`.
- **Mapping coverage:** `docker exec erp-dev-worker-1 python -m erp_backend.cli sec-standardize-report --ticker AAPL [--all] [--as-of YYYY-MM-DD]`.
- **Memory:** `make measure-memory LABEL="…"`. Results go in `docs/operations/memory.md`.
- **Engine golden outputs:** after any intended calculation change, bump `erp_engine/version.py`, then run `ERP_UPDATE_GOLDEN=1 make test-engine`.
