# Advisory next experiments

_Status: task-free operating plan for new sealed populations. It does not modify or reinterpret
the existing Shape-B campaigns._

## Why this is a new experiment

The local Shape-B sequence is not Anthropic's server-side Advisor mechanism. The original can
interrupt one executor request and expose its transcript to the advisor. The local CLI boundary can
only pass a failed attempt's task-free artifacts to an advisor between processes, then start a fresh
cheap executor:

```text
cheap executor -> verify -> advisor -> fresh cheap retry -> verify -> expensive fallback
```

Existing rows and manifests stay immutable. Never backfill a task category, timestamp, advice text,
verifier output, or task identifier. New hypotheses use new campaign roots and opaque cohort IDs.

## Read the additive diagnostics

`wclass-advisory status` and `portfolio` expose these task-free fields for every population:

- `arm`: the sealed `shape_b` or `shape_a_b` treatment;
- `advice_diagnostics.first|failure`: advice record count, complete character totals/maxima, and
  coverage-aware counts for `empty`, `truncated`, `route_failed`, and `envelope_only`;
- `failure_stages_by_attempt`: closed failure-stage counts separated into `cheap`, `retry`, and
  `expensive` attempts;
- `retry_diagnostics`: accepted retries, same/different/unknown failure-stage counts, and the closed
  cheap-stage-to-retry-outcome transition matrix.

The `recorded` denominator prevents an older row that lacks one field from silently becoming a
false value. Character totals are `null` unless every advice record has a valid nonnegative count.
These fields are diagnostics only; they do not change campaign eligibility, statistical gates, or
`policy_decision_allowed:false`.

Status also renders a closed `operating_recommendation`. It is diagnostic-only and never changes an
existing campaign contract. Missing coverage requests more diagnostics, any observed empty,
truncated, route-failed, or envelope-only advice requests delivery repair, and healthy Shape-B
advice followed by at least one rejected retry recommends reviewing a separate Shape A+B design.
That recommendation is not an effectiveness result, a stopping rule, or execution
authority. It requests human review of a non-authorizing pilot contract; it does not authorize a
pilot dispatch. This lets a human distinguish advice transport from retry shaping before committing
more naturally occurring work to the same treatment.

## Track 1 — prioritize real Codex review tasks

As of 2026-08-30, the Codex review Shape-B population has 14/60 usable tasks and 9/12 advised
failures. Collect only naturally occurring review work; do not create synthetic tasks to reach a
floor. Before each dispatch, review the task-free route and use the existing managed population:

```sh
wclass-advisory review --vendor codex --workflow review
wclass-advisory dispatch \
  --repo /absolute/repository \
  --task-file /private/owner-only/current-task.txt \
  --vendor codex \
  --workflow review \
  --confirm-task-egress
wclass-advisory status --vendor codex --workflow review
```

The task file must remain absolute, owner-only, and transient. A custom schema-2 provider also
requires its separately reviewed `--confirm-provider-egress`; do not add that flag to a built-in
route merely by habit.

## Track 2 — run Shape A+B as a separate sealed population

Managed `init` remains the stable Shape-B surface. Do not replace its manifests or append Shape A+B
rows to its result directories. Seal Shape A+B under a different owner-private root with the same
pre-registered task order, verifier, pricing origin, and exact route profile used by its paired
Shape-B baseline:

```sh
install -d -m 700 /private/advisory-experiment/shape-a-b \
  /private/advisory-experiment/shape-a-b/results

python3 -m weightclass.advisory seal \
  --arm shape_a_b \
  --workflow implementation \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --route-profile /private/reviewed-codex-profile.json \
  --advisor-context repo \
  --verify /private/verify.sh \
  --prices /private/prices.json \
  --output /private/advisory-experiment/shape-a-b/campaign.json

wclass-advisory run \
  --campaign-root /private/advisory-experiment/shape-a-b/results \
  --vendor codex \
  --workflow implementation \
  --repo /absolute/repository \
  --task-file /private/owner-only/current-task.txt \
  --route-profile /private/reviewed-codex-profile.json \
  --confirm-task-egress \
  --advise-first \
  --advise-on-failure \
  --advisor-context repo \
  --verify /private/verify.sh \
  --prices /private/prices.json --prefer-prices \
  --campaign /private/advisory-experiment/shape-a-b/campaign.json \
  --sample-ordinal 1
```

Use the command's `--help` output as the source of truth before running. Increment the ordinal only
after the previous record is durably accepted. The paired comparison is invalid if task order,
routes, verifier, pricing source, or stopping rules differ. Shape A+B cannot borrow unprimed costs
from an unrelated Shape-B population.

## Track 3 — split implementation strata without persisting task labels

Do not add `mechanical`, `architecture`, repository names, or another task-derived label to a
campaign record. Instead, pre-register the split outside weightclass and initialize two separate
managed state roots with opaque names such as `cohort-01` and `cohort-02`. Each root gets its own
sealed manifests, results, ordinals, and status invocation:

```text
/private/advisory-cohorts/cohort-01
/private/advisory-cohorts/cohort-02
```

Use the same reviewed profile, verifier version, price table, planned floor, and maximum in both
roots. Decide the cohort before invoking weightclass, never persist the mapping in campaign state,
and never combine their rows. Compare the two aggregate reports only after each independently meets
its sealed floors.

## Decision order

1. Read `operating_recommendation` together with the underlying task-free diagnostics; never treat
   it as a statistical or policy decision.
2. Keep accepting naturally occurring Codex review work, but do not synthesize tasks or prioritize
   filling the 60-task floor when healthy advice delivery and rejected retries point to shaping.
3. Review a separate, non-authorizing diagnostic Shape A+B pilot design before committing a full new
   population. Do not dispatch it until its task order, exact routes, verifier, pricing source,
   sample bounds, stopping rules, and inability to satisfy an effectiveness gate are enforced by a
   reviewed contract under a new root. Pilot rows never enter or reinterpret Shape-B evidence.
4. Start a confirmatory Shape A+B population or implementation cohorts only after the pilot question
   and their full contracts are pre-registered.
5. Keep core `wclass` unchanged. Even a passing advisory campaign permits only human review of an
   explicit companion workflow; it never authorizes automatic routing.
