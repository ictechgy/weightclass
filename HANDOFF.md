# Handoff

_Last updated: 2026-08-06 by Codex_

## Goal

Maintain public, local `weightclass`: deterministically classify transient task
stdin and select or start one reviewed native Codex or Claude Code workflow.
Preserve source-vendor pinning, redacted fail-closed diagnostics, opaque
user-provided model labels, and the no-retention boundary.

## Repository state

- The current unreleased work adds P0/P0.5 role delegation plus the P1
  qualification foundation. The offline
  `wclass delegate route` command compiles strict Claude- or Codex-native
  planner/worker/reviewer policy
  offline. `wclass delegate run` can start exactly one reviewed user-supplied
  runtime after explicit trust confirmation and exact fingerprint
  acknowledgement. Opt-in qualified route/run consult only a package-owned
  registry and bind/verify an exact runtime artifact. That registry is empty:
  no Claude/Codex adapter runtime is bundled or qualified.
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

## Unreleased delegation P0/P0.5

- `src/weightclass/delegation_types.py`, `delegation_schema.py`, and
  `delegation_compile.py` isolate the new schema and pure compiler from native
  routing and V2 API routing.
- Protocol 1 selects exactly one workflow by source vendor and tier, fully
  inlines orchestrator, worker, reviewer, adapter, action, retention, stage,
  artifact, cleanup, output, capacity, and byte contracts, then emits canonical
  JSON with a descriptor-only reproducible fingerprint.
- Claude and Codex use the same role contract. Every selected role must equal
  the explicit source vendor and use native transport. Cross-boundary lists
  must be empty; model and effort labels remain opaque user configuration.
- `assurance` is only `declared_enforcement`. The offline manifest is not a
  handshake or enforcement proof, and P0 never claims semantic authorship.
- `src/weightclass/delegation_protocol.py` builds one bounded WCD1 frame before
  spawn. `delegation_runtime.py` verifies the exact path is currently a regular
  executable, starts it once with fixed protocol argv, handles partial and
  interrupted writes, inherits environment/stdout/stderr, and waits for the
  direct child.
- Confirmation and fingerprint mismatch exit before runtime or task access;
  runtime unavailability exits before task stdin is read. Post-spawn framing
  failure uses fingerprinted grace intervals and the direct-child sequence
  `close -> wait -> terminate -> wait -> kill -> reap`, then returns redacted
  exit `7`. Normal runtime deadline, descendants, role enforcement, review,
  integration, provider access, and output remain external-runtime duties.
- The 13 final RALPLAN contract objections and the P0.5/P1/P2 gates are recorded
  in `docs/delegation-roadmap.md`. The five-round RALPLAN run itself ended at
  `max_rounds`/`ITERATE`; the roadmap incorporates its mandatory repairs but
  must not be described as an approved consensus plan.
- `tests/test_delegation.py` covers offline/nonexistent runtime behavior,
  Claude/Codex parity, fingerprint reproduction and ordering independence,
  ambiguity, source-vendor pinning, crossed-boundary rejection, mode-specific
  primitives, integer validation, platform uniqueness, lexical paths, task
  privacy, and Python 3.10 deep-JSON failure redaction.
- `tests/test_delegation_runtime.py` and its test-only fake runtime cover exact
  frame/argv/task delivery, confirmation and acknowledgement precedence,
  unavailable-runtime precedence, invalid task, inherited output, one spawn,
  runtime nonzero mapping, partial/interrupted writes, EPIPE, and finite
  direct-child reap. The fake is not a production adapter or qualification.

## Unreleased delegation P1 foundation

- `src/weightclass/delegation_qualification.py` defines a strict task-free
  evidence schema and a bounded package qualification registry. A record binds
  exact executable SHA-256 and size, runtime build ID, normalized platform,
  protocol, suite revision, adapter ID, vendor family, all 54
  role/category/action/mode observations, and all required scenario results.
- `src/weightclass/delegation_qualifications.json` is package data and contains
  zero records. Production has no CLI, environment, or user-path registry
  override. `delegate route/run --require-qualified-runtime` therefore
  currently fail closed with exit `3` for every adapter.
- A matching record changes only `run_requirement` and the reproducible route
  fingerprint; `assurance` remains `declared_enforcement` because offline route
  does not inspect the current runtime path.
- Qualified run opens the executable without following a final symlink, checks
  regular/executable state, exact bounded size and SHA-256, and concurrent
  metadata stability before reading task stdin. A mismatch returns redacted
  `executor_unavailable`. Spawn remains path-based, so hash-to-spawn replacement
  is a documented residual race.
- `wclass delegate qualification-candidate` validates complete task-free
  evidence and hashes one local executable, but outputs only an untrusted review
  candidate. It never updates the package registry and cannot establish that
  evidence was independently collected.
- `tests/test_delegation_qualification.py` covers the 54-cell matrix, required
  scenarios, unknown/incomplete/duplicate/failed input, exact evidence and
  artifact digests, record ambiguity, build selection, fingerprint rebinding,
  candidate CLI stdin independence, and changed/non-executable artifacts.
  Runtime integration tests confirm an empty registry and changed artifact both
  stop before task access.
