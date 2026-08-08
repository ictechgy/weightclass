# Claude and Codex Delegation Roadmap

## Purpose

Add role-based routing without pretending that a model label proves who did
the work. A reviewed policy can assign a strong opaque model/effort profile to
planning and review and a separate opaque profile to delegated work. Claude
and Codex use the same contract, but protocol 1 keeps every role with the
explicit source vendor.

The public surface is isolated from native routing and V2 API routing:

```text
wclass delegate route  -> offline descriptor compiler
wclass delegate run    -> one trusted external runtime (P0.5, not P0)
wclass delegate qualification-candidate -> untrusted P1 record candidate
python -m weightclass.delegation_conformance -> maintainer evidence runner
```

The external runtime, not weightclass, owns role-process creation, provider
authentication, network and billing behavior, action enforcement, artifacts,
review, integration, output, and descendant cleanup. Existing native and V2
commands retain their validation order, fingerprints, argv, and error mapping.

## Protocol 1 role contract

The compiler selects exactly one workflow by exact `(source_vendor, tier)`
membership and fully inlines three profiles:

| Role | Model policy | Workspace | Commands | Responsibility |
| --- | --- | --- | --- | --- |
| Orchestrator | opaque strong profile | read only | deny | create the three required assignments |
| Worker | opaque delegated profile | category-specific | category-specific | implementation, tests, or documentation |
| Reviewer | opaque strong profile | read only | deny | approve the exact worker artifacts |
| Integrator | no model | approved writes only | approved argv only | mechanically apply and verify approved artifacts |

Claude-originated work requires Claude-family profiles and a Claude adapter.
Codex-originated work requires Codex-family profiles and a Codex adapter. Model
and effort strings are user-supplied opaque labels: weightclass does not rank
them, infer availability, or infer subscription cost or entitlement.

Protocol 1 has a deliberately narrow fixed graph:

```text
validated
-> planned
-> required_assignments_created
-> workers_completed
-> reviewer_approved
-> integration_completed
-> descendants_reaped
-> success
```

Only the three worker contexts may overlap. The maximum simultaneous role or
helper count is therefore three, not the largest individual declared ceiling.
The runtime must return nonzero for a skipped, duplicated, or out-of-order
transition.

## P0 input schema map

Every object has exactly the listed keys. Unknown keys, duplicate JSON keys,
wrong types, duplicate IDs, and out-of-bound collections are invalid input.

| Object | Exact keys |
| --- | --- |
| Policy | `schema_version`, `profiles`, `workflows` |
| Profile | `id`, `role`, `vendor_family`, `transport`, `model`, `effort`, `allowed_categories`, `global_role_process_limit` |
| Workflow | `id`, `eligible_source_vendors`, `eligible_tiers`, `adapter_id`, `profiles`, `assignments`, `integration`, `runtime_deadline_seconds`, `direct_child_cleanup`, `boundary_authorizations` |
| Profile references | `orchestrator`, `worker`, `reviewer` |
| Assignment | `category`, `execution`, `review`, `retention`, `integration` |
| Retention | `worker_context`, `artifacts`, `on_reviewer_rejection`, `after_integration` |
| Integration | `inputs`, `allowed_operations`, `verification_commands` |
| Direct-child cleanup | `grace_seconds`, `terminate_grace_seconds` |
| Boundary authorizations | `provider_pairs`, `recipient_pairs`, `billing_pairs`, `mixed_transport_pairs` |
| Pair entry | `from`, `to` |
| Manifest | `manifest_schema_version`, `runtime_protocol_versions`, `runtime_build_id`, `supported_platforms`, `adapters` |
| Platform | `os`, `architecture` |
| Adapter | `id`, `vendor_family`, `transports`, `global_role_process_limit`, `capabilities`, `enforcement_primitives` |
| Enforcement primitives | `workspace_read`, `workspace_write`, `command_execution`, `process_isolation` |
| Action primitive | `allow`, `deny` |
| Process-isolation primitive | `create`, `attribute` |

Protocol 1 requires exactly the `implementation`, `tests`, and `documentation`
assignments. `execution`, `review`, and `integration` are respectively
`must_delegate`, `required`, and `mechanical_runtime`. Boundary authorization
arrays must be empty. The required adapter capabilities and exact retention,
integration, label, identifier, platform, collection, and integer constraints
are executable constants and parser checks in
`src/weightclass/delegation_schema.py`; the compiler-owned action, stage,
artifact, output, and byte contracts are in
`src/weightclass/delegation_compile.py`.

