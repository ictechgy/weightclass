# Handoff

_Last updated: 2026-08-02 KST by Codex_

## Goal

- Build a public local Subscription Agent Router (SAR) for choosing and rendering supported native Codex and Claude Code workflows while using subscriptions through the vendors' own tools.

## Current Status

- Git repository initialized; project guidance is committed as `4896cdf`.
- V1 persistence boundary is confirmed: SAR creates no router-owned artifacts or vendor configuration.
- A Python standard-library CLI classifies stdin task input deterministically, keeps explicit Codex/Claude source requests on the same vendor by default, and can run one selected command in the foreground without a shell.
- Reviewed policies can opt into mixed-vendor routing and declare opaque model labels per tier. Focused tests cover source-vendor filtering, mixed-vendor opt-in, model selection metadata, difficulty classification, routing, stdin execution, and redacted failure diagnostics.

## Important Context / Decisions

- The automated-launcher design grew into process supervision, retry, lease, and crash-recovery guarantees. Avoid reintroducing that scope without an explicit product decision.
- V1 accepts task bodies only as transient stdin: classify in memory, pass to one selected native child process, and never persist, log, echo, or place the task in diagnostics.
- V1 selects routes deterministically and permits `sar route` review before `sar run`. It does not capture or interpret raw vendor output.
- `--source-vendor codex|claude` identifies the calling vendor. Mixed-vendor routing is disabled by default and enabled only by `allow_mixed_vendors: true` in a reviewed policy.
- Model labels are policy-owned opaque values. SAR never probes entitlement or model availability.
- Do not handle credentials, subscription balances, or pricing data.
- V1 does not write router-owned artifacts. Durable compilation remains out of scope unless a future version defines platform support and a filesystem/publication contract.
- V1 runs one foreground command only. It does not retry, recover, background, monitor, or supervise vendor processes.

## Key Files & State

- `README.md`: local usage, default routing policy, and security boundary.
- `src/sar/`: deterministic classifier, selector, and foreground CLI runner.
- `tests/test_router.py`: focused behavior tests.
- `AGENTS.md`: shared constraints and public-repository rules.
- `CLAUDE.md`: Claude Code entry point that references `AGENTS.md`.
- `.coderabbit.yaml`: conservative automated-review preferences.
- `.gitignore`: excludes secrets, local tool state, and common build output.
- Previous planning records are under `~/.local/state/codex-lterm-workflows/ralplan/`; they are reference material, not project files. The latest decision-only review still needs iteration.

## Verification

- Ran: `PYTHONPATH=src python3 -m unittest tests/test_router.py`
  - Result: 18 tests passed.
- Ran: `PYTHONPATH=src python3 -m compileall -q src`
  - Result: source compiled without errors.

## Blockers & Open Questions

- Validate the built-in classification thresholds and keyword signals against real, non-sensitive task examples; expand only with an explicit regression test.
- Decide whether future versions need an optional model-aware triage provider. This must remain opt-in and must document task-data handling before implementation.
- Before adding durable bundles, define supported operating systems and exact safe filesystem semantics.

## What Did Not Work / Avoid

- Do not claim an automated launcher is safe without fully specifying process ownership, recovery, concurrency, and vendor safety-profile enforcement.
- Do not let planning expand release tooling, cross-platform filesystem behavior, and vendor installation into V1 without a concrete acceptance requirement.

## Next Steps

1. Add narrow vendor integration snippets that pass `--source-vendor` explicitly, without changing vendor-global configuration automatically.
2. Review default routes and model-labeled policies with intended local users, using `sar route` before any `sar run` invocation.
3. Add a release/install workflow only if a packaging requirement is approved; keep dependencies pinned and avoid vendor configuration changes.
4. Preserve the one-foreground-process, no-persistence boundary for future changes.

## Resume Prompt

Open this repository at `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`, `AGENTS.md`, and `README.md`, then preserve the V1 transient-task, no-persistence, one-foreground-process boundary while evolving classification or policy behavior only with focused tests.
