# Classifier evaluation corpus

`corpus.json` holds the 40 tasks behind the historical accuracy figures in
`README.md`, so those numbers can be re-derived instead of taken on faith. It
is a public regression fixture, not a held-out benchmark. A score against it
must not be presented as evidence of general routing accuracy or used as a
release gate for classifier tuning.

## What is in it

Each entry has the task text, three independent difficulty ratings, their
consensus, and the vendor tier recorded when the figures were first measured.

- The tasks are **synthetic** — written for this benchmark, not captured from
  anyone's use of the tool. `AGENTS.md` restricts *runtime* task content; these
  are repository fixtures, the same category as the task strings already in
  `tests/test_classification.py`.
- The three ratings were produced independently, by raters that never saw any
  classifier output. They agreed unanimously on 39 of the 40 tasks, which is the
  reason the consensus column is worth measuring against at all.
- Consensus distribution: 12 `low`, 13 `standard`, 15 `high`.

## Re-running it

`score.py` compares the local classifier against the consensus. It has no
network or vendor-CLI mode:

```sh
PYTHONPATH=src python3 tests/eval/score.py
```

The report labels this input as the public regression fixture and prints only
aggregate metrics. It includes a confusion matrix, high-tier recall,
over-routing, Wilson 95% confidence intervals, and available language and
category slices.

The current checkout scores the local classifier at 17/40 on that fixture.
The historical 15/40 local and 33/40 recorded-vendor figures predate the later
outcome-pattern refinement. The default command does not re-run or reproduce a
vendor result.

## Offline paired net-token gate

Routing to an effort label is not evidence of lower total token use. Measure a
frozen candidate against a frozen baseline on the same fresh sealed tasks,
then score evaluator-supplied evidence without giving this repository provider
credentials or a usage-payload parser:

```sh
PYTHONPATH=src python3 tests/eval/token_benchmark.py \
  --evidence /outside/the/repository/paired-token-evidence.json
```

The scorer never invokes `wclass`, a vendor CLI, or the network. Collection is
a separate, explicitly authorized step owned by the evaluator. One pair is one
sealed task. For each arm, `net_tokens` is the total measured under one frozen,
reviewed measurement contract, including all invocations and rework until the
same predeclared terminal rule. `invocations` records how many calls contributed
to that total. Do not add potentially overlapping provider fields and ask the
scorer to infer how to sum them; normalize them externally under the opaque
`measurement_contract_id`. This is a raw-token comparison, not a price or
billing estimate.

Pre-register at least these pairwise comparisons on the same task set:

1. provider-direct default versus provider-direct fixed medium, as a control;
2. provider-direct default versus the current balanced local route;
3. provider-direct default versus the experimental schema-1 policy whose
   standard command omits the effort override; and
4. provider-direct default versus `--ask-vendor` followed by `run`, with both
   full-task invocations and any rework included in the candidate total.

Run the scorer once per candidate. Keep the provider, runtime, model, other
command settings, terminal rule, and blind quality rubric fixed; counterbalance
execution order. The direct provider and vendor-triage collection steps can
make network, disclosure, quota, and billing changes and therefore require
approval outside this offline scorer.

The evidence object has exact fields. Unknown, missing, duplicate, malformed,
or value-invalid fields fail with `invalid evidence` and no supplied value.
Pair IDs are reviewed opaque identifiers assigned independently of task text
and are never emitted. The safe experiment IDs, measurement-contract ID, and
two exact configuration fingerprints are emitted as the review binding; they
must contain no task-derived value. Evidence must be a nonsymlink regular file
of at most 1 MiB. This two-pair object demonstrates the schema but necessarily
scores `no-go`, because promotion requires at least 30 pairs:

```json
{
  "schema_version": 1,
  "baseline_id": "direct-default-v1",
  "candidate_id": "experimental-efficient-v1",
  "measurement_contract_id": "provider-usage-normalization-v1",
  "baseline_configuration_fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "candidate_configuration_fingerprint": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "gate": {
    "minimum_pairs": 2,
    "minimum_net_token_savings": 0.15,
    "maximum_savings_ci_width": 0.8,
    "quality_noninferiority_margin": 0.1,
    "savings_ci_rule": "lower-bound",
    "quality_ci_rule": "lower-bound",
    "required_languages": ["en", "ko"],
    "required_categories": ["security", "routine"]
  },
  "provenance": {
    "fresh_blind_tasks": true,
    "same_sealed_tasks": true,
    "same_provider_runtime_model": true,
    "counterbalanced_order": true,
    "all_attempts_included": true,
    "ids_not_task_derived": true,
    "outside_repository_custody": true,
    "independent_quality_review": true
  },
  "pairs": [
    {
      "id": "sealed-case-001",
      "language": "en",
      "category": "security",
      "expected_tier": "high",
      "baseline": {"net_tokens": 100, "invocations": 1, "completed": true, "quality_pass": true, "critical_failure": false},
      "candidate": {"net_tokens": 60, "invocations": 1, "completed": true, "quality_pass": true, "critical_failure": false}
    },
    {
      "id": "sealed-case-002",
      "language": "ko",
      "category": "routine",
      "expected_tier": "low",
      "baseline": {"net_tokens": 100, "invocations": 1, "completed": true, "quality_pass": true, "critical_failure": false},
      "candidate": {"net_tokens": 70, "invocations": 1, "completed": true, "quality_pass": true, "critical_failure": false}
    }
  ]
}
```

`completed`, `quality_pass`, and `critical_failure` come from the independent
blind completion review, not from a vendor exit code. A non-completed arm
cannot claim `quality_pass`. The aggregate report includes token and invocation
totals, fixed slice coverage, completion and quality counts, new critical
failures, and the ratio-of-sums estimate
`1 - candidate_tokens / baseline_tokens`. Sampling uncertainty uses a
deterministic paired jackknife interval with the conservative 30-pair critical
value 2.045; zero-token baseline pairs make savings unavailable. Quality
non-inferiority uses the paired binary pass-rate difference. Its 95% interval
combines Bonferroni-adjusted exact Clopper-Pearson bounds for matched
improvements and regressions, so a perfect tie remains uncertain at small
sample sizes without being rejected forever. Zero-variance savings evidence is
still insufficient.

A machine-readable `go` requires every predeclared gate plus fixed floors that
the evidence cannot weaken: at least 30 pairs; both languages, all nine fixed
categories, and all three tiers present; savings lower bound at least 15%; and
quality lower bound no worse than -5%. Requested thresholds may be stricter.
Both arms must complete on every pair, the savings interval must fit the
requested maximum width, no candidate may introduce a critical failure, both
intervals must be nondegenerate, and every provenance assertion must be true.
A valid `no-go` still exits zero; exit 2 means the evidence was invalid. The
normal/jackknife intervals remain approximations, so independent review still
owns statistical design, confidence sufficiency, and provenance.

Output is deterministic aggregate-only JSON. It contains the explicit safe
review binding and aggregate metrics, but no task, task hash, pair ID, per-pair
row, or raw evidence. The scorer trusts rather than proves the provenance
booleans and does not verify that the supplied path is actually outside the
repository. Keep sealed task files and token evidence out of the checkout,
record the bound aggregate decision, and delete or return the temporary inputs
under the evaluation agreement.

No candidate has supplied independent paired evidence in this repository, so
there is currently no basis to add an `efficient` posture or change the built-in
`standard=medium` commands. Even after a gate passes, first keep the behavior in
an explicitly reviewed schema-1 policy; changing built-ins or schema 2 is a
separate compatibility and release decision.

## Offline paired estimated-cost gate

Raw-token savings and estimated provider cost are different objectives. Use a
separate scorer when the evaluation contract supplies a nonnegative integer
`estimated_cost_units` for every arm:

