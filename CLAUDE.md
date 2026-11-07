# Project Operating Instructions

MASTER_BRIEF.md is the authoritative product specification. Read it completely before starting
a new milestone or any task that changes product scope; for routine continuation, NEXT_SESSION.md
is the entry point and the relevant MASTER_BRIEF sections suffice.

## Development Rules

- Work exclusively within this repository.
- Follow the approved architecture and development priorities.
- Maintain modular, maintainable code.
- Use Python for all authoritative financial calculations.
- Never implement an Excel formula engine.
- Never introduce AI functionality without explicit approval.
- Never introduce portfolio management functionality.
- Never fabricate financial data or sources.
- Never introduce new dependencies without approval.
- Maintain comprehensive automated tests.
- Maintain technical documentation.
- Use Git checkpoints at meaningful milestones.

## Server Protection

This project runs on a home server hosting other services.

Do not:
- Modify unrelated Docker containers.
- Stop or restart existing services without approval.
- Modify Cloudflare Tunnel configuration without approval.
- Modify Tailscale configuration without approval.
- Modify firewall rules without approval.
- Change system-wide configuration without approval.
- Delete existing projects or user data.
- Perform destructive database operations without approval.
- Expose internal services publicly.

Do not use sudo without explicit approval.

## Security

- Never expose credentials or secrets.
- Never commit .env files.
- Never read or display SSH private keys.
- Never hardcode API credentials.
- Request approval before purchasing or activating paid services.

## Development Process

1. Inspect before modifying.
2. Document architectural decisions.
3. Implement in functional milestones.
4. Test every meaningful change.
5. Fix critical defects before proceeding.
6. Maintain reproducible builds.
7. Keep the application deployable.
8. Document completed functionality and known limitations.

## Definition of Done

A stage or milestone is done only when all of these hold, verified in the current session:

- Relevant targeted tests pass, and new behavior has tests.
- `make check` passes (includes `make check-ignored`).
- Documentation is updated (DECISIONS.md, KNOWN_LIMITATIONS.md, docs/ as applicable).
- Changes are committed locally, by explicit path (see Git Integrity).
- NEXT_SESSION.md and SESSION_LOG.md are updated (`/handoff`).
- Push only with explicit owner authorization for that milestone; never force-push, rebase,
  reset or rewrite history.

Never claim completion, or a passing check, without having run the verification. If a check
fails, report the failure and record the incomplete state in the handoff.

## Testing Conventions

Database (backend tests run on the disposable `erp_test` database; see `backend/tests/conftest.py`):
- Use the `db` fixture: one session per test, inside a transaction that is rolled back. Append-only
  tables (refused UPDATE/DELETE by trigger) rely on this rollback; never clean up with
  DELETE/TRUNCATE. Tests that need committed rows or the owner role use their own connection
  and transaction.
- Build a fresh `client`/`wf_client` per test, bound to that test's `db`. Do not share a
  TestClient or app across tests. Any new DB dependency (e.g. a read-only session) must be
  overridden to the same test session, or requests will not see the test's uncommitted rows.
- Compare JSONB values as parsed objects (`dict`/`list`), never as serialized text: JSONB does
  not preserve key order or whitespace.

SQLAlchemy:
- `session.flush()` before reading server-generated IDs, primary keys or defaults.
- After refactoring or moving code, verify imports and references (`make lint`, `make typecheck`).

Frontend:
- Mock `fetch`; wait for explicit conditions (`findBy*`, `waitFor`), never fixed sleeps or
  `setTimeout` delays. With fake timers, advance them explicitly.

## Formatting

- Run `make fmt` (ruff format + autofix of changed Python files only) before final
  verification, not between edits.
- After any formatting run, re-read the affected files before editing them again: earlier edit
  anchors may be stale.

## Git Integrity

- Before every commit, inspect `git status`, `git diff` and `git diff --cached`.
- Stage by explicit path; never `git add -A` or `git add .`. Exclude owner-owned changes
  (anything not made in the current session) and leave them for the owner.
- `.gitignore` data rules can match source directories. `make check-ignored` must pass; if a
  source path is ignored, track it rather than relying on local files.
- Check the staged diff against the private `FORBIDDEN` patterns (D48) before committing.

## Session Continuity and Context Discipline

Every session must be independently operable without previous conversation history.

- The repository is the source of truth. NEXT_SESSION.md is the entry point and the
  authoritative record of progress and outstanding work. Do not assume or reconstruct context
  from previous conversations.
- At startup, read NEXT_SESSION.md and verify it against Git status and the actual project state.
  If they conflict, investigate; verified repository state is authoritative for implementation.
- Read only what the current task needs: targeted files and sections, not whole documents. Do not
  reread the technical blueprint, the session log or completed implementation history unless
  necessary. Inspect relevant source files before modifying them.
- Do not repeat completed work or regenerate existing documentation unless verification
  identifies a specific problem.
- Preserve existing architectural decisions; distinguish proposed (`P*`) from approved (`D*`)
  decisions.
- Prefer one focused task, one validation cycle and concise reporting over repeated broad audits.
- Update the handoff after each completed milestone and before ending a session, using
  `/handoff`. NEXT_SESSION.md stays actionable and under about 600 words: branch, HEAD,
  completed work, verification results, risks, unresolved decisions and exact acceptance
  criteria for the next milestone. SESSION_LOG.md stays chronological, without duplication.

Never sacrifice correctness, security or test coverage to minimize context consumption.
