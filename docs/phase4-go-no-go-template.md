# Phase 4 go/no-go record

Use this record to decide whether an independently evaluated offline semantic
candidate has earned consideration as a production dependency. It records a
decision; it does not authorize adding a model, run a candidate, or change
`weightclass` routing.

## Reproducible evidence workflow

1. Before unsealing predictions, freeze the candidate, baseline, corpus
   protocol, thresholds, confidence-interval rules, required slices, and
   resource and supply-chain acceptance criteria.
2. Have an evaluator who did not tune the candidate obtain a fresh corpus whose
   tasks were independently rated without seeing candidate output. Give every
   entry a reviewed opaque ID assigned independently of task text.
3. Keep the corpus and prediction file outside the repository. Produce the
   prediction file without giving the scorer a model, credentials, or network
   access. Follow the exact input contract in
   [`tests/eval/README.md`](../tests/eval/README.md#offline-phase-4-candidate-decision).
4. From the reviewed source revision, score the frozen predictions offline:

   ```sh
   PYTHONPATH=src python3 tests/eval/score.py \
     --corpus /path/to/sealed-corpus.json \
     --candidate /path/to/candidate-evidence.json
   ```

5. Copy only the aggregate JSON output into the review evidence. Record the
   command and versions below. Do not copy the corpus, per-task predictions,
   task text, excerpts, task-derived IDs, or task hashes into this repository or
   the decision record.

The candidate input is an exact, versioned JSON object containing bounded
candidate and baseline IDs; one ID-bound `low`, `standard`, or `high`
prediction per corpus entry; predeclared quality thresholds and interval rules;
and explicit boolean resource and supply-chain decisions. Missing, extra,
malformed, duplicated, unknown, out-of-order, or label-mismatched data fails
closed. The detailed schema is maintained with the scorer in
[`tests/eval/README.md`](../tests/eval/README.md#offline-phase-4-candidate-decision).

The aggregate output is schema version 1. It contains an evaluator-supplied
corpus marker, an explicit statement that the scorer did not verify freshness,
and an entry count; candidate and same-corpus local-baseline aggregate
high-tier recall, over-routing, confusion matrices, and fixed
language/category slices; an aggregate raise-only comparison; explicit
quality, resource, supply-chain, and privacy gates; and `go` or `no-go`. It does
not emit the candidate/baseline ID values, per-entry results, or corpus task
content. Fresh/blind status and identifier-to-revision binding are established
only by the independent provenance review below.

The machine privacy section confirms that no corpus task field, per-task record,
or candidate/baseline identifier value was emitted. The independent reviewer
must still verify that every identifier was assigned without deriving it from
task content.

High-tier recall is correctly predicted `high` entries divided by expected
`high` entries. Its gate uses the lower bound and fails when the corpus has no
expected high entries. Over-routing is predictions above the expected tier
divided by all entries; its gate uses the upper bound. Each rate reports a
two-sided 95% Wilson score interval with `z = 1.96`; rounding to six decimal
places occurs only in serialized output. Fixed English/Korean and reviewed
category slices report the same aggregate metrics. An absent supported slice
is retained with zero totals and `[0.0, 0.0]`; the evaluator must review every
slice and declare whether any unexplained regression remains.

## Decision record template

Complete every field. Use opaque identifiers and aggregate values only. A blank,
`unknown`, or `unresolved` field makes the decision `no-go`.

```text
record_version: 1
decision_date_utc: YYYY-MM-DD

evidence_provenance:
  candidate_id: <reviewed opaque identifier>
  candidate_source_revision_or_artifact_version: <non-secret immutable reference>
  baseline_id: <reviewed opaque identifier>
  baseline_id_bound_to_scorer_revision_and_configuration: yes | no
  integration_mode: raise-only
  corpus_version_id: <reviewed opaque identifier; never task-derived>
  corpus_fresh_and_blind: yes | no
  corpus_rating_provenance: <roles/process only; no task text>
  prediction_provenance: <independent supplier and frozen version; no task text>
  evaluator_identity_or_role: <name or accountable independent role>
  evaluator_independent_of_candidate_tuning: yes | no
  scorer_source_revision: <weightclass commit identifier>
  scorer_schema_version: 1
  commands_used: <exact local commands; paths must reveal no sensitive content>
  aggregate_report_location: <reviewed evidence reference; no task content>

predeclared_quality_gates:
  high_tier_recall_minimum: <0..1>
  high_tier_recall_ci_rule: lower-bound
  over_routing_maximum: <0..1>
  over_routing_ci_rule: upper-bound
  required_language_slices: en, ko
  language_slice_acceptance_rules: <predeclared aggregate thresholds or regression bounds>
  required_category_slices: <reviewed fixed categories>
  category_slice_acceptance_rules: <predeclared aggregate thresholds or regression bounds>
  slices_reviewed: yes | no
  unexplained_slice_regression: yes | no
  confidence_interval_method: two-sided Wilson 95%, z=1.96
  confidence_interval_sufficiency_rule: <predeclared sample-size or interval-width rule>
  confidence_intervals_sufficient_for_decision: yes | no

observed_aggregate_evidence:
  corpus_entry_count: <integer>
  expected_high_count: <integer>
  baseline_high_tier_recall: <count>/<total>, <rate>, 95% CI [<low>, <high>]
  baseline_over_routing: <count>/<total>, <rate>, 95% CI [<low>, <high>]
  baseline_slice_review: <aggregate findings only>
  candidate_below_baseline_count: <integer; must be zero>
  raise_only_comparison_gate: pass | fail
  high_tier_recall_rate_delta_vs_baseline: <aggregate decimal>
  over_routing_rate_delta_vs_baseline: <aggregate decimal>
  high_tier_recall: <count>/<total>, <rate>, 95% CI [<low>, <high>]
  high_tier_recall_gate: pass | fail
  over_routing: <count>/<total>, <rate>, 95% CI [<low>, <high>]
  over_routing_gate: pass | fail
  language_slice_review: <aggregate findings and pass/fail only>
  category_slice_review: <aggregate findings and pass/fail only>
  measured_improvement_over_baseline: <aggregate comparison; no per-task result>
  measured_improvement_justifies_added_cost: yes | no
  quality_gate: pass | fail

resource_feasibility:
  startup_evidence_reference: <aggregate benchmark reference>
  startup_accepted: yes | no | unresolved
  latency_evidence_reference: <aggregate benchmark reference>
  latency_accepted: yes | no | unresolved
  memory_evidence_reference: <aggregate benchmark reference>
  memory_accepted: yes | no | unresolved
  supported_platform_determinism_evidence_reference: <aggregate test reference>
  supported_platform_determinism_accepted: yes | no | unresolved
  resource_gate: pass | fail

supply_chain_review:
  dependency_pin_evidence_reference: <review reference>
  dependency_pin_reviewed: yes | no | unresolved
  dependency_audit_evidence_reference: <review reference>
  dependency_audit_accepted: yes | no | unresolved
  model_download_required: yes | no | unresolved
  maintenance_cost_evidence_reference: <review reference>
  maintenance_cost_accepted: yes | no | unresolved
  supply_chain_gate: pass | fail

privacy_review:
  aggregate_only_report: yes | no
  candidate_and_baseline_identifiers_emitted: yes | no
  corpus_task_field_or_per_task_record_emitted: yes | no
  identifiers_independently_verified_not_task_derived: yes | no
  corpus_and_predictions_kept_outside_repository: yes | no
  privacy_gate: pass | fail

final_decision: go | no-go
pass_fail_rationale: <explain every failed, unresolved, or narrowly passed gate;
  include no task text or per-task result>
production_dependency_action: consider-after-separate-review | do-not-add
reviewer_identity_or_role: <accountable reviewer>
```

## Decision rule

The default is **no-go**. Record `no-go` and `do-not-add` if evidence or required
provenance is missing; a gate was not declared before evaluation; either Wilson
bound misses its threshold; the intervals are too wide or otherwise judged
insufficient under the predeclared rule; any required slice is unreviewed,
misses its predeclared acceptance rule, or has an unexplained regression; the
candidate has any prediction below the same-corpus local baseline; the
candidate does not demonstrate sufficient improvement over that baseline; the
measured improvement does not justify the added cost; the
corpus is not fresh and blind; or privacy, resource
feasibility, or supply-chain review is failed or unresolved. The scorer's `go`
is necessary but not sufficient when the broader record is incomplete.

The completed record must also be internally consistent. The baseline binding,
evaluator independence, non-task-derived identifier verification, and
outside-repository custody fields must be `yes`; `aggregate_only_report` must
be `yes`; and both emitted-content fields must be `no`. Any opposite answer or
any `pass`/`go` value that contradicts those answers forces `no-go` and
`do-not-add`.

A production model dependency may be considered in a separate reviewed change
only after independently supplied evidence satisfies every predeclared quality,
slice, confidence-interval, privacy, resource, and supply-chain gate. Until such
evidence exists, the Phase 4 decision remains **no-go** and the deterministic
offline router remains unchanged.