```sh
PYTHONPATH=src python3 tests/eval/cost_benchmark.py \
  --evidence /outside/the/repository/paired-cost-evidence.json
```

The cost scorer has the same bounded regular-file input, aggregate-only output,
30-pair floor, fixed slices, confidence, completion, quality, critical-failure,
and configuration-binding rules as the token scorer. Its arm objects replace
`net_tokens` with `estimated_cost_units`, and its gate replaces
`minimum_net_token_savings` with `minimum_estimated_cost_savings`. The opaque
measurement contract defines the integer unit and rounding rule. For example,
an evaluator may normalize a provider CLI's task-free estimated-cost field to a
fixed sub-dollar integer unit before scoring. Do not submit floating-point
currency values.

The scorer never downloads a price table, multiplies tokens by a price, or
claims the result is an actual bill. Its report labels the metric
`externally_normalized_estimated_cost`, sets `pricing_inferred_by_scorer` and
`actual_billing_claimed` to false, and omits pair rows and token metric names.
Subscription quota, marginal charge, invoice credits, and provider pricing
semantics remain outside weightclass.

Cost evidence may intentionally compare different reviewed models or efforts.
Its exact provenance therefore requires `same_provider_runtime`,
`only_reviewed_configuration_dimensions_changed`, and
`estimated_cost_contract_reviewed` instead of claiming that the model is the
same. The two configuration fingerprints bind the predeclared difference; all
other provenance fields retain their token-gate meaning.

## Sanitized provider-export usage gate

Provider billing and subscription exports can contain account identifiers or
other sensitive metadata. Never pass a raw export to weightclass or place it in
the repository. An evaluator may instead normalize only the task-free integer
usage totals under a reviewed contract, retain the source export outside the
repository, and score the sanitized paired evidence offline:

```sh
PYTHONPATH=src python3 tests/eval/provider_usage_benchmark.py \
  --evidence /outside/the/repository/sanitized-provider-usage.json
```

This scorer accepts an exact `objective` of `metered_cost` or
`subscription_quota`. Arm objects use `usage_units`; the gate uses
`minimum_usage_savings`. The remaining pair, binding, coverage, completion,
quality, critical-failure, confidence, and bounded-file rules are the same as
the token gate. Provenance additionally asserts that the provider export and
normalization contract were reviewed, only the reviewed configuration
dimensions changed, and the submitted evidence contains neither task data nor
account identifiers. These are evaluator assertions, not facts verified by the
scorer.

`metered_cost` requires `fixed_subscription_charge: false`. A passing result is
labeled `cost_opt_in` and may be used to prepare the separate reviewed cost
profile and qualification card; it does not create those documents or authorize
execution. `subscription_quota` requires `fixed_subscription_charge: true`.
Even when its statistical decision is `go`, its promotion scope is
`capacity_only`, `eligible_for_cost_recommendation` is false, and
`monthly_bill_reduction_claimed` is false. The scorer never fetches prices,
reads credentials, parses provider-specific exports, or verifies provider
billing assertions.

A nine-pair canary remains a valid aggregate diagnostic but necessarily scores
`no-go` against the fixed 30-pair promotion floor and full slice requirements.
Expand only a promising, quality-safe exact configuration. The aggregate-only
output omits task text, pair rows, and pair identifiers.

An exploratory Claude 2.1.228 diagnostic compared default-model medium with
explicit Haiku low on six low-risk disposable editing fixtures. Both arms
passed 6/6 automated checks and did not change protected files. Haiku used
243,851 raw tokens versus 147,771, but its CLI-reported estimated cost was
$0.0960305 versus $0.3951410, a 75.70% reduction. This reused one public task,
covered only low-risk English editing, used an automated rather than blind
independent rubric, and measured a JSON-output evaluation command rather than a
production-compatible route. It is diagnostic `no-go` evidence, not a basis to
change a built-in or publish an opt-in route.

