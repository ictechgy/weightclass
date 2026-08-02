# Claude Code Instructions

Read [`AGENTS.md`](AGENTS.md) first. It is the single source of truth for shared engineering, security, and public-repository rules.

Then read [`HANDOFF.md`](HANDOFF.md) before making changes. Do not duplicate shared rules in this file; update `AGENTS.md` when a project-wide rule changes.

Claude-specific reminders:

- Keep the current task scoped to the V1 boundary recorded in `HANDOFF.md`.
- Ask before using network access or touching credentials, vendor settings, or global configuration.
- Surface a proposed native command or configuration snippet for user review; do not treat it as authorization to execute it.
