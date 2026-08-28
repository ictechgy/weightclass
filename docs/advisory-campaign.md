# Advisory productization campaign

_Status: distributed as the explicit experimental `wclass-advisory` companion command._

For deterministic built-in and exact-command CLI route profiles, including task-delivery review
and egress confirmation, see [Advisory campaign vendor profiles](advisory-vendor-profiles.md).
For an explicit personal Codex/Claude skill install after that runtime is configured, see
[Optional advisory Agent Skill](advisory-skill.md).
For separate review, research, diagnosis, and design result contracts, see
[Read-only advisory evidence workflows](advisory-evidence-workflows.md).

The advisory runner already measures Shape A and Shape B, but an ordinary JSONL
log cannot prove that its verifier, prices, routes, stopping rule, or advisory
context stayed fixed. A product decision must use a sealed campaign manifest.
The manifest is task-free: it contains no task text, task hash, repository path,
timestamp, account/profile, credential, or full command.

## Installed entry point

The installed command keeps private campaign paths explicit:

```sh
wclass-advisory prune \
  --campaign-root /private/path/shape-b-results \
  --vendor codex
```

The seam forwards campaign arguments to `speculative_run.py` and supplies an
anonymous fixed lane below the campaign output root. The old root remains lane
0; additional lanes are `.lanes/lane-01` through `.lanes/lane-09`. Lane names
contain no project, repository, task, process, profile, or fingerprint data.
The seam allocates one free lane per selected vendor/workflow under a short
allocator lock and holds each owner-only lane lock until the complete vendor
run exits. A failed allocation releases every partial lease before task input
is read. It does not reinterpret task delivery, campaign ordinals, egress
confirmation, or result replay; those contracts remain in the reviewed runner.

Machine-local multi-vendor shims use `advisory_orchestration.acquire_campaign_lanes`
before dispatch. Every selected vendor/workflow receives a distinct anonymous
lane, so projects sharing a campaign root do not share a live ordinal or
campaign lock. If every bounded lane for one selected vendor is busy, the whole
batch fails before partial dispatch and every partial lease is released.
The cross-lane allocator has a two-second bounded wait; expiration is reported
as `managed_allocator_busy` instead of an unbounded foreground stall. The
legacy lane-0 `dispatch.lock` is probed nonblocking, so a legacy owner moves a
new run to another free lane. Availability reported by `doctor` is a
point-in-time snapshot rather than a reservation. Capacity checks stream and
validate lane bindings and local ordinals while retaining only counts; full
record merging and global in-memory ordinal rewriting remain confined to
reporting and analysis. Individual JSONL records are bounded to 1 MiB. If a
leased lane is appending its final newline while another lane is allocated, the
allocator ignores only that incomplete tail and counts the live lease as the
reserved sample; an incomplete tail in an idle lane still fails closed.
Every job runs in its own process session with an eight-hour outer deadline and a
1 MiB combined stdout/stderr retention ceiling;
the inner runner's narrower per-child and verifier limits still apply. Timeout
cleanup signals the complete process group, and results remain replayed in
deterministic vendor order.

## Fixed decision contract

- Primary endpoint: cost per passing task.
- Shape B rule: the complete rescue-rate interval must exceed the complete
  `a_B + q*r` interval.
- Shape A+B rule: paired baseline and advised campaigns are required; no
  unpaired or mixed-configuration extrapolation is accepted.
- At least 12 advised failures are required. This threshold existed before the
  current `0/2` result and must not be tuned after seeing more data.
- Both the planned task count and failure minimum must be met before a decision.
- Collection stops only after a decisive interval once both minimums are met,
  or at the sealed maximum. Reaching the maximum without both minimums is not a
  promotion result.
- Any timeout, incomplete pricing, mixed cost origin, duplicate sample ordinal,
  damaged campaign log, or contract mismatch blocks the verdict.

## Seal the campaign before reading a task

Use one manifest and output directory per arm. The example labels below are
opaque caller configuration; weightclass does not assert availability or price.

