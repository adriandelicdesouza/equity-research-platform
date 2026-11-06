# Next Session Handoff

**Written:** 2026-11-06 (end of Session 1). See `SESSION_LOG.md` for what was done.

## Status — read first

- **Superseded (Session 2):** architecture Revision 2 was APPROVED on 2026-11-06 and M0 is in progress. This file will be rewritten at the end of Session 2.
- **No implementation has been authorized.** Until approval:
  - Do not install dependencies.
  - Do not create application code.
  - Do not create Docker resources.
  - Do not start any milestone (M0–M5).
- The three documents are **untracked and uncommitted** (with `SESSION_LOG.md` and this file). Do not commit, stage, discard or revert them without the user's instruction.

## Outstanding decisions (user's to make)

| # | Decision | Notes |
|---|---|---|
| a | **Architecture approval** | Approve, or request further changes to Revision 2 |
| b | **SEC User-Agent contact string** | Required by SEC (403 without it). Stored only in `.env`, never committed. Needed by M2 |
| c | **Whether to commit the three documents** | Plus whether to include `SESSION_LOG.md` / `NEXT_SESSION.md` |

## Other pending items (not blocking development start)

- **Minecraft startup permission is still pending.**
  - Minecraft (`minecraft.service`, currently disabled/stopped) is an existing service.
  - **Ask the user before starting it**, or ask them to start it, for the memory co-residency test at M0/M5.
  - Never start, stop or restart it without explicit permission.
- **Off-server backup destination:** required **before production acceptance (M5 gate), not before development.**
- **Later approvals:**
  - Cloudflare tunnel hostname + Access policy.
  - Optional LAN binding.
  - Optional `tailscale serve` instead of Caddy's internal CA.
  - Optional free-key providers.
  - Full list: `DECISIONS.md` → "Pending decisions".

## Exact next steps when the user returns

1. Re-read `CLAUDE.md` and `MASTER_BRIEF.md` (required by `CLAUDE.md` before any project task).
2. Verify state matches the handoff:
   - `git status --short` should show exactly the five untracked `.md` files.
   - `git log -1` should show `a7b6556`.
   - Check the SHA-256 hashes in `SESSION_LOG.md` §6 with `sha256sum TECHNICAL_BLUEPRINT.md DECISIONS.md KNOWN_LIMITATIONS.md`.
   - Report any difference before doing anything else.
3. Present the three outstanding decisions (a, b, c) and wait for answers.
4. If the architecture is **not** approved: revise the documents as requested, update `SESSION_LOG.md`, and stop for approval again.
5. If the architecture **is** approved:
   1. Change the status lines in `TECHNICAL_BLUEPRINT.md`, `DECISIONS.md` and `KNOWN_LIMITATIONS.md` from "proposed" to "approved", with the approval date.
   2. Commit the documents if the user decided so in (c). Use a feature branch if work beyond docs starts; `main` stays stable.
   3. Begin **M0 Foundation** (Blueprint §14.1):
      - repo layout, `uv`/npm lock files, `erp-dev` Compose project on `127.0.0.1:8091`;
      - Postgres 17 + Alembic baseline, Caddy, health checks;
      - auth with `__Host-` secure sessions, RBAC skeleton, audit table, `make check`;
      - memory measurement script.
   4. Record idle and synthetic-load memory in `docs/operations/memory.md`, next to the estimates (Blueprint §1.2).
   5. Before any Minecraft co-residency test, **ask for permission**.
   6. Put the SEC User-Agent (once provided) in `.env` only. Confirm `.env` is git-ignored (it is: `.gitignore` covers `.env` and `.env.*`).
   7. Then M1 (engine core + DCF): write `docs/methodology/dcf.md` first, then the independent reference (§13.1.1), then the engine.

## Constraints that remain in force

- Don't modify unrelated containers or services, or the Cloudflare, Tailscale or firewall configuration. No `sudo`.
- No AI functionality, no portfolio management, no paid services.
- Never read or display credential files (for example, tunnel credentials).
