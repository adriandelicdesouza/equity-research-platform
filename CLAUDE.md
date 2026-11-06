# Project Operating Instructions

Before performing any project task, read MASTER_BRIEF.md completely.

MASTER_BRIEF.md is the authoritative product specification.

## Development Rules

- Work exclusively within this repository.
- Follow the approved architecture and development priorities.
- Maintain modular, maintainable code.
- Use Python for all authoritative financial calculations.
- Never implement an Excel formula engine.
- Never introduce AI functionality without explicit approval.
- Never introduce portfolio management functionality.
- Never fabricate financial data or sources.
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

## Token Efficiency and Session Continuity

- Minimize unnecessary context loading and repeated file reading.
- Treat NEXT_SESSION.md as the authoritative entry point for resuming work.
- Read only the files required for the current task.
- Never reread the entire technical blueprint unless necessary.
- Maintain concise, incremental session documentation.
- Keep SESSION_LOG.md chronological and avoid duplicating existing information.
- Keep NEXT_SESSION.md actionable and ideally under 600 words.
- Update handoff documentation at major milestones and before ending a session.
- At startup, verify the handoff against Git status and actual project state.
- Do not repeat completed work or regenerate existing documentation unnecessarily.
- Preserve all existing architectural decisions and distinguish proposed decisions from approved decisions.
- Prefer one focused task, one validation cycle, and concise reporting over repeated broad audits.