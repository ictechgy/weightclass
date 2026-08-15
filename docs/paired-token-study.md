# Paired Net-Token Study Plan

**Goal:** produce the first falsifiable answer to "does weightclass routing reduce
net token use?" by running the offline paired gate in
[`tests/eval/token_benchmark.py`](../tests/eval/token_benchmark.py) against real,
externally collected evidence.

**Why this comes first:** every savings number the router can currently print is a
function of user-supplied relative weights. Fixing the counterfactual baseline made
that number honest, not grounded. Until one paired collection exists, any further
routing work is optimization on top of an unmeasured assumption.

**Status:** planning. No collection has been authorized or performed.

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

## Decisions taken

| Decision | Choice | Note |
| --- | --- | --- |
| Vendor | **Codex and Claude, as two independent studies** | one `measurement_contract_id` per vendor; evidence files never mixed |
| Fixture repository | **purpose-built synthetic Python project** | lets all nine categories exist by construction and resets cleanly |
| Phase 2 comparisons | **control + main only** | comparisons 3 and 4 are pre-registered but deferred |

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

- **Comparison 1 (control):** `A0` baseline vs `A1` candidate. Answers whether the
  measurement apparatus is sound and what pinning effort alone costs. Without this,
  comparison 2 is uninterpretable.
- **Comparison 2 (main):** `A0` baseline vs `A2` candidate. Answers the actual
  question.

Pre-registered but deferred to a later phase: `A0` vs a schema-1 policy whose
standard command omits the effort override, and `A0` vs `--ask-vendor` followed by
`run` with both invocations counted.

## Token normalization contract

The scorer accepts a single `net_tokens` integer per arm and deliberately refuses
to infer how to sum provider fields. Phase 0 must pin down, per vendor, the exact
field list and record it under the `measurement_contract_id`:

- **Codex.** `codex exec` reports cumulative token usage on stderr. Confirm it is
  cumulative across turns within one invocation, then declare that single number.
- **Claude.** `claude -p --output-format json` returns a usage object. Declare an
  explicit field list; do not add fields that may overlap (cache-read and input
  counts in particular). This is a raw-token comparison, not a price estimate.

Freeze the contract before Phase 1 and never change it mid-study. If it must
change, the study restarts.

## Phases

| Phase | Work | Vendor invocations | Approval |
| --- | --- | ---: | --- |
| **0** | fixture repo, 36 tasks + acceptance tests, tier ratings, terminal/critical rules, collection harness, token contract, scorer smoke against synthetic evidence | **0** | not required |
| **1** | pilot: 6 tasks × 3 arms × 2 vendors | **36** | required |
| **2** | full: 36 tasks × 3 arms × 2 vendors | **216** | required |
| **3** | comparisons 3 and 4 | +144 | optional, decided later |

Phase 1 **cannot pass the gate** — six pairs is below the floor of 30 — and is not
meant to. Its purpose is to validate the token contract, prove the harness counts
rework correctly, calibrate task difficulty, and produce the first real measurement
of what Phase 2 will cost. Phase 2 must not start before Phase 1 has done that;
there is currently no basis for estimating total token spend.

Terminal-rule reruns add invocations on top of the counts above, bounded at 2× in
the worst case.

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

## What needs approval before Phase 1

Collection makes network, disclosure, quota, and billing changes, so it sits
outside the offline scorer and outside this plan's authority.

- Full task text is sent to the vendor. The tasks are synthetic and contain no
  private content, but this is still outbound disclosure.
- 36 (Phase 1) then 216 (Phase 2) agent invocations per the table above, against
  whatever subscription or metered account the CLIs are logged into.
- Agents write to the fixture repository. It must live outside this repository so
  no run can touch weightclass source or the user's other work.

## Known threats to validity

- **Harness-performed rework.** Addressed by the deliberately dumb terminal rule,
  but the rule is still an assumption about how a user would retry.
- **Synthetic tasks.** A purpose-built fixture makes coverage achievable and
  resets clean, at the cost of realism. The result generalizes to tasks like these,
  not to arbitrary production work.
- **One model per vendor.** Model behaviour changes; the result is a snapshot bound
  to the recorded configuration fingerprints.
- **Acceptance tests define "done".** A task whose test is too lenient rewards a
  lazy arm; too strict punishes both equally. Tests are authored before collection
  precisely so this bias cannot be tuned after seeing results.
- **The 15% floor is a promotion bar, not the question.** A result of, say, 6%
  savings fails the gate while still being a real finding. Report the point
  estimate and interval regardless of pass or fail.
