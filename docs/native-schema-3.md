# Native schema 3

Native schema 3 describes one deterministic source-profile/tier selection and
one closed built-in CLI invocation. It supports Codex, Claude Code,
Antigravity (`agy`), and Grok. It does not turn weightclass into an agent
orchestrator, provider client, credential manager, subscription accountant, or
model-discovery service.

## Discovery and caller assertions

`wclass discover` reports executable presence only. It searches the absolute
directories supplied in the caller's `PATH` for supported executable names; it
does not authenticate, run the executable, or establish that any profile,
model, account, or subscription is usable. weightclass intentionally initiates
no provider or network request during discovery or native route review. A
`PATH` entry may nevertheless be a remote or automounted filesystem, so normal
filesystem metadata lookup can have external I/O outside weightclass's
knowledge.

Every profile, account-profile, model, effort, entitlement, pricing,
subscription, and quota statement is an opaque caller assertion. Schema
validation checks shape and explicit grants, not the truth of those labels or
the effective provider recipient and billing account.

## Policy and review

A schema-3 policy has exact top-level fields `schema_version`, `profiles`,
`execution_targets`, `routes`, `profile_grants`, and `vendor_grants`. A route is
selected by exact `(source_profile_id, tier)` membership after validating the
explicit source vendor/profile/tier tuple. A profile or vendor change requires
one exact directional grant for each changed dimension. Unknown, ambiguous,
ungranted, or malformed input fails closed.

Ordinary native review uses purpose `native_route`. Nested native delegation
uses a separate purpose:

```sh
wclass delegate native route \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low
```

Only schema 3 is accepted by this nested surface. The command reads the policy,
performs static selection, obtains one `lstat` observation of the selected
regular executable, and prints one canonical, task-free descriptor. Its
fingerprint binds `purpose: "native_delegation"`, the selector, route, opaque
profiles/model/effort, grants, exact argv template and delivery mode, required
confirmations, and executable observation. It does not read task stdin or
start a child. An ordinary `native_route` fingerprint differs and cannot
authorize delegated execution.

Review and run have separate roles. Route output shows what would run; it is
not execution consent. Run consent does not waive exact acknowledgement of the
reviewed fingerprint.

## One-subtask run contract

```sh
printf '%s' 'Complete this one bounded subtask.' | \
  wclass delegate native run \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low \
  --confirm-native-delegation \
  --confirm-endpoint-transition \
  --ack-route-fingerprint 'sha256:copied-from-delegate-native-route'
```

`--confirm-native-delegation` is mandatory for every run.
`--confirm-endpoint-transition` is mandatory only when the reviewed descriptor
lists `endpoint_transition`; it is not required when source and destination
profile/vendor are unchanged. The run order is fixed:

1. parse and validate schema, selector, and tier;
2. perform task-free static selection and derive required confirmations;
3. require native-delegation and, when listed, endpoint-transition consent;
4. require a nonempty exact fingerprint acknowledgement;
5. validate safe direct-child process context;
6. obtain the first executable observation and bind the review descriptor;
7. compare the exact acknowledgement;
8. read and validate stdin exactly once;
9. observe the executable again immediately before spawn;
10. materialize once and start exactly one foreground child.

Every failure before step 8 reads zero task bytes and starts zero children.
There is no fallback, recovery, or retry path. Child stdout/stderr and the
environment are inherited and uncaptured; weightclass does not parse,
synthesize, redact, or persist child output. It observes only the direct
child's status and does not supervise descendants.

Codex and Claude receive the exact validated UTF-8 task bytes on stdin. `agy`
and Grok replace exactly one reviewed `{{task}}` argv slot and receive empty
stdin. That argv delivery exposes task text to local process inspection while
the child runs. It rejects a NUL, more than 32,768 UTF-8 task bytes, and a Grok
task beginning with `-`. Materialized argv is limited to 32 tokens, 32,768
UTF-8 bytes per token, and 49,152 aggregate token bytes. The general task input
limit remains 80,000 UTF-8 bytes, subject to the narrower argv limit.

The delegated unit is exactly one bounded subtask to one child. weightclass
does not decompose it, create planner/worker/reviewer roles, inspect completion
semantics, retain an artifact, integrate a result, calculate token or monetary
usage, or verify which model/account/provider handled it. Use the separate
`wclass delegate route|run` external-runtime protocol only when a reviewed
orchestration runtime is actually intended; native delegation does not borrow
that protocol's claims.

## Fail-closed results

| Exit | Diagnostic | Meaning |
| --- | --- | --- |
| `2` | `invalid_input` or `invalid_task` | Malformed/wrong-schema input, or invalid UTF-8/size/NUL/materialization. |
| `3` | `unsupported_route` | No exact route or required directional grant. |
| `4` | `executor_unavailable` | Unsafe process context or unavailable first/final executable observation. |
| `5` | `confirmation_required` | Required native-delegation or endpoint-transition consent is absent. |
| `6` | `route_fingerprint_mismatch` | Missing, empty, wrong, ordinary-purpose, or drifted fingerprint/observation. |
| `7` | `executor_failed` | Spawn/status failure or nonzero child status. |

Diagnostics are redacted. weightclass never places task content or task hashes
in policy, fingerprints, artifacts, router stdout/stderr, exceptions,
representations, logs, or comparison diagnostics. The inherited child output
is outside that guarantee because the selected vendor process controls it.

## Residual executable race

The route descriptor binds a complete `lstat` identity, and run compares that
identity before task access and again immediately before spawn. This detects
ordinary executable replacement across review/run and during the pre-spawn
window. The final observation and path-based process creation are not atomic,
so an actor able to replace the path after the final check can still win a
time-of-check/time-of-use race. Keep the executable and every containing
directory under an appropriate ownership boundary; schema 3 does not claim
verified-object execution.
