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
wclass-advisory review --vendor all --workflow review
wclass-advisory dispatch \
  --vendor all --workflow review \
  --repo /absolute/clean/repository \
  --task-file /absolute/owner-only/task-file \
  --confirm-task-egress
```

`doctor` and `review` are task-free. `dispatch` validates every selected
profile, manifest, price basis, result lane, ordinal, clean repository,
committed verifier, and private task file before vendor execution. Task content
is never stored, logged, echoed, hashed, or used in a label. Codex and Claude
normally receive it through stdin; agy retains its reviewed argv exposure and
Grok uses a private transient prompt file.

Use `wclass-advisory status` for aggregate readiness and evidence. Use
`wclass-advisory cleanup` only to prune registered disposable workspaces; it
does not remove profiles, sealed campaigns, or aggregate records. The low-level
`run --campaign-root ...` interface remains available for advanced callers and
backward compatibility. Its help labels the command as advanced, lists the
security-critical options forwarded to the sealed runner, and points managed
users back to `dispatch`; those forwarded options include `--repo`,
`--task-file`, and `--confirm-task-egress`.

Managed dispatch reports `managed_lane_unavailable` only when every bounded
lane for a selected vendor/workflow is actively leased. A sealed sample cap is
`managed_campaign_capacity_reached`; `managed_allocator_busy` means the short
allocator exceeded its bounded wait; other preflight or binding failures remain
`managed_dispatch_rejected`. `doctor` reports ten configured lanes by default
plus a point-in-time free/busy snapshot. After lanes are selected, dispatch
immediately emits a task-free `managed_dispatch_started` event; a long silence
after that event is vendor/verifier work, not silent lock acquisition.

Release 0.17.5 introduced a Claude evidence-route generation because the
older plan-mode executor did not mechanically require the closed workflow JSON.
The replacement uses the complete four-mode schema, including exact required
fields, closed objects, bounded arrays, and bounded strings; the local parser
remains the final byte-bounded validation boundary.

Release 0.17.6 moves that complete contract to `structured-v5`. The 0.17.5
package changed the route fingerprint after some machines had already created
`structured-v1`, so those machines correctly rejected the mismatch. Migration
now accepts the newest complete v4, v3, v2, or v1 population, or the older
unversioned population as its source, validates all bindings, creates an empty
v5 population, and preserves every source byte. V2 through v4 were used only
by local pre-release verification and are handled so those machines recover too.
Existing Claude review/research/diagnosis/design records are never rewritten or
merged. Upgrade an existing managed root explicitly:

```sh
wclass-advisory migrate-evidence --vendor claude --dry-run
wclass-advisory migrate-evidence --vendor claude
```

The old campaign files and records remain owner-private and read-only at their
existing paths. Managed doctor, dispatch, and status select only the new
`structured-v5` generation after migration. Claude implementation and every
Codex population retain their existing paths and records.

Failed child processes expose only a fixed `child_failure_code` plus stdout/
stderr presence booleans. Categories cover authentication, rate limits, context
limits, invalid invocation, permission/approval, network, provider availability,
timeout, and unknown failures. No raw child output is retained. A failed
implementation child that produced no candidate is classified as infrastructure,
skips the verifier, and is excluded from model-quality denominators.