The follow-up router canary freezes those two exact low-tier commands in
`claude_cost_baseline_policy.json` and
`../../src/weightclass/examples/claude_cost_focused_policy.json`. They are schema-1,
same-vendor, stdin-delivery policies. Only the low route differs: the baseline
requests medium effort with the default model, while the candidate requests
Haiku with low effort. Their standard and high routes are identical. Both low
routes use `--output-format json` solely so an external authorized evaluator
can normalize task-free usage and cost fields; JSON is the child process's
public output and weightclass does not parse it.

One disposable public-fixture canary exercised each policy through the real
`wclass route` fingerprint acknowledgement and `wclass run` path. Both edits
passed the automated file rubric. The candidate used 27,867 raw tokens versus
23,133 (+20.46%) while the CLI-reported estimated cost was $0.0106916 versus
$0.049921 (-78.58%). This single, reused, English low-risk task is only a
transport/configuration check. It does not satisfy the 30-pair, fixed-slice,
blind-quality, confidence, or provenance gates and remains `no-go`.

A subsequent fresh 30-pair run covered both languages, all nine categories,
and all three tiers. It executed 60 counterbalanced `route`/`run` arms; all 60
returned complete usage, both arms passed 30/30 arm-blind quality reviews, and
neither introduced a critical failure. Baseline/medium used 643,578 raw tokens
and reported $1.9441010; the candidate used 688,268 raw tokens (+6.94%) and
reported $1.5257985 (-21.52%). The estimated-cost 95% interval was 8.08% to
34.95%, wider than the predeclared 20% maximum and with its lower bound below
the required 15%. Thirty perfectly matched quality passes also have an exact
lower difference bound below -5%. The scored decision therefore remains
`no-go`. The synthetic tasks, responses, per-pair evidence, and disposable
workspaces were not retained; only these aggregate facts remain.

A smaller follow-up candidate retained Haiku/low, changed standard to
Sonnet/medium, and left high unchanged. Its nine-pair mixed-tier canary reduced
aggregate reported cost by 27.29% while increasing raw tokens by 19.35%.
However, arm-blind standard quality was 2/3 versus baseline 3/3 and standard
raw tokens rose 58.29%. That candidate was rejected immediately; its policy was
not retained and no larger run was started.

An effort-only follow-up kept the default standard model but changed its effort
from medium to low. Both arms passed 9/9 blind checks, yet total reported cost
fell only 14.08%—below the fixed 15% floor—and raw tokens rose 6.49%. The
standard slice itself reported 11.08% higher cost for 0.59% fewer tokens. This
candidate was also discarded. Across both canaries, the repeatable cost signal
remained confined to the existing Haiku/low route.

The retained low-only candidate was then evaluated on a fresh balanced
150-pair corpus with 300 complete counterbalanced arms. Candidate blind quality
was 143/150 versus baseline 142/150; its exact paired 95% interval was -2.41%
to +3.66%, with no new candidate critical failure. The candidate used
3,604,732 raw tokens versus 3,359,583 (+7.30%) and reported $8.353250 versus
$10.133820 (-17.57%). Its estimated-cost 95% interval was 12.30% to 22.84%, so
only the required 15% lower-bound gate failed. The scorer returned `no-go`.
This balanced policy-level result does not establish the low route's cost
effect directly because unchanged standard/high arms contribute measurement
noise. A separately predeclared target-tier experiment may oversample low while
retaining all fixed control slices, but any passing result can authorize only
review of the opt-in low route—not a built-in or whole-policy claim.