```sh
wclass-advisory seal \
  --arm shape_b \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --cheap 'codex exec --model REVIEWED_CHEAP_MODEL -' \
  --expensive 'codex exec --model REVIEWED_STRONG_MODEL -' \
  --advisor 'codex exec --model REVIEWED_ADVISOR_MODEL -' \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./prices.json \
  --output ./shape-b-campaign.json
```

`price_table` requires exactly `cheap`, `expensive`, and `advisor` tables. The
file is bounded, duplicate-key-safe, finite, and non-negative. Its raw bytes,
the verifier bytes, and all three complete argv arrays are SHA-256-bound into
the manifest. Only executable basenames and digests are retained; commands and
rates are not copied into the manifest.

Use `--cost-basis vendor` without `--prices` only when every arm reports a
complete cost from the same vendor-defined origin. Do not mix a vendor bill
with price-table conversion.

## Record one representative task

Campaign mode rejects `--label`; the only persisted task handle is an opaque
ordinal within the sealed maximum. It is not a task name or content hash.

```sh
wclass-advisory run \
  --campaign-root /private/path/shape-b-results \
  --vendor codex \
  --repo /path/to/clean/repo \
  --task-file /private/path/current-task.txt \
  --cheap 'codex exec --model REVIEWED_CHEAP_MODEL -' \
  --expensive 'codex exec --model REVIEWED_STRONG_MODEL -' \
  --advisor 'codex exec --model REVIEWED_ADVISOR_MODEL -' \
  --confirm-task-egress \
  --advise-on-failure \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./prices.json --prefer-prices \
  --campaign ./shape-b-campaign.json \
  --sample-ordinal 1 \
```

The runner obtains a nonblocking campaign lock, pins one canonical copy of the
manifest, requires the next contiguous ordinal, and validates every task-free
input before reading `--task-file`. An unbound legacy invocation cannot append
to that output directory. Every campaign input is opened once with no-follow
and nonblocking flags, validated as a regular file through `fstat`, and read
through that descriptor; FIFO and other special-file inputs therefore fail
instead of waiting for I/O.
Descriptor-bound verifier and price-table bytes are staged into the owner-only
output directory; verification and pricing use those private copies rather than
reopening the caller paths. The JSONL record contains the campaign fingerprint,
arm, and ordinal but never task/advice/output text. Run only one task at a time.

### Diagnosing a failed arm

Each failed cheap, advised-retry, or expensive attempt emits one task-free
JSON receipt on standard error. The receipt is an operational event, not a
campaign record: it contains only the selected arm, fixed `failure_kind` and
`failure_stage` values, booleans, and bounded child/candidate/verifier exit,
timing, and count fields. It never contains verifier streams, error text,
paths, task or patch material, model labels, advice, or credentials. Failed
workspaces and patches are discarded.

The verifier's exit status remains the task-specific oracle. Exit `0` means
that the candidate satisfied the verifier; any other ordinary exit is recorded
as `failure_kind=route`, `failure_stage=verification`, and
`error=verification_failed` in the transient attempt record. A verifier
timeout is a verification failure with the same stage and a timeout-specific
internal error. Exit `42` is reserved for the task-free baseline probe and is
not a candidate pass. The receipt retains only the bounded numeric exit code;
the verifier's stdout and stderr are available transiently to the existing
advisor flow and are never printed or persisted by the runner.

Use `failure_stage` to choose the task-specific diagnosis path, then consult
the verifier's own acceptance criteria separately:

- `setup`: workspace creation or checkout failed;
- `execution`: the selected child did not produce a usable result;
- `result`: a read-only result could not be extracted or shaped;
- `handover`: reconstruction or patch-boundary checks rejected the candidate;
- `verification`: the verifier rejected or timed out;
- `verification_integrity`: the verifier exited but modified patched files, so
  its exit code alone is not the final acceptance verdict;
- `acceptance`: the verifier passed but the attempt made no usable change;
- `persistence`: writing an already accepted patch failed;
- `unknown`: a malformed or incomplete in-memory record.

