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
   A future P1 route may fingerprint an exact-artifact qualification target,
   while only a successful run-time match may report execution as
   `conformance_qualified`.
3. **Finite direct-child cleanup.** The fingerprint binds two bounded grace
   intervals and the future run sequence `close -> wait -> terminate -> wait ->
   kill -> reap`. Weightclass will act only on its direct child and will never
   enumerate descendants. A defective trusted runtime can still orphan them;
   descendant leakage fails P1 conformance.
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
7. **Output ownership.** P0.5 runtime stdout/stderr will be inherited,
   uncaptured, unparsed, unredacted, and unbounded. Once runtime output is
   emitted, it is outside weightclass's no-retention guarantee. Terminal or log
   exhaustion is a trusted-runtime risk. Conformance evidence uses a test-only
   harness rather than production stdout parsing.
8. **Named bounds.** Policy, manifest, descriptors, labels, paths, platforms,
   protocols, profiles, workflows, adapters, capabilities, boundary pairs,
   verification commands, argv entries/tokens, process limits, deadlines, and
   cleanup intervals have named bounds in `delegation_schema.py` or
   `delegation_compile.py`. Every integer field rejects booleans, floats, and
   other non-integer JSON values.
9. **No handshake claim.** The manifest is a reviewed offline capability
   declaration. Protocol 1 has no live negotiation or authenticated
   self-attestation. P1 adds independent exact-artifact qualification, not a
   handshake.
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
    Ruff, mypy, and `git diff --check`. P0 does not require runtime or
    qualification test modules that do not exist yet.

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

Add `delegate run` only after P0 is stable. It must require both
`--confirm-trusted-delegation-runtime` and the exact reviewed fingerprint, read
the bounded task only after those static gates, build the complete WCD1 frame
before spawn, and start exactly one foreground external runtime. A fake runtime
must cover partial writes, `EINTR`, `EPIPE`, truncation, oversize, invalid UTF-8,
review rejection, premature success, deadline failure, action-attribution
failure, integration substitution, and cleanup failure.

This phase remains `declared_enforcement`: weightclass observes only the direct
child's final exit or signal and cannot detect a dishonest zero exit.

## P1 — exact-artifact qualification

Build independent conformance suites for Claude-family and Codex-family
adapters. Package-owned records bind executable SHA-256, build ID, platform,
protocol, suite revision, adapter ID, and the complete role/category/action/
mode result matrix. Production must not accept a CLI, environment, or
user-supplied qualification registry. A changed executable byte or mismatched
field fails closed. Path validation and hashing still leave a documented
hash-to-spawn TOCTOU until verified-object execution is available.

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
