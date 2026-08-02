# Handoff

_Last updated: 2026-08-02 KST by Codex_

## Goal

- Build a public local Subscription Agent Router (SAR) for choosing and rendering supported native Codex and Claude Code workflows while using subscriptions through the vendors' own tools.

## Current Status

- Git repository initialized; project guidance is committed as `4896cdf`.
- V1 persistence boundary is confirmed: in-memory selection and rendering only; SAR creates no router-owned artifacts.
- A Python standard-library CLI selects a policy route deterministically and renders a JSON command array without executing it.
- Focused tests cover ordered exact-match selection, successful rendering, unsupported routes, and redacted malformed-input failures.

## Important Context / Decisions

- The automated-launcher design grew into process supervision, retry, lease, and crash-recovery guarantees. Avoid reintroducing that scope without an explicit product decision.
- V1 is decision-only: accept trusted local policy plus a redacted descriptor, select a route deterministically, and render a user-reviewable native command array. The user runs vendor tools manually.
- Do not handle credentials, task bodies, raw vendor output, subscription balances, or pricing data.
- V1 does not write router-owned artifacts. Durable compilation remains out of scope unless a future version defines platform support and a filesystem/publication contract.

## Key Files & State

- `README.md`: local usage plus security boundary and V1 non-goals.
- `src/sar/`: deterministic selector and non-executing CLI renderer.
- `tests/test_router.py`: focused behavior tests.
- `AGENTS.md`: shared constraints and public-repository rules.
- `CLAUDE.md`: Claude Code entry point that references `AGENTS.md`.
- `.coderabbit.yaml`: conservative automated-review preferences.
- `.gitignore`: excludes secrets, local tool state, and common build output.
- Previous planning records are under `~/.local/state/codex-lterm-workflows/ralplan/`; they are reference material, not project files. The latest decision-only review still needs iteration.

## Verification

- Ran: `PYTHONPATH=src python3 -m unittest tests/test_router.py`
  - Result: 4 tests passed.
- Ran: `PYTHONPATH=src python3 -m compileall -q src`
  - Result: source compiled without errors.

## Blockers & Open Questions

- Define the supported workflow-policy schema beyond the current exact `vendor` and `workflow` fields only when a concrete user requirement needs it.
- Before adding durable bundles, define supported operating systems and exact safe filesystem semantics.

## What Did Not Work / Avoid

- Do not claim an automated launcher is safe without fully specifying process ownership, recovery, concurrency, and vendor safety-profile enforcement.
- Do not let planning expand release tooling, cross-platform filesystem behavior, and vendor installation into V1 without a concrete acceptance requirement.

## Next Steps

1. Review the policy schema and rendered commands with intended local users.
2. Add a release/install workflow only if a packaging requirement is approved; keep dependencies pinned and avoid vendor configuration changes.
3. Preserve the decision-only boundary for future changes.

## Resume Prompt

Open this repository at `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`, `AGENTS.md`, and `README.md`, then preserve the V1 decision-only, no-persistence boundary while evolving the policy schema only for concrete requirements.
