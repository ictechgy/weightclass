# Handoff

_Last updated: 2026-08-02 KST by Codex_

## Goal

- Build a public local Subscription Agent Router (SAR) for choosing and rendering supported native Codex and Claude Code workflows while using subscriptions through the vendors' own tools.

## Current Status

- The project directory exists but is not yet a Git repository and contains no implementation code.
- Project instructions, this handoff, CodeRabbit preferences, and `.gitignore` were added for a future implementation session.
- Several `ralplan` attempts explored an automated launcher and then a decision-only renderer. No plan reached Critic approval yet.

## Important Context / Decisions

- The automated-launcher design grew into process supervision, retry, lease, and crash-recovery guarantees. Avoid reintroducing that scope without an explicit product decision.
- The safer candidate V1 is decision-only: accept trusted local policy plus a redacted descriptor, select a route deterministically, explain it, and render a user-reviewable native command or snippet. The user runs vendor tools manually.
- Do not handle credentials, task bodies, raw vendor output, subscription balances, or pricing data.
- Before coding, decide whether V1 writes any router-owned artifacts. A completely in-memory renderer is the smallest path; durable compilation requires a precise, platform-supported filesystem and publication contract.

## Key Files & State

- `AGENTS.md`: shared constraints and public-repository rules.
- `CLAUDE.md`: Claude Code entry point that references `AGENTS.md`.
- `.coderabbit.yaml`: conservative automated-review preferences.
- `.gitignore`: excludes secrets, local tool state, and common build output.
- Previous planning records are under `~/.local/state/codex-lterm-workflows/ralplan/`; they are reference material, not project files. The latest decision-only review still needs iteration.

## Verification

- Ran: project-directory inspection
  - Result: no existing source files or Git repository.
- No build, lint, or tests were run because implementation has not started.

## Blockers & Open Questions

- Choose the V1 persistence boundary: in-memory rendering only, or router-owned compiled bundles.
- If adding durable bundles, define supported operating systems and exact safe filesystem semantics before writing path-handling code.
- Create and initialize the Git repository in a future session if the user approves it.

## What Did Not Work / Avoid

- Do not claim an automated launcher is safe without fully specifying process ownership, recovery, concurrency, and vendor safety-profile enforcement.
- Do not let planning expand release tooling, cross-platform filesystem behavior, and vendor installation into V1 without a concrete acceptance requirement.

## Next Steps

1. Confirm the V1 persistence boundary with the user.
2. Initialize Git only with approval, then scaffold the smallest implementation and tests.
3. Add public documentation for threat boundaries, local data handling, and non-goals before any release.

## Resume Prompt

Open this repository at `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md` and `AGENTS.md`, then confirm the V1 persistence boundary before implementing anything.