- `src/weightclass/delegation_conformance.py` is a maintainer-only evidence
  runner, invoked with `python -m weightclass.delegation_conformance`. It owns a
  separately declared copy of the 54 permission cases and 13 scenarios, creates
  one private temporary workspace per case, and invokes an external reviewed
  driver with fixed protocol argv. Driver stdin/stdout, case deadline, output
  size, response ID, exit status, and same-process-group cleanup are bounded and
  fail closed; SIGINT cleans the active group and returns redacted exit `130`.
  Evidence schema 2 binds the runtime size/SHA-256 observed before the suite;
  the runner rechecks it after all cases and candidate construction rechecks the
  current bytes. The runner reads no task stdin and never edits the registry.
- The runner is not an attestation mechanism. Drivers inherit the environment
  and own runtime/vendor authentication, network, quota, billing, and external
  observation. A driver can lie or escape its process group. No real Claude or
  Codex conformance driver is shipped; the test fixture exercises only runner
  defenses and must never support a package qualification record.
- `tests/test_delegation_conformance.py` uses real subprocesses to cover all-pass
  candidate compatibility, explicit failure, case-ID spoofing, oversized valid
  JSON, fixed case timeout, same-process-group leakage detection, cleanup, and
  stdin independence. The package registry remains empty in the success test.
- `tests/fixtures/delegation_claim_map_v3.json` is a test-only executable claim
  inventory, not evidence. `tests/test_delegation_claim_map_v3.py` reconciles
  its 54 permission and 13 scenario IDs with both production catalogs, enforces
  bounded identifier-only rows, keeps every claim blocked behind
  `no-independent-oracle-v1`, and proves the file is rejected as both v2
  evidence and registry input. The ownership and no-go rules are documented in
  `docs/delegation-qualification-oracles.md`; no v3 production schema or runner
  is implemented.

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

- The P1 qualification, conformance-runner, and blocked claim-map foundation
  passed all 226 tests
  under Python 3.10.20 and Python
  3.14.6 with `ResourceWarning` promoted to an error. Both interpreters passed
  `compileall`; Ruff check/format, strict mypy, and `git diff --check` passed.
  An offline sdist/wheel build included the qualification module, empty package
  registry, and typing marker. The claim map remained outside the wheel, while
  its validator and JSON fixture were present together in the sdist and passed
  there. A clean no-index wheel install loaded the empty registry and exposed
  the candidate CLI. Build artifacts remained under a temporary directory.
- The unreleased delegation P0/P0.5 passed all 200 tests under Python 3.10.20
  and Python 3.14.6 with `ResourceWarning` promoted to an error. Both
  interpreters passed `compileall`; Ruff check/format, strict mypy, and
  `git diff --check` passed. Ruff and mypy were downloaded from PyPI only for
  local verification after user approval; no repository or task data was sent
  to a provider runtime.
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
- Qualification hashes only the external runtime artifact and task-free
  conformance evidence. It never hashes or receives runtime task content.
- Public evaluation data is regression-only and must not be used to approve a
  semantic candidate. Keep the Phase 4 decision no-go without independent
  predeclared evidence.

## Next safe action

1. Do not advertise real Claude/Codex delegation from P0.5. It proves only the
   weightclass-to-runtime boundary; a user-supplied runtime can lie with exit
   zero, leave descendants, or ignore the descriptor.
2. Add a narrowly worded v2 indistinguishability regression, then design a
   non-public synthetic probe-protocol kernel using separate self-test IDs and
   only runner-direct verdicts. Runtime event telemetry is not independent
   evidence, and path execution remains `TOCTOU-UNRESOLVED`. Do not implement a
   Claude/Codex driver or add a package record until an exact claim has an
   independent oracle, negative control, identity closure, and platform gate.
3. Keep Phase 4 at no-go unless independent predeclared evidence satisfies every
   quality, resource, privacy, and supply-chain gate.
4. Before the next release, address the upstream `actions/setup-python` Node 20
   deprecation warning by updating only to a reviewed pinned action commit.
5. Re-run the full local and macOS boundary gates for every routing behavior
   change; do not weaken the Codex-triage fail-closed contract without a newly
   documented all-tools-disabled vendor boundary.

## Resume prompt

Read `HANDOFF.md` and `AGENTS.md`. The published package and Homebrew formula are
0.4.0. The working tree contains unreleased delegation P0/P0.5 and an empty P1
qualification registry. P0.5 starts one explicitly trusted user runtime; P1
adds opt-in exact-artifact gates and a bounded maintainer evidence runner but
qualifies no Claude/Codex adapter and ships no real conformance driver. Do not
describe runner or candidate output as independent evidence or proven
delegation.
Preserve the Codex-triage fail-closed decision, native source-vendor routing,
transient-task boundary, and Phase 4 no-go. Re-run final verification after any
change; release, tag, and external publishing remain explicit actions.
