# Handoff

_Last updated: 2026-08-09 by Codex_

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
  runtime unavailability and Python-visible unsafe `SIGCHLD` contexts exit
  before task stdin is read. Module-owned `waitpid` preserves the exact child
  status and treats ECHILD, including hidden Darwin `SA_NOCLDWAIT`, as redacted
  post-spawn exit `7` instead of synthetic success. Framing failure uses
  fingerprinted grace intervals and the direct-child sequence `close -> wait ->
  terminate -> wait -> kill -> reap`; each signal is preceded by an
  authoritative zero-time wait. Normal runtime deadline, descendants, role
  enforcement, review, integration, provider access, and output remain
  external-runtime duties.
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
  direct-child reap, Python-visible SIGCHLD rejection, child-status-loss races,
  and actual Darwin `SA_NOCLDWAIT`. The fake is not a production adapter or
  qualification.

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
  Python-visible unsafe `SIGCHLD` state, Darwin `SA_NOCLDWAIT`, and a Linux
  behavioral probe that cannot authoritatively reap its disposable child are
  rejected immediately before spawn. Real `ECHILD` releases every numeric
  signal target; macOS Python 3.10 kqueue `ESRCH` from a normally exited,
  still-waitable leader instead preserves the zombie PGID anchor through group
  cleanup and one final authoritative `waitpid`. Evidence schema 2 binds the
  runtime size/SHA-256
  observed before the suite;
  the runner rechecks it after all cases and candidate construction rechecks the
  current bytes. The runner reads no task stdin and never edits the registry.
- The Linux child-status probe is behavioral, not atomic against a hostile
  concurrent native reaper. A later `ECHILD` fails the case and releases stale
  numeric signal targets instead of risking PID/PGID reuse.
- The runner is not an attestation mechanism. Drivers inherit the environment
  and own runtime/vendor authentication, network, quota, billing, and external
  observation. A driver can lie or escape its process group. No real Claude or
  Codex conformance driver is shipped; the test fixture exercises only runner
  defenses and must never support a package qualification record.
- `tests/test_delegation_conformance.py` uses real subprocesses to cover all-pass
  candidate compatibility, explicit failure, case-ID spoofing, oversized valid
  JSON, fixed case timeout, same-process-group leakage detection, cleanup, and
  stdin independence. Its v2 indistinguishability regression compares fixed
  runtime-invoked, runtime-skipped, marker-forged, and self-attested modes.
  Marker presence differs while complete evidence and candidates remain
  identical; this proves only v2 observer blindness, not authentic invocation
  or misconduct. The package registry remains empty in every success test.
- `tests/fixtures/delegation_claim_map_v3.json` is a test-only executable claim
  inventory, not evidence. `tests/test_delegation_claim_map_v3.py` reconciles
  its 54 permission and 13 scenario IDs with both production catalogs, enforces
  bounded identifier-only rows, keeps every claim blocked behind
  `no-independent-oracle-v1`, and proves the file is rejected as both v2
  evidence and registry input. The ownership and no-go rules are documented in
  `docs/delegation-qualification-oracles.md`; no v3 production schema or runner
  is implemented.

### Synthetic probe-protocol kernel (test-only)

- `tests/synthetic_probe_protocol.py`, `synthetic_probe_runner.py`, and their
  fixtures define a bounded non-public self-test kernel. Its
  `wcp-selftest/v1/*` identifiers are separate from all 67 qualification claim
  identifiers, its canonical manifests hardcode qualification and delegation
  false, and its output is not qualification-candidate or registry compatible.
- Decisive observations are limited to runner-selected argv, runner-observed
  direct-child PID/start, exit status, timeout, and runner-owned framed file
  descriptor traffic. Child payload assertions, stdout markers, telemetry, and
  self-attestation are explicitly untrusted and non-decisive. Malformed,
  truncated, duplicate, reordered, oversized, retained-writer, and hostile
  substitution cases fail closed with bounded value-free diagnostics.
- Containment assessment accepts direct provenance only from the runner-owned
  immutable result type; a plain child- or caller-supplied mapping cannot gain
  `runner-direct` provenance by copying field values. This is a test-process
  type boundary, not cryptographic attestation or protection from malicious
  code already executing inside that Python process.
- Path-based execution remains `TOCTOU-UNRESOLVED`: the probe does not establish
  that reviewed bytes were the bytes executed. It invokes only bounded local
  synthetic fixtures and is not a production driver or qualification runner.
- `tests/synthetic_descendant_containment.py` records tested Linux and Darwin
  `NO-GO` decisions. Process groups/sessions and observation of known processes
  do not establish an authoritative descendant boundary; Linux cgroup v2 was
  not verified as runner-owned and Darwin has no verified equivalent here.
  Platform labels, child cooperation, and child self-report never imply
  containment. No unsafe or runaway descendant was launched.