## Resolved pre-implementation contract defects

The following decisions close the final Planner-Architect-Critic objections.

1. **Mode-specific enforcement.** Each workspace/command action declares
   separate `allow` and `deny` primitives. Process isolation separately
   declares context `create` and action `attribute` primitives. A single opaque
   action label is invalid.
2. **Offline assurance.** `delegate route` always emits
   `assurance: declared_enforcement`. It never says the current runtime path is
   qualified. P0 uses `run_requirement.kind: trusted_runtime_confirmation`.
   Opt-in P1 uses `run_requirement.kind: exact_artifact_conformance` to
   fingerprint a package-recorded qualification target, but the offline route
   still does not claim that the current path matches it.
3. **Finite direct-child cleanup.** The fingerprint binds two bounded grace
   intervals and the future run sequence `close -> wait -> terminate -> wait ->
   kill -> reap`. Weightclass will act only on its direct child and will never
   enumerate descendants. A defective trusted runtime can still orphan them;
   descendant leakage fails P1 conformance. Runtime execution also requires a
   main-thread, reviewed native `SIGCHLD` disposition before task input and
   again immediately before `Popen`. Darwin and reviewed 64-bit glibc Linux
   x86_64/AArch64 ABIs use the same fail-closed native `sigaction` gate as the
   conformance runner; unsupported libc, ABI, and POSIX platforms do not run.
   The caller must exclusively own direct-child status throughout the
   invocation. The spawn-adjacent observation and module-owned `waitpid`
   narrow stale-PID exposure and treat missing status as a redacted failure,
   but neither is atomic against hostile concurrent native mutation or a
   foreign `waitpid` between an ownership observation and later signaling or
   reaping.
4. **Stage-specific retention.** Worker contexts release after the worker
   stage. Runtime-owned artifacts remain available through review and
   integration and are destroyed on reviewer rejection or after integration.
   Weightclass never receives or retains them.
5. **Three byte representations.** `fingerprint_payload` is canonical JSON
   excluding only `route_fingerprint`. `review_descriptor` is canonical JSON
   including it. The future `runtime_descriptor` is byte-identical to the
   review descriptor, excluding the CLI's final display newline. Each is
   bounded to 262,144 bytes. With an 80,000-byte task, the complete WCD1 frame
   is at most 342,156 bytes.
6. **Deterministic platform selection.** P0 binds the normalized current host
   (`darwin|linux`, `aarch64|x86_64`) without accessing the runtime path.
   Duplicate manifest platforms are invalid; no exact compatible entry is an
   unsupported route. Windows remains unsupported.
7. **Output ownership.** P0.5 runtime stdout/stderr is inherited,
   uncaptured, unparsed, unredacted, and unbounded. Once runtime output is
   emitted, it is outside weightclass's no-retention guarantee. Terminal or log
   exhaustion is a trusted-runtime risk. Conformance evidence belongs to an
   independent harness rather than production stdout parsing.
8. **Named bounds.** Policy, manifest, descriptors, labels, paths, platforms,
   protocols, profiles, workflows, adapters, capabilities, boundary pairs,
   verification commands, argv entries/tokens, process limits, deadlines, and
   cleanup intervals have named bounds in `delegation_schema.py`,
   `delegation_compile.py`, or `delegation_qualification.py`. Every integer
   field rejects booleans, floats, and other non-integer JSON values.
9. **No handshake claim.** The manifest is a reviewed offline capability
   declaration. Protocol 1 has no live negotiation or authenticated
   self-attestation. P1 permits a package record only after independent
   exact-artifact qualification; candidate generation is not a handshake or
   qualification.
10. **Artifact integrity.** Runtime artifact IDs are unique per run and
    immutable across worker context, category, reviewer approval, and
    integration. Duplicate, missing, altered, cross-category, or unapproved
    artifacts must fail. These mechanisms stay runtime-owned and must not put
    task-derived data in weightclass diagnostics.
11. **Meaningful mandatory work.** Protocol 1 requires genuine implementation,
    tests, and documentation assignments. A runtime must fail when the task
    cannot support all three; dummy assignments and silent skipping are
    forbidden. A later protocol may define an explicit `not_applicable`
    transition.
