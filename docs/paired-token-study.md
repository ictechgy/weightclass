# Paired Net-Token Study Plan

**Goal:** produce the first falsifiable answer to "does weightclass routing reduce
net token use?" by running the offline paired gate in
[`tests/eval/token_benchmark.py`](../tests/eval/token_benchmark.py) against real,
externally collected evidence.

**Why this comes first:** every savings number the router can currently print is a
function of user-supplied relative weights. Fixing the counterfactual baseline made
that number honest, not grounded. Until one paired collection exists, any further
routing work is optimization on top of an unmeasured assumption.

**Status: closed at Phase 1b. Phase 2 never ran.** Calibration found **0 of 18
tasks tier-sensitive** against a pre-registered floor of 9, which is the
stopping condition this document set for itself. The answer to the goal question,
within the stated scope, is that this task shape cannot value tier routing at
all: effort moved cost and never changed whether the work got done. See
[Calibration result](#calibration-result-the-stopping-condition-fired). The plan
below is kept as the pre-registration it was — reading it as live work would be
a mistake.

The design below is revision 2 and is now final; the closure above is its
outcome, not a further revision of the plan. Revision 2 was written after the
Phase 1 pilot ran — 36 real invocations across two vendors — because that result
changed the design, most importantly by adding Phase 1b. Revision 1 is preserved
in git history; what follows supersedes it.

## What the scorer forces

These are read from `token_benchmark.py`, not chosen here. The evidence file
declares its own gate, but the scorer takes `max(declared, built-in)` for floors
and `min(declared, built-in)` for the quality margin, so a study cannot be made
easier by declaring a looser gate.

| Constraint | Value | Design consequence |
| --- | --- | --- |
| `MIN_PROMOTION_PAIRS` | 30 | fewer than 30 sealed tasks can never pass |
| `MIN_PROMOTION_NET_TOKEN_SAVINGS` | 0.15 | the savings CI **lower bound** must reach 15% |
| `MAX_PROMOTION_QUALITY_NONINFERIORITY_MARGIN` | 0.05 | quality CI lower bound ≥ −5pp |
| language coverage | all of `en`, `ko` | both languages need ≥1 pair |
| category coverage | all 9 categories | ≥1 pair each; this is what sets the task count |
| tier coverage | all of `low`, `standard`, `high` | ≥1 pair each |
| completion | every pair | both arms must complete on all 30+ pairs |
| provenance | 8 booleans, all true | see the checklist below |
| critical failures | total 0 | one new critical failure fails the gate |
| CI rules | `lower-bound` for both | paired interval, `t = 2.045` (df 29) |

The nine categories are `security`, `privacy`, `data-integrity`,
`destructive-work`, `concurrency`, `reliability`, `performance`, `migration`,
`routine`. Full category coverage at 30 pairs is tight, so this plan uses **36
tasks (9 × 4)** for margin.

## What the pilot changed

Six sealed tasks, three arms, two vendors, all completing on the first attempt.
Full numbers are in the study directory's `PILOT-REPORT.md`. Three results
forced this revision.

**1. The originally pre-registered main comparison answers the wrong question.**
Pinning effort to medium (`A1`) saved more than weightclass routing (`A2`) on
both vendors — +24.7% vs +20.8% on Claude, +4.8% vs +1.2% on Codex — with less
variance in both cases. Head to head, routing was 5.1% worse than the fixed flag
on Claude and 5.5% worse on Codex. Measuring routing against the *vendor
default* at scale would report a saving that a single flag already captures.
The question that matters is whether routing beats that flag.

**2. The mechanism this study exists to price never fired.** Zero rework in 36
runs. The case for cheap routing is that a cheap attempt usually succeeds and
the occasional failure is recovered for less than the expensive attempt would
have cost. On these tasks nothing failed at any effort, so effort moved cost and
nothing else. A task set with no tier-sensitive tasks cannot measure the value
of choosing a tier — only the price of one.

**3. The ratio is compressed by a constant neither arm controls.** Claude totals
ran 5–20× Codex totals for identical tasks, almost entirely
`cache_read_input_tokens` accumulating per turn. Both arms pay it, so the paired
comparison stays valid, but a large shared constant pushes any ratio toward
zero and makes the gate's 15% bar reflect context size as much as routing.

## Decisions taken

| Decision | Choice | Note |
| --- | --- | --- |
| Vendor | **Codex and Claude, as two independent studies** | one `measurement_contract_id` per vendor; evidence files never mixed |
| Fixture repository | **purpose-built synthetic Python project** | lets all nine categories exist by construction and resets cleanly |
| Primary comparison | **`A1` baseline vs `A2` candidate** | added in revision 2; declared before any Phase 2 collection |

## The central validity risk

`net_tokens` is defined as the total across *all* invocations and rework until a
predeclared terminal rule. weightclass runs exactly one foreground child and does
not retry, recover, or supervise, so **the harness performs the rework, and the
harness rule becomes part of what is measured.** A harness that escalates cleverly
flatters whichever arm routes cheaply first.

### The terminal rule, and why it is asymmetric

Revision 1 used one symmetric rule: run the command, and on failure run the
**same** command once more. Revision 2 cannot, and the reason is a contradiction
the review caught.

`completion_passes = both_completed == len(pairs)` — the gate requires **both
arms to complete every pair**. Phase 1b exists to find tasks where low effort
fails and high effort succeeds. Put such a task in the set under a symmetric
retry rule and the routed arm fails twice, reports `completed: false`, and the
completion gate fails the entire study. The tasks that make routing measurable
would be the tasks that make the study unpassable.

The rule is therefore per-arm, and each arm gets the recovery its own strategy
actually offers:

> Run the arm's command once and run the task's acceptance test. On failure,
> reset the fixture and take **one** second invocation:
>
> - `A0` and `A1` re-run the identical command. A fixed setting has no ladder;
>   retrying is what its user would do.
> - `A2` runs the escalation route weightclass names on failure — the tier one
>   step up, at the fingerprint `run --suggest-escalation` prints.
>
> Either way the arm gets exactly two invocations and every token is summed. If
> the second attempt also fails, the arm is `completed: false`.

The asymmetry is the point, not a flaw. `A1` is "pin one flag and retry" and
`A2` is "route, and escalate when the cheap attempt fails". Those are the two
strategies a user actually chooses between, and the pilot showed the first one
currently wins. Giving both arms the same two-invocation budget keeps the
comparison fair on cost while letting each spend it the way its strategy would.

What this does **not** do is let the harness be clever. The escalation route is
not chosen by the harness: it is the route weightclass itself names, at the
fingerprint it prints, one step up and no further. The harness never picks a
tier, never retries a third time, and never decides that a failure was caused by
the tier. That last judgement is explicitly disclaimed in the router's own
output (`failure_cause_diagnosed: false`).

This makes the study depend on the escalation surface, which did not exist when
revision 1 was written. If that surface is not merged, this study cannot include
tier-sensitive tasks and falls back to measuring effort pricing only — which is
what the Phase 1 pilot already did.

## Fixture repository

A single frozen snapshot, held outside this repository.

- Small Python project with deliberate surface for all nine categories: a job
  runner (concurrency), a schema/migration path, a request handler with an
  authorization check, a cache layer, a CSV/ledger writer (data-integrity), a
  cleanup command (destructive-work), a slow query path (performance), a logging
  and formatting layer (routine), and a PII field (privacy).
- Every run starts from an identical state: `git reset --hard <snapshot>` plus
  `git clean -fdx`, or a fresh clone. Both arms of a pair must start byte-identical.
- The fixture's own test suite must be green at the snapshot, so "existing tests
  broken" is an unambiguous critical failure.

## Task set

36 sealed tasks, four per category, 18 `en` / 18 `ko`.

Tier coverage is a hard gate requirement, so it is measured rather than assumed.
The rated set comes out at **13 `low` / 12 `standard` / 11 `high`**, with all
three tiers and both languages present after consensus filtering. Re-measure
whenever a task is added or replaced; a set that loses its only `low` task fails
the coverage gate no matter how good the token result is.

Each task ships four things:

1. **Prompt text.** Written fresh for this study. It must not come from
   `tests/eval/corpus.json` — that fixture is public and visible, which would
   defeat `fresh_blind_tasks`.
2. **`expected_tier`.** Assigned by three raters working independently, none of
   whom has seen router output for that task. This mirrors how the existing
   corpus was rated.
3. **A hidden acceptance test.** Written before any collection. This is the
   `quality_pass` instrument — see below.
4. **Critical-failure predicate.** Concretely: an unrelated file deleted or
   rewritten, a pre-existing fixture test broken, the acceptance test itself
   modified, or a secret written into the tree.

### Quality measurement

`quality_pass` is decided by the task's pre-written acceptance test, not by a
human or a model reading the diff. This is the cheapest instrument that honestly
satisfies `independent_quality_review`: the rubric is authored before collection
and evaluated mechanically, so it cannot know which arm produced the diff. A
post-hoc human or model judgement would be neither blind nor independent of the
system under test.

## Arms

Per vendor, three frozen configurations. Each gets a
`sha256:` configuration fingerprint over its exact command, settings, and version,
recorded once and reused for every pair.

| Arm | Definition |
| --- | --- |
| `A0` provider-direct default | the vendor CLI invoked directly, no weightclass, vendor defaults |
| `A1` provider-direct fixed medium | same, with reasoning effort pinned to medium |
| `A2` weightclass balanced route | `wclass classify` then `wclass run` with the built-in balanced policy |

- **Comparison P (primary, new in revision 2):** `A1` baseline vs `A2` candidate.
  Does routing beat the trivial alternative of pinning one flag? This is a
  promotion question, and the gate is exactly the right shape for it: adopt
  routing only if it saves at least 15% over the thing it would replace.
- **Comparison 1 (control):** `A0` baseline vs `A1` candidate. Establishes that
  the apparatus works and prices the vendor default.
- **Comparison 2:** `A0` baseline vs `A2` candidate. The originally primary
  comparison, kept for the record and demoted.

Comparison P was chosen after the pilot. That is legitimate — informing the
design is what a pilot is for — but it is only legitimate under two conditions,
both of which hold: it is declared here **before** any Phase 2 collection, and
Phase 2 collects fresh runs rather than reusing the pilot's six pairs.

Pre-registered but still deferred: `A0` vs a schema-1 policy whose standard
command omits the effort override, and `A0` vs `--ask-vendor` followed by `run`
with both invocations counted.

### Pre-registered stratified analysis

Report savings split by whether routing agreed with the rated tier:

| Stratum | Meaning |
| --- | --- |
| matched | routed tier equals `expected_tier` |
| over-routed | routed above the rated tier |
| under-routed | routed below the rated tier |

The pilot found agreement on 1 of 6 pairs, with over-routing costing money
directly and under-routing paying off twice by chance. Whether that pattern
holds at 36 pairs is the most useful thing this study can produce, because it
separates "routing is a bad idea" from "this classifier is wrong". Declare the
strata now so the split is not chosen after seeing the totals.

### Reporting absolute differences

Report the paired absolute token difference and its interval beside every ratio.
A large constant present in both arms — `cache_read_input_tokens` on Claude is
most of the total — drags every ratio toward zero without changing the
difference. The gate is a ratio and stays a ratio; this is an addition to the
study's own reporting, not a change to the gate.

## Token normalization contract

Frozen in Phase 1 by probe, not by assumption.

- **Codex.** The cumulative count `codex exec` writes to stderr as
  `tokens used\n2,231`.
- **Claude.** `usage.input_tokens + output_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`, and nothing else. The
  same payload repeats those numbers under `cache_creation.*`, inside
  `iterations[]`, and as `output_tokens_details.thinking_tokens`, which is a
  subset of `output_tokens`; adding any of them double counts.
  `total_cost_usd` is money rather than tokens and is not used.

Claude totals ran 5–20× Codex totals for identical tasks. That is not a model
comparison — it is `cache_read_input_tokens` accruing on every turn. The paired
comparison is unaffected because it never crosses vendors, but `net_tokens` is
dominated by context size times turn count, which is why revision 2 also reports
absolute differences.

The harness records the four Claude fields separately from Phase 1b onward.
Phase 1 kept only the sums and cannot be decomposed after the fact.

### Declared deviation

The built-in Claude route prints no usage anywhere, so `A2` could not be
measured at all. All three Claude arms therefore carry `--output-format json`,
and `A2` uses a policy that is the built-in command with that one flag added.
`wclass route` reports it as `unqualified_custom` and its fingerprint differs
from the built-in. Both facts belong in the final report. Codex needs no
deviation.

## Phase 1b: difficulty calibration

Added in revision 2, and the most important change here. Phase 1 recorded zero
rework in 36 runs, which means not one task in the pilot set was **tier
sensitive**: low effort finished it just as well as high. A task set like that
can price effort but cannot value routing, because routing's whole claim is that
picking the right tier matters.

Phase 2 must not start until the task set contains tasks where the tier changes
the outcome. Which tasks those are cannot be guessed; it has to be measured.

Tier sensitivity has to be defined over **the step the routed arm will actually
take**, not over the ends of the ladder. `A2` escalates exactly one tier. A task
where pinned `low` fails and pinned `high` passes looks tier sensitive on a
two-point test, but if the middle tier also fails, the routed arm fails,
escalates one step, fails again, and reports `completed: false` — putting the
study straight back into the contradiction the terminal rule was rewritten to
remove.

Classification is local and costs nothing, so the pair to test is knowable
before any vendor is invoked:

1. Run `wclass classify` on the candidate. No vendor is involved. Call the
   result `T`.
2. Run the candidate pinned at `T`, then pinned at the tier `T` escalates to.
3. Classify the outcome:

| at `T` | at the escalation tier | classification |
| --- | --- | --- |
| pass | not needed | tier-insensitive; usable, but exercises no recovery |
| fail | pass | **tier sensitive** — the tasks this study needs |
| fail | fail | drop; one escalation step cannot recover it, so it would fail the completion gate |
| pass | fail | noise; re-run once, then drop |

`A1` has to be covered too, and it usually is for free. `A1` is pinned to
`medium`, which is the same tier as `standard`. When `T` is `low` the pair
already includes `standard`; when `T` is `standard` the pair starts there. Only
`T` = `high` leaves `A1` untested, and that case needs a third invocation at
`medium`. Skipping it would let a task through where the routed arm completes
and the fixed arm does not, which fails the completion gate for the whole study
just as surely.

Cost is 2 invocations per candidate, or 3 for the `T` = `high` case. Calibrating
18 candidates is roughly 36-40 invocations, about the size of the whole Phase 1
pilot.

Phase 2 must not start with fewer than **9 tier-sensitive tasks out of 36**, and
the confirmed count must be recorded in the report. "A declared, non-zero share"
was the first wording and it is unfalsifiable — one such task out of 36 would
satisfy it while leaving the study exactly as uninformative as the pilot. A
quarter of the set is a floor chosen so the stratified analysis has enough
tier-sensitive pairs to say anything; it is not a claim that a quarter is the
right proportion in real work.

If calibration cannot find 9, that is itself the finding: on work of this shape,
the tier does not change the outcome often enough for routing to have a value to
measure. Report it and stop, rather than lowering the floor to proceed.

Writing tasks that fail at low effort and pass at high is its own problem. The
existing 36 are mostly "add function X with behavior Y", which is fully
specified and therefore easy at any tier. Tier-sensitive work tends to look
like: the obvious implementation silently breaks a pre-existing invariant the
acceptance test also checks; the requirement spans modules that must stay
consistent; or the prompt describes a symptom and the cause has to be found.

### Calibration result: the stopping condition fired

Calibration ran on 18 candidates, using 23 invocations and 981,613 tokens. The
candidates were drawn from the 30 tasks not already used in the Phase 1 pilot and
weighted to the hard end: 9 of the 11 tasks the blind raters rated `high` — the
other two were pilot tasks — plus 9 rated `standard`. Unlike the other
phases, that count is not a product: the second run is conditional. Thirteen
tasks passed at the routed tier and stopped after one run; two routed to `high`
took the mandatory extra run a tier down; three failed and took an escalation
run. 13 + (2 × 2) + (3 × 2) = 23. The planned estimate of ~36 assumed every
candidate would need its pair.

| verdict | count |
| --- | ---: |
| tier-insensitive: passed at the routed tier | 15 |
| drop-unrecoverable: failed at the routed tier *and* one step up | 3 |
| **tier sensitive** | **0** |

Both labels describe the *run*, not the task, and neither is as strong as it
sounds. `tier-insensitive` covers 15 tasks but only 2 of them were actually
observed at two tiers; the other 13 passed at the routed tier and stopped there,
so for those the label records "no escalation was needed," not a demonstrated
insensitivity. `drop-unrecoverable` means only that one escalation step did not
recover the run — as the next paragraph shows, two of the three failed because
of a defect in this study's acceptance tests rather than anything about
difficulty or tier. What no label in this table can hide is the empty row: the
comparison that would have justified routing was never observed once.

Zero against a floor of nine. The floor is defined over all 36 tasks while only
18 were calibrated, so strictly the other 18 might still hold the nine. Nothing
here proves they do not. But the calibrated 18 were selected as the *most
likely* to be tier-sensitive — 9 rated `high` and 9 rated `standard` — and the
untested remainder is 13 `low`, 3 `standard`, and 2 `high`. Even those two
`high` tasks, `p05` and `p09`, are not unknowns: both ran in the Phase 1 pilot
and both passed on the first attempt. Finding nine tier-sensitive tasks in that
tail, after none were found among the hardest eighteen, is not a bet worth 36
more invocations to settle. Per the rule above the study stops here rather than
lowering the floor, and **Phase 2 was never started.**

The three failures are not hidden tier sensitivity: they failed at *both* tiers,
so they were never candidates. Two were investigated and the cause was a defect
in the study's own acceptance tests, which over-specify the interface for tasks
that ask for a new API. In `p27` the agent caught per-job exceptions under a
lock, kept the pool running, and re-raised the first failure from `drain()`; the
test wrapped `drain()` so that a raise skipped its completion signal and was
misreported as a hang. In `p13` the agent exposed the retry count as a callback
while the test looked only for a tuple return or a function attribute. Had those
tests been correct, the tasks would most likely have passed at the routed tier —
tier-*insensitive*, not sensitive. The count stays 0.

Two tasks were routed to `high`. That is the one case where the calibration rule
above mandates an extra run a tier down, to cover `A1`; since both passed at
`high`, no escalation run was needed and that extra run was their second. **Both
passed a tier down as well.** (`A1` is pinned to the vendor effort `medium`,
which is what the `standard` tier maps to; the tier name is used here for
consistency.) The two tasks where the router spent the most are the two where the
cheaper tier is confirmed sufficient.

Of the nine calibrated tasks the blind raters judged `high`, seven were routed
`standard`; five of those passed there and two are the concurrency failures
discussed above.
So even human-rated difficulty did not predict a need for effort.

Combined with the pilot, where a pinned `medium` came out ahead of routing on
both vendors by point estimate (decisively only on Claude; Codex was a wide
null), the finding is that on work of this shape **routing up bought nothing**:
every step above the routed tier was spent without changing an outcome, which is
*why* the pilot came out the way it did.

Be careful not to stretch that into "the cheapest tier always suffices," which
this design cannot support. Calibration runs at the routed tier and escalates
*upward*; it never probes downward except for the two `high`-routed tasks, and
**no task was ever run at `low`.** The thirteen tasks routed `standard` and
passed might well have passed a tier down, but that was not measured. What is
measured is that raising the tier never rescued anything — which makes routing
up a cost with no demonstrated benefit here, and leaves the value of routing
down an open question.

State the scope honestly: one vendor for calibration (Codex, chosen because its
runs cost roughly a fifteenth of Claude's), one synthetic fixture, and small
well-specified maintenance tasks. This does not show that effort never matters.
It shows that this shape of work cannot be used to value tier routing, and that
the fixture is probably too small to host work hard enough to try — almost
anything expressible against nine short modules is within reach of low effort.

Full evidence, including the per-task verdict table, lives in the separate study
repository alongside the fixture and harness; it is deliberately not vendored
here.

## Phases

| Phase | Work | Vendor invocations | Approval |
| --- | --- | ---: | --- |
| **0** | fixture, 36 tasks, blind ratings, harness, evidence builder | 0 | done |
| **1** | pilot: 6 tasks × 3 arms × 2 vendors | 36 | done |
| **1b** | difficulty calibration on 18 candidates, one vendor | 23 | done — 0 tier-sensitive |
| **2** | comparison P plus control, on the calibrated set | 0 | **not started; blocked by 1b** |
| **3** | comparisons 3 and 4 | deferred | dropped with the study |

Phase 2's sizing below is retained as a record of the decision that was prepared,
not as a plan that is still live.

Phase 2's size was to have been a real decision rather than a default, because
the pilot had measured what each run costs. Codex averaged roughly 35k tokens
per invocation and Claude roughly 500k, so three arms over 36 tasks on two
vendors is about 216 invocations and on the order of 58M tokens, dominated by
Claude.

Three options, in increasing cost:

| Option | Arms | Runs | Answers |
| --- | --- | ---: | --- |
| **P-only** | `A1`, `A2` | 144 | comparison P on both vendors |
| **P + control** | `A0`, `A1`, `A2` | 216 | P, plus comparisons 1 and 2 at full size |
| **P, Claude only** | `A1`, `A2` | 72 | P where the pilot found signal; Codex stays descriptive |

The pilot already answered comparisons 1 and 2 well enough to act on, and both
pointed the same way on two vendors. Re-running them at 36 pairs would have
bought precision on a question that was no longer in doubt, so **P-only was the
recommended option**, with the pilot cited for the control rather than repeated.

Codex would have been kept despite its flat result: its variance was 25–32%, so
a null there is itself informative, and dropping the vendor where routing looked
worst would have biased the study.

## Provenance checklist

All eight must be true, and each must be true because of a procedure, not an
assertion.

| Field | How this study satisfies it |
| --- | --- |
| `fresh_blind_tasks` | tasks written for this study; not from the public fixture; raters see no router output |
| `same_sealed_tasks` | both arms run the identical prompt set |
| `same_provider_runtime_model` | one vendor, runtime, and model per study; CLI versions recorded in the fingerprint |
| `counterbalanced_order` | per pair, alternate which arm runs first |
| `all_attempts_included` | harness sums every invocation, including terminal-rule reruns and failures |
| `ids_not_task_derived` | pair ids are sequential, not hashes or slugs of task text |
| `outside_repository_custody` | evidence JSON is written and kept outside this repository |
| `independent_quality_review` | pre-written acceptance tests, evaluated mechanically |

## Running the scorer

```sh
PYTHONPATH=src python3 tests/eval/token_benchmark.py \
  --evidence /outside/the/repository/paired-token-evidence-<vendor>-<comparison>.json
```

One evidence file per (vendor, comparison). The scorer never invokes `wclass`, a
vendor CLI, or the network; collection is a separate, explicitly authorized step
owned by the evaluator.

## What needs approval

Collection makes network, disclosure, quota, and billing changes, so it sits
outside the offline scorer and outside this plan's authority. Phase 0 and the
Phase 1 pilot are done; what follows still needs a decision.

- **Phase 1b**, roughly 36 invocations on one vendor, to find which tasks are
  tier sensitive.
- **Phase 2**, 144 invocations under the recommended P-only option, or 216 with
  the control repeated at full size.
- Task text is synthetic and carries no private content, but it still leaves the
  machine. Agents write only to the fixture repository, which lives outside this
  repository so no run can touch weightclass source.

## Known threats to validity

- **Harness-performed rework.** weightclass never retries, so the harness does.
  The rule is deliberately dumb and symmetric, but it is still an assumption
  about how a user would retry. Phase 1 never exercised it at all.
- **Tier-insensitive tasks.** The reason for Phase 1b. Until the set contains
  tasks where the tier changes the outcome, this study measures the price of
  effort and calls it the value of routing.
- **Synthetic tasks.** A purpose-built fixture makes coverage achievable and
  resets clean, at the cost of realism. Results generalize to tasks like these.
- **One model per vendor.** Model behaviour changes; the result is a snapshot
  bound to the recorded configuration fingerprints.
- **Acceptance tests define "done".** Authored before collection so the bar
  cannot be tuned after seeing results. Every one was verified to fail on the
  pristine fixture, which caught two tasks asserting bugs that did not exist.
- **A ratio bar against a large shared constant.** The 15% floor is a promotion
  bar, not the question. Report the point estimate, the interval, and the
  absolute difference whether the gate passes or fails.
- **Collection agents can read the filesystem too.** The arms run real coding
  agents. `codex exec --sandbox workspace-write` confines *writes* to the working
  directory but not reads, and the Claude arms run with `acceptEdits`. Nothing in
  this design stops a collection run from reading host files and sending them to
  the vendor. The fixture is synthetic and lives outside this repository, so what
  is at risk is the host, not the study's own data. Treat that as the reason to
  run collection under a dedicated account or container if anything sensitive
  shares the machine; this plan does not provide that isolation and should not be
  read as if it did.
- **Raters can read the filesystem.** The first rating batch was discarded after
  `codex exec --sandbox read-only` was confirmed able to print the study's own
  `meta.json`. Raters now run in an empty directory outside the tree, but
  read-only sandboxes still permit reads anywhere; isolation here means nothing
  points at the study, not that reads are impossible. Check rater prose for
  study vocabulary after every batch.