- Distribution gates require the source, wheel, and sdist production registries
  to have the exact canonical empty shape and canonical archive identity. Wheel
  and sdist component tries retain exact spelling alongside Unicode NFC plus
  case-folded identity, reject implicit file-parent and file/directory
  collisions in either order, and cap raw name bytes before normalization plus
  total path depth. The production registry itself must use its exact path. The
  sdist must have one root;
  backslash/NUL and other unsafe member names, links, devices, FIFOs, or other
  special members are rejected before extraction; synthetic assets must occupy
  their exact `tests/...` paths. Synthetic and candidate-like content are
  excluded from the wheel.
  CI/release action pins were not changed.

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
- Distribution verification accepts exactly one regular wheel and one regular
  sdist, binds their inventory and hashes across extracted tests, and applies
  bounded physical-tar checks before `tarfile` parsing or extraction. The
  source registry and both artifacts use bounded no-follow reads; wheel and
  sdist parsers consume only fingerprint-bound private snapshots. A bounded
  classic-ZIP preflight runs before `ZipFile`, rejects unsupported or ambiguous
  layouts, and verifies every stored/deflated payload's exact byte consumption,
  output size, and CRC. Release
  validation uses a fresh stdlib-only job, and publication consumes the same
  immutable artifact that job approved rather than re-uploading mutable paths.
- `README.md`, `RELEASING.md`, `tests/eval/README.md`, and
  `docs/routing-roadmap.md` describe the new boundaries and 0.4.0 delivery.

## Verification evidence

- On 2026-08-09, the current PR #22 worktree passed all 402 tests under Python
  3.14.6 in 62.495s and Python 3.10.20 in 59.053s with `ResourceWarning`
  promoted to an error. Ruff 0.16.1 check/format, mypy 2.3.0 strict checking,
  workflow YAML parsing, and `git diff --check` passed. Independent diff and
  completion-evidence reviews reported no actionable critical, high, or medium
  findings.
- A fresh offline exact wheel/sdist build passed the distribution-isolation
  gate and all 400 extracted-sdist tests under both interpreters: 69.153s on
  Python 3.14.6 and 60.496s on Python 3.10.20, with one platform skip each. The
  gate proves exact empty source/wheel/sdist registries; rejects duplicate,
  Unicode-normalized, case-folding, and file/implicit-directory archive
  identities; bounds physical tar and classic-ZIP headers and payloads before
  parsing; rejects unconsumed deflate bytes, checksum/size mismatches, ZIP64,
  data descriptors, encryption, overlaps, and excessive physical member counts;
  binds preflight, parsing, and extraction to one fingerprinted snapshot;
  excludes test-only and qualification-like wheel content; and confines
  required synthetic assets to their exact sdist `tests/` paths.
- Verification did not access a credential or secret-like file, invoke a
  weightclass-selected product/vendor runtime, persist runtime task content,
  publish a release, or deploy. External review tools received only bounded,
  redacted code targets; their provider authentication and network egress remain
  owned by those tools and are not a weightclass guarantee. No runtime task
  content was supplied to review. The synthetic kernel does not qualify a
  runtime or advertise delegation support; the packaged production registry
  remains empty.
- The P1 qualification, conformance-runner, and blocked claim-map foundation
  passed all 227 tests
  under Python 3.10.20 and Python
  3.14.6 with `ResourceWarning` promoted to an error. Both interpreters passed
  `compileall`; Ruff check/format, strict mypy, and `git diff --check` passed.
  An offline sdist/wheel build included the qualification module, empty package
  registry, and typing marker. Test fixtures and evaluation assets remained
  outside the wheel but were included through extension-bounded sdist rules;
  the extracted sdist passed all 227 tests without caches or bytecode. A clean
  no-index wheel install loaded the empty registry and exposed the candidate
  CLI. Build artifacts remained under a temporary directory.
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
PYTHONPATH=src python3.14 -W error::ResourceWarning -m unittest discover -s tests
PYTHONPATH=src python3.10 -W error::ResourceWarning -m unittest discover -s tests
python3.14 -m compileall -q src tests
python3.10 -m compileall -q src tests
uvx --offline ruff check src tests
uvx --offline ruff format --check src tests
uvx --offline mypy --strict src tests
git diff --check
```

The final offline distribution verification additionally used a fresh output
directory:

```sh
wclass_artifact_root=$(mktemp -d "${TMPDIR:-/tmp}/weightclass-pr22-final.XXXXXX")
mkdir "$wclass_artifact_root/source" "$wclass_artifact_root/dist"
git archive HEAD | tar -x -C "$wclass_artifact_root/source"
(
  cd "$wclass_artifact_root/source"
  uv --quiet build --offline --no-python-downloads --no-create-gitignore \
    --out-dir "$wclass_artifact_root/dist"
)
python3.14 tests/verify_distribution_isolation.py \
  --source "$wclass_artifact_root/source" \
  --dist-dir "$wclass_artifact_root/dist" \
  --run-sdist-tests
python3.10 tests/verify_distribution_isolation.py \
  --source "$wclass_artifact_root/source" \
  --dist-dir "$wclass_artifact_root/dist" \
  --run-sdist-tests
```

Build artifacts were created under a system temporary directory; they are
outside the repository and are not release artifacts.

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
2. Keep the completed synthetic probe kernel test-only. Its runner-direct facts
   do not close runtime identity or descendant containment and cannot support a
   qualification record. The next honest boundary is an independently reviewed
   design for one exact claim with an external oracle, hostile negative control,
   identity closure, and an authoritative per-platform containment gate. Do not
   implement a Claude/Codex driver, add a package record, or advertise
   delegation support before those prerequisites exist.
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
delegation. The working tree also contains a test-only synthetic probe kernel;
its runner-direct observations remain `TOCTOU-UNRESOLVED`, Linux and Darwin
descendant containment are tested `NO-GO`, the production registry is empty,
and no synthetic artifact ships in the wheel.
Preserve the Codex-triage fail-closed decision, native source-vendor routing,
transient-task boundary, and Phase 4 no-go. Re-run final verification after any
change; release, tag, and external publishing remain explicit actions.
