# Native integrations

weightclass is a local router, not a modification to Codex or Claude Code. It
does not install hooks, edit global configuration, infer the calling vendor, or
reuse an existing interactive agent session. Use these reviewable commands from
the project directory after installing `weightclass`.

## Shared safe workflow

1. Pass the task on standard input and review the selected route first, then
   pass its `route_fingerprint` to `run` so the selection you read is the
   selection that executes.
2. Run the route only after the rendered command and vendor are acceptable. The
   command is the whole decision: the model and effort arguments appear in it.
3. Keep a Codex-originated task on Codex and a Claude-originated task on Claude
   unless a reviewed policy explicitly enables cross-vendor routing.

## Local discovery and profile selection

Use `wclass discover` to list package-supported agent executable names found in
absolute `PATH` entries. It performs filesystem checks only: no agent is
started, no task is read, and no vendor configuration, authentication file, or
network service is accessed. The result does not prove subscription access,
model entitlement, price, quota, or installed-CLI compatibility. Model and
effort catalogs are package declarations with availability explicitly
unverified.

Use `wclass profile --agent <agent> --tier <tier> --model <label> --effort
<effort>` to generate one schema-1 policy on standard output. `default` omits a
model override; another accepted model label is opaque user configuration, not
an availability claim. `agy` supports only `default` because it has no reviewed
model-override builder. Add `--allow-cross-vendor` only for an intentional
boundary change, then review the generated policy through the ordinary
fingerprint-bound `route`/`run` workflow. The generated policy uses the
detected absolute lexical executable path, starts nothing by itself, and is
never persisted unless the caller explicitly writes its stdout to a selected
file.

Do not put credentials, tokens, or personal information on a command line, in
a policy, or in vendor-global configuration. Task text is a deliberate
exception to that rule, not an oversight: the built-in `agy` and `grok` routes,
and any policy route that declares the reserved `{{task}}` token, put the task
on the command line by design, because those CLIs accept a prompt only as an
argument. The token may occupy one prompt-value element but never `argv[0]`;
the executable must remain fixed in the reviewed policy. Command lines are
readable by every user on the machine — anyone who
can run `ps` sees the task for as long as the child runs. On a single-user
machine this is inconsequential; on a shared host it is not. This is a real
exposure you accept each time you write `{{task}}` into a policy or select an
`agy`/`grok` built-in route, stated the same way in `README.md` next to the
route documentation.

For schema 2, `route` and `run` additionally require `--source-profile`. Review
the canonical source/destination profiles, exact model/effort pair, executable,
argv, transitions, and directional grants. Then provide the emitted fingerprint
to `run` with `--ack-route-fingerprint`. Missing acknowledgement stops before
task access; mismatch stops before executable inspection or spawn. Account,
recipient, billing, subscription, entitlement, model, and effort labels are
opaque declarations, not facts verified by weightclass.

## Codex

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass route --source-vendor codex
```

After reviewing the JSON route descriptor, run the selected Codex workflow:

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass run --source-vendor codex
```

With the built-in policy, this starts exactly one foreground `codex exec`
process. It does not change the configuration or context of an already-running
Codex session. For the packaged opt-in, review all three commands with
`wclass review-preset codex-cost-focused`; tier-specific `--low-model`,
`--standard-model`, `--high-model`, and matching effort flags can then be
passed unchanged to `route --preset codex-cost-focused` and `run`. A local
`--policy` remains the general extension point. All labels are opaque caller
configuration.

## Claude Code

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass route --source-vendor claude
```

After reviewing the JSON route descriptor, run the selected Claude workflow:

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass run --source-vendor claude
```

With the built-in policy, this starts exactly one foreground `claude --print`
process with `acceptEdits` permissions and no session persistence. Print mode is
non-interactive, so a permission mode that prompts a human refuses every edit
while still exiting `0`. `route` and `run` read the policy separately, so pass
`run` the `route_fingerprint` from the review to bind the two together; without
it the run re-selects whatever the policy says at that moment. It does not
change
the configuration or context of an already-running Claude Code session. Use a
reviewed local policy for arbitrary command shapes, or use the packaged
`claude-cost-focused` preset with the same tier-specific model/effort flags as
Codex. First run `wclass review-preset claude-cost-focused` with the intended
flags, then use identical flags on `route` and fingerprint-acknowledged `run`.
weightclass never probes model availability, subscription access, remaining
usage, or price.

Every custom preset is reported as `unqualified_custom`. It is not covered by
the measured Claude low-route result and is not evidence of token or cost
savings. Presets write no router state or vendor configuration; removing the
selector returns to the unchanged built-in routes.

## Policy review

Both integrations accept the same local policy format:

```sh
printf '%s' 'Review this authorization change.' | \
  wclass route --source-vendor codex --policy policy.json
```

