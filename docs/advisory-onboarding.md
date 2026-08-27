# Managed advisory onboarding

`wclass-advisory` is installed in the same distribution as `wclass`, but it is
always explicit and never becomes a core route. Managed onboarding removes the
need to hand-wire profile, campaign, verifier-wrapper, price-table, and result
paths for every project.

## Initialize a vendor

Supply exact model and effort labels selected for the three roles. weightclass
does not validate entitlement, availability, relative quality, or remaining
subscription usage:

```sh
wclass-advisory init --vendor codex \
  --model cheap=CHEAP --model advisor=ADVISOR --model expensive=EXPENSIVE \
  --effort cheap=low --effort advisor=high --effort expensive=high
```

Repeat for another vendor. `init` creates implementation, review, research,
diagnosis, and design campaigns with the fixed 60-task/12-advised-failure
evidence gates and ten anonymous lanes. Identical input is idempotent. Different
input is rejected instead of changing an existing population. Use a new state
root deliberately when testing a different model or pricing treatment.

Add `--prices /path/to/prices.json` only for a reviewed single-origin price
table. Without it the campaign uses the vendor-reported basis; incomplete cost
reporting causes an economic abstention rather than invented pricing. A
schema-2 arbitrary vendor can use `init --profile /path/to/profile.json`.

The default root is `~/Library/Application Support/weightclass/advisory-v1` on
macOS and `$XDG_STATE_HOME/weightclass/advisory-v1` (or
`~/.local/state/weightclass/advisory-v1`) on other POSIX systems. The root and
result directories are owner-only; files are owner-only. `--state-root` is an
advanced explicit override and is never required by the Agent Skill.

## Prepare a project

Commit one prospective verifier before observing a candidate:

- implementation: `.weightclass/verify`
- review: `.weightclass/verify-review`
- research: `.weightclass/verify-research`
- diagnosis: `.weightclass/verify-diagnosis`
- design: `.weightclass/verify-design`

It must return `42` for the documented task-free baseline probe, `0` only when
the candidate meets the fixed acceptance criteria, and another code for an
infrastructure failure. Managed state contains a package verifier that loads
the workflow verifier from the clean repository's committed `HEAD`, so a model
cannot weaken acceptance by editing the working copy.

## Check, review, and dispatch

```sh
wclass-advisory doctor --vendor all --workflow review
wclass-advisory cli-check --vendor all
wclass-advisory review --vendor all --workflow review
wclass-advisory dispatch \
  --vendor all --workflow review \
  --repo /absolute/clean/repository \
  --task-file /absolute/owner-only/task-file \
  --confirm-task-egress
```

For an isolated answer that must not become campaign evidence, use `consult`
with a read-only workflow (`review`, `research`, `diagnosis`, or `design`):

```sh
wclass-advisory review --consult --vendor claude --workflow review
wclass-advisory consult --vendor claude --workflow review --role cheap \
  --repo /absolute/clean/repository \
  --task-file /absolute/owner-only/task-file \
  --ack-route-sha256 claude=sha256:REVIEWED --confirm-task-egress
```

It invokes exactly one role per vendor, takes no lane, writes no sample, and
prints one tagged NDJSON receipt per vendor. The nested result is model-authored
untrusted content even after closed-schema validation. Custom schema-2 profiles
require `--confirm-provider-egress`; their task-free provider check completes
before the task path is inspected. `review --consult` validates the same
non-recording profile routes without depending on campaign records.
It prints their exact argv, profile digest, and workflow-specific
`route_sha256`; supply one `--ack-route-sha256 VENDOR=sha256:...` per selected vendor. Provider
conformance and the task-consuming child both recheck that same digest before
task access.

`doctor`, `cli-check`, and `review` are task-free. `doctor` locally invokes
installed CLI `--help`/`--version` with a minimal environment and temporary
working directory; it sends no task bytes or provider prompt but is not a network
sandbox for a hostile executable. It distinguishes
`campaign_ready` from `dispatch_ready`. `dispatch` repeats the same local check
for cheap, advisor, and expensive before it inspects the task file, then validates every selected
profile, manifest, price basis, result lane, ordinal, clean repository,
committed verifier, and private task file before vendor execution. Task content
is never stored, logged, echoed, hashed, or used in a label. Codex and Claude
normally receive it through stdin; agy retains its reviewed argv exposure and
Grok receives an inherited `/dev/fd/N` pipe. No task pathname or task file is
created; failure to establish anonymous descriptor delivery stops the route
before egress.

The managed parent also pins its loaded weightclass version into every runner
bootstrap. A concurrent package replacement fails before task access with
`managed_runner_version_changed`; retry only after the install is complete.
Concurrent `init` or migration setup returns `managed_setup_busy` after a
bounded wait rather than blocking indefinitely.

An explicit live readiness check is available when local capability is not
enough:

```sh
wclass-advisory provider-check --vendor all --workflow review \
  --confirm-provider-egress
```

It makes three task-free provider calls per vendor and may consume quota or
incur cost. It returns only vendor, role, fixed failure code, exit/timing
fields, presence booleans, and result shape. It never writes provider output or
a campaign sample. A failed check therefore cannot contaminate effectiveness
or cost evidence.