These stages identify where to inspect the task-specific verifier contract;
they do not infer why a model failed, and receipt fields must not be used as a
substitute for verifier output.

## Report and promotion gate

```sh
wclass-advisory report \
  --log /private/path/shape-b-results/runs.jsonl \
  --campaign ./shape-b-campaign.json
```

For a task-free overview of several independently sealed populations, repeat
`--campaign` with the public vendor/workflow labels and each population's
private inputs. Output is canonical aggregate-only JSON; manifest and result
paths are used for loading but are never rendered.

```sh
wclass-advisory portfolio \
  --campaign claude review /private/claude-review.json /private/claude-review-results \
  --campaign codex review /private/codex-review.json /private/codex-review-results
```

When a machine uses the documented `<vendor>[-<workflow>]-shape-b.json` and
results naming below one owner-only campaign directory, the same task-free
status is available without a persisted path configuration:

```sh
wclass-advisory portfolio \
  --campaign-directory /private/advisory-campaigns
```

Discovery uses exact profile-derived vendor names, includes only complete
manifest/results pairs, rejects symlinks or half-configured populations, and
never renders discovered paths.

The portfolio sorts populations deterministically, rejects duplicate labels or
inputs, and reports the sealed task/failure floors, abstention reason, and next
collection action. It does not combine populations or make a promotion
decision; `speculative_report.py` remains the per-campaign economic report.
Actual money cost, token volume, and child latency are separate metrics with
per-stage totals. If any executed stage lacks one metric, that metric's total is
`null` rather than a partial value and the population automatically abstains.
Infrastructure failures also force abstention and remain included in actual
spend/usage totals when their child reported those values.
Even a population with complete sample floors and metrics is only
`evaluate`-ready: its next action is `run_statistical_gate`, and the portfolio
never sets `policy_decision_allowed`. Incomplete measurements instead direct
the operator to `repair_measurement`. Policy changes remain outside this
task-free status command.

`speculative_report.py` remains the price/cost report; the managed acceptance
signal is the provenance-bound gate below. Managed users can apply it without
handling private paths:
`wclass-advisory campaign-gate --vendor VENDOR --workflow WORKFLOW`. It
validates the exact schema-3 population and sealed gate and can emit only
`eligible_for_human_review`, never permission to change routing. Unbound
`experiment sequential --records` input remains descriptive and cannot become
promotion-eligible.

Legacy logs may skip a damaged trailing line for descriptive recovery. A sealed
campaign discovers lane 0 and every existing bounded lane automatically, then
validates each log independently against the exact manifest. Malformed rows,
duplicate JSON keys, missing bindings, repeated/local gaps, mixed fingerprints,
or a combined population over the sealed maximum fail closed. The merged
ordinals exist only in memory; lane records are never rewritten. Even a
statistically decisive point estimate is withheld until the sealed task and
failure minimums are both met. Low-level `--prune` cleans every existing lane
only when the population is inactive. Managed `cleanup` cleans inactive lanes
independently, skips active lanes, and reports only counts. A new campaign
attempt also recovers registered residue from its own locked lane before
creating another workspace.

The campaign receipt is necessary but not sufficient for productization. A
promotion also requires independent blind quality review, zero new critical
failures, zero task/credential persistence or workspace escapes, complete
macOS/Linux compatibility, and a clean security review.

## Product boundary after a passing campaign

Do not add retries to `wclass run`. A passing Shape B campaign authorizes only
a separate explicit surface such as `wclass advisory review|run|prune`, with:

- exact cheap/advisor/escalation/verifier fingerprints;
- explicit task-egress confirmation;
- one bounded advised retry;
- disposable clones and patch-only handoff;
- no automatic patch application;
- aggregate-only router state and no task-level product log;
- advisory disabled unless the caller selects it.

Shape A remains a separate decision. Shape B can pass without proving that an
up-front plan improves subtle quality, and an A+B campaign cannot borrow the
unprimed costs or rates from a Shape-B-only log.