`allow_mixed_vendors` is `false` by default. Set it to `true` only in a
reviewed policy when intentionally allowing a task to leave its source vendor.
The optional `posture` is `balanced` by default. A reviewed `cautious` posture
raises ambiguous local `standard` decisions to `high` while retaining the same
source-vendor filter and opaque policy commands. The route review displays the
explicit posture and static reason code, and its fingerprint binds the posture.
`cautious` may select a higher-effort command and increase token use; neither
posture is a measured token-saving mode. Use the offline paired gate in
`tests/eval/README.md` before making an efficiency claim about a custom policy.
Use its separate estimated-cost gate for provider-reported cost evidence;
lower estimated cost does not imply fewer raw tokens or an actual lower bill.
For sanitized provider-export evidence, use
`tests/eval/provider_usage_benchmark.py`: metered cost can support a later
explicit opt-in review, while fixed-price subscription quota is capacity-only
and never a monthly-bill claim. Never pass a raw billing export to the scorer.
For API routes, use `wclass v2 route` followed by the explicit egress
acknowledgement described in the main README; native integration commands never
read API credentials.

## Offline Claude and Codex role review

`wclass delegate route` compiles the same planner/worker/reviewer contract for
Claude-family and Codex-family profiles while keeping every role with the
explicit source vendor:

```sh
wclass delegate route \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor claude \
  --tier standard
```

The route command reads no task standard input and does not inspect or execute
the runtime path. Its `declared_enforcement` descriptor reports only that the
policy and offline capability declaration are schema-compatible. It is not a
runtime handshake, proof of delegation, or semantic-authorship claim.

After review, P0.5 can start one user-supplied trusted runtime:

```sh
printf '%s' 'Apply the reviewed change.' | \
  wclass delegate run \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor claude \
  --tier standard \
  --confirm-trusted-delegation-runtime \
  --ack-route-fingerprint 'sha256:copied-from-route'
```

The runtime receives the exact canonical descriptor and transient task through
the WCD1 stdin frame and owns all Claude/Codex process creation, authentication,
network, billing, permissions, review, integration, descendant cleanup, and
output. weightclass bundles no runtime and cannot detect a dishonest zero exit.

Protocol 2 uses an independent WCD2 byte frame and requires the exact
policy/manifest/descriptor/runtime/frame version-2 tuple plus
`--source-profile`. It can express authorized same-vendor account-profile and
cross-vendor transitions. It is not eligible for qualified-runtime mode. The
runtime executable is observed twice using lexical path, device, inode,
type/mode, size, nanosecond modification/change times, and POSIX execute bits.
A zero-size executable is structurally allowed; final symlinks are rejected,
while intermediate-component symlinks and replacement after the second check
remain residuals. Execution is path-based and direct-child-only.

## Orchestration patterns reflected in protocol 2

Protocol 2 borrows reviewable structure, not another tool's runtime claims:

- Orca informs requested run/task/dispatch provenance, requested ownership,
  DAG readiness, typed gates, and the separation of direct-child completion
  from task settlement.
- Codex informs bounded independent workstreams, explicit projections,
  synthesized terminal structure, mutable-scope ownership, and a separately
  selected model/effort for each task.
- Claude informs typed model/effort, tool, permission, and turn requests.
  Hooks, memory, background work, and peer messaging remain runtime-owned and
  are not router features.
- Cursor informs explicit allowed model/effort pools and task-mode binding,
  without any network, background, or retention guarantee.
- LangGraph informs typed transitions, gates, and projections, but protocol 2
  deliberately adds no persisted graph state or resume mechanism.
- AutoGen informs structural fanout/join and synthesis-terminal validation;
  message selection/filtering is not adopted.
- CrewAI topology labels are not adopted because protocol 2 does not derive
  them deterministically. A caller may describe a typed approved-value gate as
  human-in-the-loop, but the human involvement itself is not structurally
  verified and there is no callback or user interface.
- OpenHands informs provider-neutral capability and workspace vocabulary,
  without claiming sandbox parity.

The exact schema path, validator, regression test, enforcement status, and
non-goal for every row are machine-checked in
`tests/fixtures/orchestration_traceability.json` by
`tests/test_orchestration_traceability.py`.

For the P1 gate, add `--require-qualified-runtime` to both the review and run
commands. Production consults only the registry shipped inside the weightclass
package; it accepts no environment, CLI, or user-registry override. The current
registry is empty, so this mode intentionally returns `unsupported_route` for
every adapter. If a reviewed package record is added later, the route binds its
build/platform/protocol/adapter/vendor identity, exact artifact size and
SHA-256, suite revision, and conformance-evidence digest. Run verifies those
artifact bytes before reading task stdin. The later path-based spawn still has
a documented hash-to-spawn replacement race.

`wclass delegate qualification-candidate` only validates a complete task-free
evidence document and hashes a local executable for package-maintainer review.
It neither updates the registry nor proves that the supplied evidence was
independently collected.

The repository-maintainer runner is available as
`python -m weightclass.delegation_conformance`. It executes a separately
reviewed driver once per predeclared case with fixed protocol argv, a private
temporary workspace, bounded stdout, a fixed deadline, and process-group
cleanup. It reads no task stdin and emits only the complete task-free evidence
shape. Evidence schema 2 includes the runtime size and SHA-256 observed before
the suite; the runner rechecks them after all cases, and candidate construction
rechecks the current artifact again. The driver inherits the environment and
owns any runtime/vendor process, authentication, network, quota, or billing
effects. No Claude or Codex driver is currently shipped; the test-only fake
driver is not qualification evidence.

See [the delegation roadmap](delegation-roadmap.md) for the strict schema and
the exact-artifact qualification gate. Do not put
task content, credentials, recipient data, or billing identifiers into a role
profile, runtime manifest, or conformance evidence document.
