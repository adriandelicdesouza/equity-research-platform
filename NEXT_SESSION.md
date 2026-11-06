# Next Session Handoff

**Updated:** 2026-11-06, end of Session 2. History: `SESSION_LOG.md`.

## State

- **Architecture Revision 2: APPROVED** (2026-11-06). Implementation authorized; follow the roadmap in Blueprint §14.1.
- **Branch `m0-foundation`:** M0 implementation committed there. `main` = docs checkpoint `4855614`.
- **Dev stack `erp-dev` may still be running** (`make ps-dev`). It uses about 0.2 GB idle and is bound to loopback only (8091, 8444, 5433).
- **Dev admin login:** user `admin`, password in `secrets/dev/initial_admin_password` (git-ignored).

## Verify at startup

```bash
git status --short          # expect clean
git log --oneline -3        # M0 commit on m0-foundation, then 4855614, a7b6556
make ps-dev                 # web/api/db healthy (or `make up-dev`)
make check                  # all green
```

## Open items needing the owner

1. **Minecraft co-residency test.** Permission is still pending. **Ask before starting `minecraft.service`**, or ask the owner to start it. Then:
   - run `make measure-memory LABEL="minecraft co-residency"`;
   - run `infra/scripts/loadgen.py` as a background job;
   - record the results in `docs/operations/memory.md`.
2. **Merge `m0-foundation` into `main`.** Proposed once item 1 is done or explicitly deferred.
3. **SEC User-Agent contact string.** Needed before M2. `.env` only.
4. **Off-server backup destination.** Needed before production (M5 gate), **not** before development.

## Next steps

1. Close M0: items 1–2 above.
2. **Start M1** (Blueprint §14.1), in this order:
   1. `docs/methodology/dcf.md`: conventions, stub modes A/B, lease treatments, ERP/CRP, beta, dilution, output kinds. Write it first.
   2. Independent reference in `engine/tests/reference/`: `Fraction`/`mpmath`, backward recursion, economic-profit identity. It must not import `erp_engine` (add an import-linter contract). See Blueprint §13.1.1.
   3. `erp_engine` modules: `core` (Decimal context, periods, series, trace, registry, validation), `outputs`, forecast, `dcf` (fcff/fcfe/discounting/stub/terminal/leases), `bridge`, `dilution`, `wacc`.
   4. Policy-guard tests (Blueprint §13.1), and at least 3 hand-worked benchmarks.
3. Update `SESSION_LOG.md` and this file at the M1 checkpoint.

## Notes

- Run long jobs (load tests, big builds) with background execution. A long foreground command was killed once.
- Use `.tools/bin/uv run --all-packages …`, or the `make` targets. Plain `uv run` removes the workspace packages.
- Constraints remain in force:
  - no changes to unrelated services, Cloudflare, Tailscale or the firewall;
  - no `sudo`; no AI features; no portfolio features; no paid services.