That low-target qualification was predeclared at 90 fresh pairs: 72 low, nine
standard controls, and nine high controls, with en/ko and all nine categories
still covered. Its first attempt was invalidated after 45 pairs when a new task
failed the pre-arm protected-file validator; none of those rows were reused.
The clean restart completed all 180 counterbalanced arms. Blind quality tied
88/90, with two matched critical failures and no new candidate critical
failure. Baseline reported $4.8391435 and 1,803,839 raw tokens; the candidate
reported $2.1708828 (-55.14%) and 2,066,975 tokens (+14.59%). The estimated
cost-savings 95% interval was 46.28% to 64.00%, the exact quality-difference
interval was -4.02% to +4.02%, and every machine gate passed. This `go`
authorizes review of the exact cost-focused low-route example only. It is not
evidence of token savings, a provider bill, a built-in default, standard/high
changes, or an `efficient` posture.

## Reproducible blind evaluation

Use a fresh synthetic corpus for evidence about a classifier change:

1. Before changing the classifier, write a sampling plan and release
   thresholds. Include English and Korean tasks across security, privacy, data
   integrity, destructive work, concurrency, reliability, performance,
   migration, and routine work. Pre-register at least the required high-tier
   recall and the maximum tolerated over-routing or high-tier inflation.
2. Have one party generate synthetic tasks from that plan. Do not collect real
   runtime tasks, logs, histories, task hashes, or telemetry.
3. Give shuffled tasks to independent raters who cannot see classifier output.
   Resolve labels before scoring and keep the corpus sealed from anyone tuning
   the classifier.
4. After the candidate rules are frozen, export a UTF-8 JSON array. Each entry
   must contain a non-empty `task`, a `consensus` of `low`, `standard`, or
   `high`; `language` must be `en` or `ko`, and `category` must be one of
   `security`, `privacy`, `data-integrity`, `destructive-work`, `concurrency`,
   `reliability`, `performance`, `migration`, or `routine`. Restricting slice
   labels prevents malformed metadata from carrying task text into the
   aggregate report. An `index` and independent `ratings` may be included for
   auditability. Candidate-decision mode additionally requires a reviewed
   opaque `id` that was assigned independently of task text.
5. Score it locally, from a clean checkout, without vendor credentials or
   network access:

   ```sh
   PYTHONPATH=src python3 tests/eval/score.py --corpus /path/to/sealed-corpus.json
   ```

6. Record only the aggregate report in review evidence. Do not copy, commit,
   log, or attach the sealed task text. Delete or return the temporary corpus
   according to the evaluation agreement after the release decision.

The blind-corpus release gate passes only if the pre-registered thresholds are
met, confidence intervals and language/category slices have been reviewed, and
no slice shows an unexplained safety regression. The committed public fixture
remains useful for detecting known regressions but cannot satisfy this gate.

## Opt-in semantic triage comparison gate

Vendor triage is an opt-in experiment, not the default classifier and not a
fallback for `route` or `run`. Any future local semantic model is also an
opt-in experiment until it independently passes this gate; it must not replace
the deterministic local default merely because it improves the public fixture.

After the candidate and thresholds are frozen, an authorized evaluator may use
the explicit `wclass classify --source-vendor claude --ask-vendor` boundary to
obtain one tier for each sealed task. That separate collection step owns vendor
access, disclosure, and quota/billing, and requires separate approval. The
Codex adapter currently fails closed because Codex has no documented
all-tools-disabled CLI contract. Add authorized results as a `vendor_tier`
field in the evaluator-owned temporary corpus, then compare the three
pre-registered candidates entirely offline:

```sh
PYTHONPATH=src python3 tests/eval/score.py \
  --corpus /path/to/sealed-corpus.json --compare-triage
```

The comparison harness never invokes a vendor CLI. It reports local-only,
vendor-only, and raise-only (`max(local, vendor)`) aggregate metrics. The public
fixture is rejected in comparison mode, and a missing or malformed
`vendor_tier` fails closed. Accept a composition only if it beats the
pre-registered baseline and thresholds on the fresh blind corpus, including
review of confidence intervals and every language/category slice.

