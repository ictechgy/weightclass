# Paired Net-Token Study Plan

**Goal:** produce the first falsifiable answer to "does weightclass routing reduce
net token use?" by running the offline paired gate in
[`tests/eval/token_benchmark.py`](../tests/eval/token_benchmark.py) against real,
externally collected evidence.

**Why this comes first:** every savings number the router can currently print is a
function of user-supplied relative weights. Fixing the counterfactual baseline made
that number honest, not grounded. Until one paired collection exists, any further
routing work is optimization on top of an unmeasured assumption.

**Status:** revision 2. The Phase 1 pilot has run — 36 real invocations across
two vendors — and its result changed this design. Revision 1 is preserved in git
history; what follows supersedes it.

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

The terminal rule is therefore deliberately unintelligent and identical for both
arms:

> Run the arm's command once. Run the task's acceptance test. If it fails, re-run
> the **exact same command** once more against the same reset fixture. If it still
> fails, the arm is `completed: false` for that pair. Neither arm may change tier,
> model, or effort on the second attempt. Every invocation's tokens are summed.

This measures whether routing lowers the failure rate that drives rework, without
letting harness cleverness leak into the result.

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

36 sealed tasks, four per category, 18 `en` / 18 `ko`. Tier coverage follows the
category mix naturally (`routine` skews `low`, `destructive-work` and `security`
skew `high`).

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

For each candidate task, run **two invocations only** — the vendor CLI pinned to
low effort, and pinned to high — and classify the result:

| low | high | classification |
| --- | --- | --- |
| pass | pass | tier-insensitive; effort only moves cost |
| fail | pass | **tier sensitive** — the tasks this study needs |
| fail | fail | too hard for the fixture or badly specified; fix or drop |
| pass | fail | noise; re-run once, then drop |

Cost is 2 invocations per candidate on one vendor. Calibrating 18 candidates is
36 invocations, the same size as the whole Phase 1 pilot.

The Phase 2 task set should hold a declared, non-zero share of tier-sensitive
tasks, and the share must be recorded in the report. A study over 36
tier-insensitive tasks would produce a confident number that answers nothing.

Writing tasks that fail at low effort and pass at high is its own problem. The
existing 36 are mostly "add function X with behavior Y", which is fully
specified and therefore easy at any tier. Tier-sensitive work tends to look
like: the obvious implementation silently breaks a pre-existing invariant the
acceptance test also checks; the requirement spans modules that must stay
consistent; or the prompt describes a symptom and the cause has to be found.

## Phases

| Phase | Work | Vendor invocations | Approval |
| --- | --- | ---: | --- |
| **0** | fixture, 36 tasks, blind ratings, harness, evidence builder | 0 | done |
| **1** | pilot: 6 tasks × 3 arms × 2 vendors | 36 | done |
| **1b** | difficulty calibration on ~18 candidates, one vendor, two efforts | ~36 | required |
| **2** | comparison P plus control, on the calibrated set | see below | required |
| **3** | comparisons 3 and 4 | deferred | optional |

Phase 2's size is now a real decision rather than a default, because the pilot
measured what each run costs. Codex averaged roughly 35k tokens per invocation
and Claude roughly 500k, so three arms over 36 tasks on two vendors is about
216 invocations and on the order of 58M tokens, dominated by Claude.

Three options, in increasing cost:

| Option | Arms | Runs | Answers |
| --- | --- | ---: | --- |
| **P-only** | `A1`, `A2` | 144 | comparison P on both vendors |
| **P + control** | `A0`, `A1`, `A2` | 216 | P, plus comparisons 1 and 2 at full size |
| **P, Claude only** | `A1`, `A2` | 72 | P where the pilot found signal; Codex stays descriptive |

The pilot already answers comparisons 1 and 2 well enough to act on, and both
pointed the same way on two vendors. Re-running them at 36 pairs buys precision
on a question that is no longer in doubt. **P-only is the recommended option**,
with the pilot cited for the control rather than repeated.

Codex is worth keeping despite its flat result: its variance was 25–32%, so a
null there is itself informative, and dropping the vendor where routing looked
worst would bias the study.

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
- **Raters can read the filesystem.** The first rating batch was discarded after
  `codex exec --sandbox read-only` was confirmed able to print the study's own
  `meta.json`. Raters now run in an empty directory outside the tree, but
  read-only sandboxes still permit reads anywhere; isolation here means nothing
  points at the study, not that reads are impossible. Check rater prose for
  study vocabulary after every batch.