12. **No retry-shaped metric.** Protocol 1 has no reviewer-requested replacement
    cycle, so its experiment has no rework metric. A later retry-capable state
    machine must define that metric before collecting it.
13. **Phase-specific verification.** Each phase runs only its newly available
    focused tests plus the invariant full compatibility suite, `compileall`,
    Ruff, mypy, and `git diff --check`. P0, P0.5, and P1 tests remain separated
    so a lower phase does not accidentally claim a higher-phase guarantee.

None of these statements proves semantic authorship. The strongest permitted
claim is that a qualified exact runtime artifact exhibited the specified
runtime-mediated stages, processes, attributions, permissions, and integration
behavior in the independent conformance suite.

## P0 — offline compiler

`wclass delegate route` takes a strict delegation policy, strict runtime
manifest, lexical absolute runtime path, source vendor, and tier. It:

- reads no task standard input;
- performs no network access;
- does not resolve, stat, open, hash, or execute the runtime path;
- rejects unknown keys, duplicate JSON keys, unsupported combinations, and
  ambiguous workflow matches;
- fully inlines the selected profiles and adapter;
- inserts the compiler-owned action map, stage graph, artifact rules, output
  boundary, cleanup contract, capacity, and byte contract;
- fingerprints only the self-contained descriptor, excluding its fingerprint;
- emits no policy/manifest path, task content, task hash, credentials, unused
  declaration, or hidden policy-order input.

Acceptance gate:

```sh
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest tests.test_delegation
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest \
  tests.test_router tests.test_v2
PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests
python3 -m compileall -q src tests
uvx --offline ruff check src tests
uvx --offline ruff format --check src tests
uvx --offline mypy
git diff --check
```

P0 rollback removes only the `delegate` parser branch and isolated delegation
modules. It migrates or persists no state.

## P0.5 — trusted same-vendor runtime

Status: implemented locally with a test-only fake runtime; no Claude/Codex
adapter runtime is bundled or qualified.

`delegate run` requires both
`--confirm-trusted-delegation-runtime` and the exact reviewed fingerprint, read
the bounded task only after those static gates, build the complete WCD1 frame
before spawn, and start exactly one foreground external runtime. The current
fake runtime covers exact argv/frame/task delivery, partial writes, interrupted
writes, `EPIPE`, direct-child cleanup/reap, invalid task, runtime nonzero,
inherited output, and pre-task gate precedence. Runtime-side conformance cases
such as reviewer rejection, premature success, deadline failure,
action-attribution failure, integration substitution, descendant leakage, and
malformed inbound frames remain P1 suite work because production weightclass
does not parse runtime stage evidence.

This phase remains `declared_enforcement`: weightclass observes only the direct
child's final exit or signal and cannot detect a dishonest zero exit.

## P1 — exact-artifact qualification

Status: the local trust registry, candidate schema, route binding, and run gate
are implemented; the package contains zero records and no adapter is qualified.

Every P1 object has exactly the listed keys:

| Object | Exact keys |
| --- | --- |
| Registry | `registry_schema_version`, `suite_revision`, `records` |
| Record | `record_schema_version`, `artifact_sha256`, `artifact_size_bytes`, `runtime_build_id`, `platform`, `protocol_version`, `suite_revision`, `adapter_id`, `vendor_family`, `conformance_evidence_sha256`, `result_matrix`, `scenario_results` |
| Evidence | `evidence_schema_version`, `artifact_sha256`, `artifact_size_bytes`, `suite_revision`, `runtime_build_id`, `platform`, `protocol_version`, `adapter_id`, `vendor_family`, `result_matrix`, `scenario_results` |
| Observation | `role`, `category`, `action`, `mode`, `passed` |
| Scenario result | `id`, `passed` |

`--require-qualified-runtime` makes route/run select exactly one package-owned
record by build ID, normalized host platform, protocol, adapter ID, and source
vendor. The route fingerprint additionally binds executable SHA-256 and size,
suite revision, and a digest over the complete normalized conformance evidence.
Production accepts no CLI, environment, or user-supplied registry override.

The evidence schema contains exactly 54 passing observations:

```text
3 roles × 3 categories × 3 actions × 2 modes = 54
```

