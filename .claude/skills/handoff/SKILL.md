---
name: handoff
description: Verify the repository state and update the session handoff (NEXT_SESSION.md, SESSION_LOG.md) at a milestone or before ending a session. Runs make check, inspects Git and ignored files, and commits only the handoff documentation when safe. Never pushes.
disable-model-invocation: true
argument-hint: "[optional note, e.g. 'end of session' or 'M3 phase 4 complete']"
---

# Session handoff

Produce an accurate, verified handoff. Follow CLAUDE.md (Definition of Done, Git Integrity); this
skill does not repeat it. Note from the invoker: $ARGUMENTS

## 1. Establish the facts

Run these and keep the output for the handoff:

```bash
git branch --show-current && git rev-parse --short HEAD
git status --short
git log --oneline -15
git rev-list --left-right --count @{upstream}...HEAD 2>/dev/null   # behind / ahead
git diff --stat && git diff --cached --stat
```

- Identify the milestone's commits: those since the HEAD recorded in the current
  `NEXT_SESSION.md`. Review them with `git log --stat <recorded>..HEAD`; read diffs only where a
  summary is unclear.
- Classify every uncommitted change as **this session's work** or **owner-owned** (anything you
  did not make in this session, e.g. owner edits to `CLAUDE.md`, stray local files). Do not stage
  or modify owner-owned changes; list them in the handoff.

## 2. Verify

1. `make check`. It needs the dev database: if it is down, ask before running `make up-dev`.
   Record the test counts (backend / engine / frontend) and any failures verbatim.
2. `make check-ignored` (also part of `make check`): no source file may be hidden by `.gitignore`.
3. If the backend schema changed this milestone, confirm `make api-types` leaves no diff.

If anything fails, **do not fix it silently inside the handoff**. Record the failure, the
incomplete state and the next step in the handoff, and report it to the owner.

## 3. Update the handoff documents

**`NEXT_SESSION.md`** — rewrite in place; actionable; under 600 words. Keep its existing section
layout. It must state:
- date, branch, HEAD (short hash), ahead/behind count vs upstream, push state;
- verified `make check` counts (or failures) and dev stack / DB state if relevant;
- owner-owned uncommitted changes;
- what is complete; open risks and limitations (point to `KNOWN_LIMITATIONS.md` sections);
- unresolved owner decisions (pending `P*` items in `DECISIONS.md`), kept distinct from approved
  decisions;
- the recommended next milestone with **exact acceptance criteria**;
- start-up commands with the expected test counts.

**`SESSION_LOG.md`** — append one chronological entry (`### Session N — <title> (<date>)`):
start state, stages with commit hashes, verification results, data or environment impact,
lessons. Do not duplicate earlier entries or restate `NEXT_SESSION.md`.

Then re-read your diff of both files: every hash, count and status must match what step 1–2
actually showed. Never record a check as passing that you did not run in this session.

## 4. Commit (only when safe)

Commit only if all hold:
- only `NEXT_SESSION.md` / `SESSION_LOG.md` (and documentation you changed this session) are
  staged — stage them by explicit path, never `git add -A` / `git add .`;
- `git diff --cached` has been reviewed, and checked against the private `FORBIDDEN` patterns
  (see the local Claude memory note on the pre-rewrite backups, D48);
- no owner-owned change is included.

Message: `docs(<scope>): update session handoff` plus the required attribution trailer.
Otherwise leave the changes uncommitted and say why.

**Never push.** Report whether a push is pending, and push only when the owner explicitly says
so.

## 5. Report

Reply briefly: branch, HEAD, ahead/behind, check results, what was committed, what is left
uncommitted (and whose it is), and the next milestone's acceptance criteria.
