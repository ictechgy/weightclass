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
the existing explicit `wclass classify --source-vendor VENDOR --ask-vendor`
boundary to obtain one tier for each sealed task. That separate collection step
owns any vendor access and requires separate approval. Add those results as a
`vendor_tier` field in the evaluator-owned temporary corpus, then compare the
three pre-registered candidates entirely offline:

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
client, task persistence, retry supervisor, or automatic cross-vendor route.
The source vendor remains explicit for collection and for any later execution.

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
record, candidate, and baseline ID is limited to 1–64 ASCII letters, digits,
dots, underscores, and hyphens. IDs must be assigned independently of task
text; task-derived IDs and task hashes are forbidden, and the independent
reviewer—not the scorer—verifies that provenance. `candidate_id` and
`baseline_id` must differ. The scorer
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