It also requires passing scenarios for distinct contexts, process/action
attribution, stage order, worker concurrency, reviewer rejection, artifact
integrity and substitution, integration restrictions and verification
commands, deadline handling, direct-child cleanup, descendant leakage, and
output-channel separation. Unknown fields are invalid, so task content cannot
be added through a dedicated evidence field. The harness and package reviewer
must also keep task-derived values out of the opaque build and identity labels;
weightclass cannot infer whether an otherwise valid label came from a task.

`delegate qualification-candidate` validates and canonicalizes that task-free
shape and hashes one local regular executable. Its output is explicitly
untrusted: it neither establishes independent collection nor edits the package
registry. An independent adapter-specific harness, human review, and a source
change are required before any record can ship.

The maintainer runner defines 67 fixed black-box cases and invokes a separately
reviewed adapter-specific driver once per case. The executable contract is:

```text
driver --weightclass-conformance-driver 1
```

The driver receives one bounded canonical JSON request on stdin with exact keys
`case`, `case_id`, `driver_protocol_version`, `runtime_path`, and
`workspace_path`. It may return only `case_id` and boolean `passed`. The runner
requires the response ID to equal the request, caps stdout at 4,096 bytes,
discards stderr, applies a fixed 60-second deadline, and treats nonzero exit,
malformed output, timeout, or a surviving same-process-group descendant as a
failed case. Every case uses a separate private temporary workspace, and the
driver process group is killed when cleanup is required. An interrupt cleans
the active group and exits `130` without a traceback.

Evidence schema 2 binds the runtime size and SHA-256 observed immediately
before the suite. The runner hashes the runtime again after all cases and marks
`artifact_integrity_and_substitution` failed if the identity changed or became
unreadable. Candidate construction independently reopens the runtime and
requires the same size and digest. This closes the simple
test-artifact-then-replace-before-candidate gap, while path-based driver/runtime
execution still has the documented in-run replacement race.

This is an evidence-collection boundary, not an attestation mechanism. An
arbitrary driver can return `passed: true` without invoking the runtime, can
inherit vendor credentials and cause network/billing effects, or can move a
descendant into another session. Independent qualification therefore requires
source-reviewed Claude- and Codex-specific drivers that externally observe the
required effects and leakage. None are shipped yet, and the test fixture must
never be used for a package record.

The next trust-boundary increment begins with the test-only machine-readable
inventory in `tests/fixtures/delegation_claim_map_v3.json`, documented in
`docs/delegation-qualification-oracles.md`. It reconciles all 67 case IDs with
both production catalogs and records a fixed subject, stimulus, expected
observation, negative control, identity boundary, platform boundary, and stable
blocker for each claim. Every row remains blocked and projects no evidence.
The inventory is outside package data and is rejected as both v2 evidence and
registry input. It must not be described as qualification-v3 evidence or as a
vendor feasibility result.

A test-only v2 indistinguishability regression now compares four fixed modes:
sentinel runtime invocation, runtime skip, constant-marker forgery, and
self-attestation. Marker presence differs, but every mode produces identical
v2 evidence and candidate output. This proves only that v2 lacks an independent
runtime-invocation observation. It does not make a marker authentic, allege
misconduct, or qualify the sentinel or fake driver.

At run time, qualified mode opens the path without following a final symlink,
checks regular-file and executable status, reads a bounded artifact through the
opened descriptor, compares exact size and SHA-256, and checks for concurrent
metadata change. Any mismatch fails with `executor_unavailable` before task
stdin is read. Spawning still uses the reviewed path, leaving a documented
hash-to-spawn replacement race until verified-object execution is available.

## P2 — pair-authorized crossed boundaries

Only a new protocol may add cross-provider or mixed-transport execution. Each
direction requires exact provider, intended-recipient, billing-boundary, and
transport pair entries. No global boolean can authorize a combination, and
weightclass still does not verify the actual recipient or billing account.

## Explicit deferrals

- automatic retry, fallback, recovery, backgrounding, or descendant supervision;
- orchestrator-retained implementation or prompt-only `must_delegate`;
- optional protocol-1 assignments or dummy work;
- nested delegation;
- bundled runtime distribution;
- vendor/model/entitlement/cost discovery;
- task journals, task hashes, or adaptive routing state;
- claims that a named model authored particular content.
