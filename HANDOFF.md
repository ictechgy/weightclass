# Handoff

_Last updated: 2026-08-06 by Codex_

## Goal

Maintain public, local `weightclass`: deterministically classify transient task
stdin and select or start one reviewed native Codex or Claude Code workflow.
Preserve source-vendor pinning, redacted fail-closed diagnostics, opaque
user-provided model labels, and the no-retention boundary.

## Repository state

- PR #19 delivered routing hardening and version 0.4.0 at merge commit
  `786824cc74819e3bd8b254b615c3beb21f2fdd32`.
- Tag `v0.4.0` published PyPI version 0.4.0 through Release workflow run
  `31076506680`.
- PR #20 updated the canonical Homebrew formula at `7da697e`; tap PR #10
  published the identical formula at `36134c4` in `ictechgy/homebrew-tap`.
- Published PyPI, canonical formula, tap formula, and local Homebrew installation
  are all version 0.4.0.
- Phase 4 local semantic-model adoption remains **no-go**. No independent fresh
  blind-corpus, resource, or supply-chain evidence has satisfied the gate.

## Delivered hardening

### P0 — optional vendor triage boundary

- `src/weightclass/triage.py` enables only the Claude adapter. Its reviewed argv
  requests safe mode, no built-in tools, no MCP, no user/project/local setting
  sources, no session persistence, plan permission mode, and low effort.
- The child starts in an empty private working directory and a new POSIX session.
  Nonblocking stdin/stdout exchange is bounded; timeout, oversized output, and
  successful-leader cleanup terminate the captured process group before one
  final reap. Linux/current macOS use `waitid`; macOS Python 3.10 uses a
  non-reaping kqueue process-exit observer. Parent pipes, selectors, and kqueues
  are closed deterministically.
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
  `docs/routing-roadmap.md` describe the new boundaries and 0.4.0 delivery.

## Verification evidence

- Python 3.10.20 and the current local Python each passed all 177 tests with
  `ResourceWarning` promoted to an error.
- `compileall`, Ruff check/format, native and Linux-targeted mypy, workflow YAML,
  and `git diff --check` passed locally.
- Local release artifacts passed `twine check --strict`, clean no-index wheel
  installation, source/metadata/CLI version equality, and byte-exact default
  classification smoke tests.
- PR #19 and PR #20 each passed 14 CI checks. Merge commit `786824c` passed main
  CI run `31076433696`, including Python 3.10–3.13 and macOS 3.10/3.13 jobs.
- Release run `31076506680` passed tag/version, tests, lint, formatting, types,
  build, strict Twine metadata, macOS boundaries, and PyPI Trusted Publishing.
- A clean public-index environment installed `weightclass==0.4.0` and passed
  CLI/metadata/default-output checks. The public sdist SHA-256 is
  `46f2d6b76385fc9585542310497227b0eb329d2fed309382b9d15caaac6389c0`.
- `brew style ictechgy/tap/weightclass`, strict tap audit, source upgrade from
  0.3.0 to 0.4.0, `brew test`, and installed CLI smoke checks passed before tap
  PR #10 merged.

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

1. Keep Phase 4 at no-go unless independent predeclared evidence satisfies every
   quality, resource, privacy, and supply-chain gate.
2. Before the next release, address the upstream `actions/setup-python` Node 20
   deprecation warning by updating only to a reviewed pinned action commit.
3. Re-run the full local and macOS boundary gates for every routing behavior
   change; do not weaken the Codex-triage fail-closed contract without a newly
   documented all-tools-disabled vendor boundary.

## Resume prompt

Read `HANDOFF.md` and `AGENTS.md`. The published package and Homebrew formula are
0.4.0. Preserve the Codex-triage fail-closed decision, native source-vendor
routing, transient-task boundary, and Phase 4 no-go. Re-run final verification
after any change; release, tag, and external publishing remain explicit actions.
