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

Review uses purpose `native_route`, which is the only purpose the CLI selects:

```sh
wclass route \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low
```

The command reads the policy, performs static selection, obtains one `lstat`
observation of the selected regular executable, and prints one canonical,
task-free descriptor. Its fingerprint binds `purpose: "native_route"`, the
selector, route, opaque profiles/model/effort, grants, exact argv template and
delivery mode, required confirmations, and executable observation. It does not
read task stdin or start a child.

A route that would require a `native_delegation` confirmation is refused with
`unsupported_route`. The nested `delegate native` surface that offered that
confirmation was removed in 0.32.0, so a policy still asking for it is closed
rather than run without the consent it names.

Review and run have separate roles. Route output shows what would run; it is
not execution consent. Run consent does not waive exact acknowledgement of the
reviewed fingerprint.

## One-subtask run contract

```sh
printf '%s' 'Complete this one bounded subtask.' | \
  wclass run \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low \
  --confirm-endpoint-transition \
  --ack-route-fingerprint 'sha256:copied-from-wclass-route'
```

`--confirm-endpoint-transition` is mandatory only when the reviewed descriptor
lists `endpoint_transition`; it is not required when source and destination
profile/vendor are unchanged. The run order is fixed:

1. parse and validate schema, selector, and tier;
2. perform task-free static selection and derive required confirmations;
3. refuse a route requiring the removed native-delegation confirmation, and
   require endpoint-transition consent when the descriptor lists it;
4. require a nonempty exact fingerprint acknowledgement;
5. validate an explicitly selected or already enabled aggregate usage store;
6. validate safe direct-child process context;
7. obtain the first executable observation and bind the review descriptor;
8. compare the exact acknowledgement;
9. read and validate stdin exactly once;
10. materialize and validate the redacted invocation exactly once;
11. observe the executable again, then immediately start exactly one foreground child;
12. after a real child status is available, atomically increment the aggregate store.

Every failure before step 9 reads zero task bytes and starts zero children.
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

The executed unit is exactly one bounded subtask to one child. weightclass
does not decompose it, create planner/worker/reviewer roles, inspect completion
semantics, retain a task artifact, integrate a result, calculate token or
monetary usage, or verify which model/account/provider handled it. The optional
local usage store records cumulative selected agent/model/effort/tier and
direct-child status counters only. It has no task identifier, per-run record,
timestamp, profile/account, path, fingerprint, provider usage, or inferred
price. Relative weights apply prospectively, and weights plus rework/escalation
flags are caller assertions.

## Fail-closed results

| Exit | Diagnostic | Meaning |
| --- | --- | --- |
| `2` | `invalid_input` or `invalid_task` | Malformed/wrong-schema input, or invalid UTF-8/size/NUL/materialization. |
| `3` | `unsupported_route` | No exact route, no required directional grant, or a route requiring the removed native-delegation confirmation. |
| `4` | `executor_unavailable` | Unsafe process context or unavailable first/final executable observation. |
| `5` | `confirmation_required` | Required endpoint-transition consent is absent. |
| `6` | `route_fingerprint_mismatch` | Missing, empty, wrong, or drifted fingerprint/observation. |
| `7` | `executor_failed` | Spawn/status failure or nonzero child status. |
| `9` | `usage_unavailable` | Enabled aggregate state was unsafe/unavailable, or its post-child atomic update failed. |

Diagnostics are redacted. weightclass never places task content or task hashes
in policy, fingerprints, artifacts, router stdout/stderr, exceptions,
representations, logs, or comparison diagnostics. The inherited child output
is outside that guarantee because the selected vendor process controls it.

## Residual executable race

The route descriptor binds a complete `lstat` identity, and run compares that
identity before task access and again after task-dependent materialization,
immediately before spawn. Admission additionally rejects other-writable
executable files, group-writable files not owned by root or the current user,
and non-sticky world-writable containing
directories in both the lexical and resolved target chains. Sticky directories
Root/current-user-owned group-writable files, sticky directories, and
user-owned group-writable ancestors remain compatible. A world-writable hosted
tool cache is not implicitly trusted; automation must stage or select a private
runtime path. This narrows ordinary replacement opportunities but does not make
the final observation and path-based process creation atomic: an actor able to
replace an admitted path after the final check can still win a
time-of-check/time-of-use race. Schema 3 therefore does not claim
verified-object execution; a portable descriptor-based launcher remains future
work.