Vendor failure remains terminal: unavailable, timed-out, oversized, malformed,
or non-zero vendor output exits `8` with only `triage_unavailable`; it never
quietly becomes the local result. This experiment adds no credentials, HTTP
client, task persistence, retry supervisor, or automatic cross-vendor route to
weightclass. The external CLI still owns its authentication and network. The
source vendor remains explicit for collection and for any later execution.

## Offline Phase 4 candidate decision

An evaluator may score a frozen candidate without adding it to `weightclass` or
letting the scorer run it. Keep the fresh sealed corpus outside the repository
and supply a second local JSON file containing identifier-bound predictions and
predeclared gate evidence:

```sh
PYTHONPATH=src python3 tests/eval/score.py \
  --corpus /path/to/sealed-corpus.json \
  --candidate /path/to/candidate-evidence.json
```

`predictions` must have exactly one record for each corpus entry, in corpus
order. Each record repeats the corpus's reviewed opaque `id` and consensus
`label`, then supplies one `low`, `standard`, or `high` `prediction`. Duplicate,
unknown, missing, out-of-order, and label-mismatched records fail closed. Every
record, candidate, and baseline ID is 1–64 ASCII characters: the first must be
a letter or digit, and the remainder may also contain dots, underscores, and
hyphens. IDs must be assigned independently of task text; task-derived IDs and
task hashes are forbidden, and the independent reviewer—not the scorer—verifies
that provenance. `candidate_id` and `baseline_id` must differ. The scorer
validates but does not emit those unverified evaluator labels; record and bind
them to source revisions only in the independent review record. The candidate
file has this exact schema; unknown, missing, malformed, or mismatched fields
are rejected with diagnostics that name only fields or entry positions.
Duplicate JSON object fields are rejected before schema validation without
including their names or values in diagnostics. The accepted object is:

```json
{
  "schema_version": 1,
  "candidate_id": "candidate-1",
  "baseline_id": "deterministic-baseline-1",
  "predictions": [
    {"id": "task-001", "label": "high", "prediction": "high"},
    {"id": "task-002", "label": "standard", "prediction": "standard"},
    {"id": "task-003", "label": "low", "prediction": "low"}
  ],
  "quality_gate": {
    "high_tier_recall_min": 0.9,
    "high_tier_recall_ci_rule": "lower-bound",
    "over_routing_max": 0.1,
    "over_routing_ci_rule": "upper-bound",
    "slices_reviewed": true,
    "unexplained_slice_regression": false
  },
  "resource_gate": {
    "startup_accepted": true,
    "latency_accepted": true,
    "memory_accepted": true,
    "supported_platform_determinism_accepted": true
  },
  "supply_chain_gate": {
    "dependency_pin_reviewed": true,
    "dependency_audit_accepted": true,
    "model_download_required": false,
    "maintenance_cost_accepted": true
  }
}
```

The only accepted interval-rule literals are `lower-bound` for high-tier
recall and `upper-bound` for over-routing. Any other value is rejected before
scoring with a value-free diagnostic.

Candidate mode emits one aggregate-only JSON decision record. It scores both
the supplied candidate predictions and the current deterministic local
classifier against the same fresh corpus. `baseline_metrics` contains the
local baseline's aggregate agreement, high-tier recall, over-routing,
confusion matrix, and fixed slices. The scorer does not bind the supplied
`baseline_id` to its checkout, so the independent record must bind the measured
baseline to the scorer revision and classifier configuration. Rates use the
same two-sided Wilson 95% interval as the ordinary report (`z = 1.96`) and are
rounded to six decimal places only when serialized. High-tier recall passes when its Wilson
lower bound meets `high_tier_recall_min`; a corpus with no expected high-tier
entries fails that gate. Over-routing passes when its Wilson upper bound does
not exceed `over_routing_max`. The candidate quality section also contains the
confusion matrix and every fixed language/category slice. A supported slice
absent from the corpus is represented by zero totals and the defined
`[0.0, 0.0]` empty interval rather than being omitted.

