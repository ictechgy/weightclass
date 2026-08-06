# Handoff

_Last updated: 2026-08-06 by Codex_

## Goal

Maintain public, local `weightclass`: deterministically classify transient task
stdin and select or start one reviewed native Codex or Claude Code workflow.
Preserve source-vendor pinning, redacted fail-closed diagnostics, opaque
user-provided model labels, and the no-retention boundary.

## Repository state

- Branch: local `main` at `b764f83` plus an uncommitted 0.4.0 hardening change.
- Published PyPI and Homebrew version: 0.3.0.
- Source version prepared by this change: 0.4.0.
- Do not update `packaging/homebrew/weightclass.rb` until an authorized release
  produces the final artifact URL and SHA-256.
- Phase 4 local semantic-model adoption remains **no-go**. No independent fresh
  blind-corpus, resource, or supply-chain evidence has satisfied the gate.

## Current hardening change

### P0 — optional vendor triage boundary

- `src/weightclass/triage.py` enables only the Claude adapter. Its reviewed argv
  requests safe mode, no built-in tools, no MCP, no user/project/local setting
  sources, no session persistence, plan permission mode, and low effort.
- The child starts in an empty private working directory and a new POSIX session.
  Nonblocking stdin/stdout exchange is bounded; timeout, oversized output, and
  successful-leader cleanup terminate the captured process group before one
  final reap. Parent pipes and selectors are closed deterministically.
- The parser accepts only the complete decoded lowercase value `low`,
  `standard`, or `high`; prose, multiple tokens, uppercase, invalid UTF-8, and
  embedded NUL fail closed.
- Codex optional triage is unavailable with reason `no_no_tools_boundary`.
  Official Codex CLI documentation exposes read-only filesystem sandboxing but
  no contract that disables every built-in tool. Native Codex route/run support
  is unchanged.
- Claude managed policy remains a documented vendor-owned residual capability.
  `--ask-vendor` is a distinct opt-in disclosure and quota/billing event.

### P1 — deterministic input and classification contracts

- New `src/weightclass/json_input.py` is shared by native policy, workflow
  descriptor, and V2 policy loading. It opens once with nonblocking/close-on-exec
  intent, validates that opened descriptor as a regular file, caps raw input at
  262,144 bytes, requires strict UTF-8 and a top-level object, and rejects
  duplicate keys at every nesting depth with value-free errors.
- Native and V2 route/run validate static policy before reading task stdin.
  FIFOs and other special files fail promptly. Symlinks are accepted only when
  the opened object is regular; this does not eliminate review/run path TOCTOU.
- Classification policy version is 2. Narrow exploit/failure phrases retain
  `high.risk_floor`; broad domain vocabulary uses
  `high.complexity_signal`. Harmful-outcome patterns retain bounded distances
  across newlines. Duplicate-work qualifiers are order-independent, and the
  `multiple` token inside `multiple times` cannot qualify itself.

### P2 — invariants and release gates

- Main CLI dispatch has an explicit V2 route/run branch and otherwise returns
  redacted `invalid_input`; it no longer has an implicit V2 run fallthrough.
- Tests bind source-vendor/provider map coverage and confirm that explanation
  reason-only changes do not alter a reviewed native route fingerprint.
- CI and release workflows add blocking macOS Python 3.10/3.13 triage-process
  and JSON-input boundary jobs. Release verification compares source version,
  installed metadata, and `wclass --version`.
- `README.md`, `RELEASING.md`, `tests/eval/README.md`, and
  `docs/routing-roadmap.md` describe the new boundaries and pending 0.4.0 state.

## Verification evidence

- `PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests`
  — 177 tests passed.
- `python3 -m compileall -q src tests`, offline Ruff check/format, mypy, and
  `git diff --check` — passed.
- `uv build --offline` — built the 0.4.0 sdist and universal wheel.
- A clean temporary virtual environment installed the wheel with `--no-index`;
  `wclass --version`, installed metadata, wheel metadata, and byte-exact default
  classification output all reported 0.4.0/the expected result.
- `twine check --strict` was not run: `twine` is absent from the offline tool
  cache. No network installation was attempted. CI/release retains the strict
  Twine gate.

Reproduction commands:

```sh
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests
python3 -m compileall -q src tests
uvx --offline ruff check src tests
uvx --offline ruff format --check src tests
uvx --offline mypy
git diff --check
```

Build and install smoke artifacts were created under temporary `/tmp` paths;
they are outside the repository and are not release artifacts.

## Safety and compatibility decisions

- Never persist, log, echo, hash, or include runtime task content in diagnostics.
- Never access `.env`, authentication, keychain, credential, cookie, or shell
  profile files without explicit approval. None were accessed in this change.
- The router itself does no provider HTTP and owns no provider credentials.
  External CLIs own their authentication, network, billing, and output.
- Default `wclass classify` output remains byte-compatible; richer local reason
  metadata remains opt-in through `--explain` or posture-bearing route output.
- Route fingerprints bind the reviewed selection/policy inputs, not task text or
  reason metadata. Task hashes remain forbidden by the no-retention contract.
- Public evaluation data is regression-only and must not be used to approve a
  semantic candidate. Keep the Phase 4 decision no-go without independent
  predeclared evidence.

## Next safe action

1. Review the complete diff for value-bearing errors, task leakage, and workflow
   syntax; fix only demonstrated issues.
2. Commit/PR only when explicitly requested. Release/tag/publish and Homebrew
   formula changes require separate authorization and final artifact metadata.

## Resume prompt

Read `HANDOFF.md` and `AGENTS.md`, inspect the uncommitted 0.4.0 hardening diff,
and preserve the Codex-triage fail-closed decision, native source-vendor routing,
transient-task boundary, and Phase 4 no-go. Re-run final verification after any
change. Do not release or modify the 0.3.0 Homebrew formula without authorization.