Use `wclass-advisory status` for aggregate readiness and evidence. Use
`wclass-advisory cleanup` only to prune registered disposable workspaces; it
does not remove profiles, sealed campaigns, or aggregate records. Cleanup
locks and cleans each inactive lane independently, skips active lanes, and
returns one task-free JSON receipt with removed, retained, and busy counts.
Rerun it until `complete` is true when another dispatch was active. A new
campaign attempt also removes registered residue from its own lane while
holding that lane's campaign lock, before it creates another workspace. That
automatic recovery emits only counts and never a workspace path. The low-level
`run --campaign-root ...` interface remains available for advanced callers and
backward compatibility. Its help labels the command as advanced, lists the
security-critical options forwarded to the sealed runner, and points managed
users back to `dispatch`; those forwarded options include `--repo`,
`--task-file`, and `--confirm-task-egress`.

Managed dispatch reports `managed_lane_unavailable` only when every bounded
lane for a selected vendor/workflow is actively leased. A sealed sample cap is
`managed_campaign_capacity_reached`; `managed_allocator_busy` means the short
allocator exceeded its bounded wait; other preflight or binding failures remain
`managed_dispatch_rejected`. `managed_provider_preflight_failed` includes only
the vendor, role, fixed failure code, and `sample_recorded:false`. `doctor`
reports ten configured lanes by default
plus a point-in-time free/busy snapshot. After lanes are selected, dispatch
immediately emits a task-free `managed_dispatch_started` event; a long silence
after that event is vendor/verifier work, not silent lock acquisition. During a
long call, fixed `managed_vendor_heartbeat` receipts appear on standard error;
`managed_vendor_completed` marks child completion. These receipts contain only
vendor, workflow, and elapsed seconds.

## Offline experiment analysis

`wclass-advisory experiment` analyzes caller-prepared, aggregate-only JSONL and
never writes advisory state or changes core routing. The four closed schemas
cover conservative sequential acceptance, a Context Guard × advisory 2×2
matrix, paired single-generator versus generator-critic brainstorming, and
confidence/abstention calibration:

```sh
wclass-advisory experiment sequential --records outcomes.jsonl
wclass-advisory experiment context-2x2 --records context-matrix.jsonl
wclass-advisory experiment brainstorm --records paired-ratings.jsonl
wclass-advisory experiment confidence --records predictions.jsonl
```

Unknown fields and malformed records fail closed without echoing input. The
sequential decision uses a simultaneous Hoeffding union-bound confidence
sequence, so repeated inspection does not silently turn a fixed-horizon
interval into an unsafe stopping rule. Context interaction is labeled
descriptive rather than causal; brainstorming keeps preference, constraint
compliance, duplicates, and rater agreement separate; confidence records must
represent abstentions with null prediction and outcome. None of these reports
promotes a model or updates a route.
See [Advisory experiment records](advisory-experiments.md) for every closed
input shape and the interpretation boundary.

Release 0.17.5 introduced a Claude evidence-route generation because the
older plan-mode executor did not mechanically require the closed workflow JSON.
The replacement uses the complete four-mode schema, including exact required
fields, closed objects, bounded arrays, and bounded strings; the local parser
remains the final byte-bounded validation boundary.

Release 0.17.6 moved that complete contract to `structured-v5`. Release 0.17.8
uses `structured-v6`, where each provider call receives only its selected
workflow schema instead of the four-workflow `oneOf`; this stays below Claude's
structured-output grammar complexity limit while the local parser still
enforces the complete selected contract. The 0.17.5
package changed the route fingerprint after some machines had already created
`structured-v1`, so those machines correctly rejected the mismatch. Migration
now accepts the newest complete v5, v4, v3, v2, or v1 population, or the older
unversioned population as its source, validates all bindings, creates an empty
v6 population, and preserves every source byte. V2 through v4 were used only
by local pre-release verification and are handled so those machines recover too.
Existing Claude review/research/diagnosis/design records are never rewritten or
merged. Upgrade an existing managed root explicitly:

```sh
wclass-advisory migrate-evidence --vendor claude --dry-run
wclass-advisory migrate-evidence --vendor claude
```

The old campaign files and records remain owner-private and read-only at their
existing paths. Managed doctor, dispatch, and status select only the new
`structured-v6` generation after migration. Claude implementation and every
Codex population retain their existing paths and records.

Release 0.17.8 also changes built-in agy and Grok command fingerprints. Agy
read-only routes remove the rejected `--effort` flag, preserve effective plan
mode, and add the workflow JSON Schema; Grok evidence executors add the same
schema. Existing populations remain read-only. Migrate them explicitly:

```sh
wclass-advisory migrate-routes --vendor agy --dry-run
wclass-advisory migrate-routes --vendor agy
wclass-advisory migrate-evidence --vendor grok --dry-run
wclass-advisory migrate-evidence --vendor grok
```

Agy migration creates empty current campaigns for all five workflows because
its advisor command also changed. Grok migration changes only the four evidence
workflows; its implementation population retains its existing path. Neither
operation rewrites, merges, or counts an old record in the new generation.

Failed child processes expose only a fixed `child_failure_code` plus stdout/
stderr presence booleans. Categories cover authentication, rate limits, context
limits, invalid invocation, permission/approval, network, provider/model
availability, account limits, configuration, result contracts, timeout, and
unknown failures. No raw child output is retained. A failed
implementation child that produced no candidate is classified as infrastructure,
skips the verifier, and is excluded from model-quality denominators.