The `comparison_gate` reports aggregate candidate-minus-baseline recall and
over-routing rate deltas plus the number of candidate predictions below the
local baseline. Its raise-only requirement passes only when that count is zero.

The report records `corpus.evaluator_supplied` as true but
`corpus.freshness_verified_by_scorer` as false. A local path proves only that a
file was supplied; the scorer deliberately does not hash it, compare it with a
known corpus, or claim to verify its provenance. The independent review record
must establish that it was genuinely fresh and blind.

The decision is `go` only when both quality bounds pass, slices were reviewed,
there is no unexplained slice regression, the observed predictions are
raise-only against the same-corpus local baseline, every resource field is
accepted, pinning and dependency audit were reviewed and accepted, no model
download is required, and maintenance cost is accepted. Resource and
supply-chain sections each include an explicit aggregate `passes` field. Every
other complete record is `no-go`; incomplete or ambiguous records fail
validation. Candidate mode rejects the committed public fixture and aliases
with the same filesystem identity, including symlinks and hardlinks, before
read. It cannot recognize a copied fixture, so the independent freshness
review remains mandatory.

Decision template:

```text
candidate_id + baseline_id + evaluator-supplied corpus count
quality: recall lower bound >= minimum; over-routing upper bound <= maximum;
         slices reviewed; no unexplained slice regression
resources: startup + latency + memory + platform determinism accepted
supply chain: pin reviewed + audit accepted + no download + maintenance accepted
privacy: aggregate-only; no corpus task field or per-task record emitted;
         candidate and baseline identifiers are not emitted
comparison: candidate and local baseline measured on the same supplied corpus;
            zero predictions below baseline; aggregate rate deltas recorded
provenance: scorer does not verify corpus freshness or bind evaluator labels;
            independent review must verify and record both
decision: scorer go only if every machine-readable gate passes; otherwise no-go
```

For the reproducible review record—including evidence provenance, evaluator
role, corpus and scorer versions, commands used, confidence sufficiency, and
pass/fail rationale—complete
[`docs/phase4-go-no-go-template.md`](../../docs/phase4-go-no-go-template.md).
The default is `no-go` when that record is incomplete or does not demonstrate
sufficient improvement over `baseline_metrics`, even if the scorer emits `go`
for the supplied machine-readable gates.

The scorer reads only the two explicit local files. It does not access
credentials, invoke a provider or candidate runtime, make HTTP requests,
download a model, retry, supervise background work, select a vendor, infer an
entitlement, or write router/vendor configuration. This gate does not add a
production semantic model or change default routing.

### No-retention boundary

The scorer reads the supplied corpus and, in candidate mode, the supplied
evidence file, holds their contents in process memory for the run, and emits
aggregate results only. It does not write task content, hashes, predictions per
task, caches, diagnostics containing field values, or the unverified candidate
and baseline identifier values.
The operator owns the supplied file and must keep it outside the repository.
This evaluation-only file input does not change the runtime contract: `wclass`
continues to accept task text transiently on standard input and never persists
it.

## Honest limits of this corpus

- **40 items is small.** The `high` subset that the under-rating figure is
  computed over is only 15 items, so that figure has a wide confidence interval.
  Treat a few points of difference as noise; treat 15/40 versus 33/40 as real.
- **It is no longer sealed.** The figures were measured before the corpus was
  committed, but anyone tuning the classifier can now read it. The original
  local score was 15/40; a later outcome-pattern refinement re-runs at 17/40.
  Neither figure is valid evidence of general accuracy for later changes. A
  tuned score against this file measures the tuner, not the classifier. Build
  and blind-rate a fresh corpus before making a new accuracy claim.
- **Recorded vendor tiers are a snapshot.** Models change. `recorded_vendor_tier`
  documents what was measured, not what a rerun will produce.
