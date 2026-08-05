# Phase 4 evaluation contract audit

Baseline: `73388587d9d186dcff7e4a5caf815ffff371625f` (merge of PR #14).
This audit did not open `tests/eval/corpus.json` or any credential, environment,
or authentication file. It used repository code, tests, and documentation only.

## Existing contract mapped to the Phase 4 brief

| Requirement | Existing evidence | Baseline assessment |
| --- | --- | --- |
| Fresh blind input | `tests/eval/score.py` accepts `--corpus`, labels supplied input as a synthetic blind corpus, and requires non-empty task text, consensus tier, reviewed `en`/`ko` language, and a fixed category. `tests/eval/README.md` requires an independently generated, shuffled, rated, sealed corpus and pre-registered thresholds. | Supported. The committed corpus is explicitly regression-only and cannot satisfy a new acceptance gate. |
| Evaluator-supplied predictions | `tests/eval/score.py` accepts one optional `vendor_tier` per entry with `--compare-triage`, then scores local-only, vendor-only, and raise-only candidates. `OfflineOutputTests.test_triage_comparison_is_offline_and_reports_all_candidates` proves that comparison does not invoke a vendor process. | Partially supported; see gap G1. |
| Required quality metrics | `aggregate_metrics` and `render_report` in `tests/eval/score.py` provide agreement, a three-tier confusion matrix, high-tier recall, over-routing, Wilson 95% confidence intervals, and language/category slices. `MetricAggregationTests.test_aggregates_confusion_recall_overrouting_and_slices` checks the calculations and slice totals. | Supported for classification quality. |
| Aggregate-only output | `render_report` prints counts, rates, intervals, matrix cells, and fixed slice labels, never individual records. `OfflineOutputTests.test_supplied_corpus_output_contains_only_aggregate_results` uses a sentinel task and proves it is absent from both output streams. Validation errors identify only entry positions and field names; the validation tests prove rejected values are not echoed. | Supported. |
| No retention or task-derived identifiers | `tests/eval/score.py` reads the evaluator-owned JSON file into memory and contains no write, cache, logging, or hashing path. `tests/eval/README.md` requires the supplied file to remain outside the repository and forbids recording or attaching task text, per-task predictions, and task hashes. | Supported by implementation inspection and output tests. The evaluator necessarily owns a temporary corpus file; that is evaluation input, not runtime capture. |
| Runtime privacy boundary | `src/weightclass/classification.py` is explicitly local and has no logging or persistence. `src/weightclass/cli.py` reads runtime tasks from standard input and emits only tiers/static reason metadata or redacted errors. `TaskConfidentialityTests.test_no_subcommand_echoes_any_word_of_the_task` covers success and failure paths across runtime subcommands. | Runtime task text can remain transient and absent from logs, diagnostics, hashes, persisted artifacts, and scored reports. No Phase 4 scorer needs to alter this boundary. |
| Offline/default behavior | The scorer imports only the deterministic local classifier; the comparison test fails if `subprocess.Popen` is used. `README.md` and `docs/routing-roadmap.md` keep default classification deterministic and offline and keep semantic candidates opt-in pending an independent gate. | Supported. |
| Default CLI compatibility | `DefaultOutputCompatibilityTests.test_default_outputs_remain_byte_for_byte_compatible_without_explain` fixes the default `classify` and `route` JSON shapes. `test_keeps_a_high_effort_task_on_the_default_policy_vendor` and the source-vendor route/run tests fix vendor pinning and prevent implicit switching. `.github/workflows/ci.yml` also asserts the installed default classification output exactly. | Any evaluation extension must remain under `tests/eval/`; it must not change `wclass` arguments, output, classification, fingerprints, or route/run selection. |
| Resource and supply-chain gate | `docs/routing-roadmap.md` requires a candidate to be pinned, deterministic across supported platforms, dependency-audited, and benchmarked for startup, latency, and memory; its Phase 4 exit gate requires improvement to justify package, performance, maintenance, and supply-chain cost. | Policy exists, but no scored decision record exists; see G2 and G3. |
| Tests and CI | `tests/test_eval_score.py` covers schema rejection, redaction, metrics, aggregate-only reporting, offline comparison, and rejection of the public fixture for comparison. `.github/workflows/ci.yml` runs the full unit suite on Python 3.10–3.13 and separately runs lint, formatting, typing, and package checks. | Existing contract is covered; the missing Phase 4 decision schema is not. |

## Evidence-backed gaps

### G1 — Candidate predictions are tied to the Phase 3 vendor experiment

`validate_corpus` accepts only the hard-coded `vendor_tier` candidate field, and
`main` renders only the fixed labels `vendor-only experiment` and `raise-only
experiment`. There is no input for an evaluator-supplied, named offline semantic
candidate or for comparing more than that one prediction column. The current
test likewise exercises only `vendor_tier`. This blocks a Phase 4 decision
because independently produced semantic predictions cannot be scored without
renaming them as vendor results or changing the scorer for each candidate.

### G2 — Resource and supply-chain evidence is prose-only

The Phase 4 section of `docs/routing-roadmap.md` lists pinning, cross-platform
determinism, dependency audit, startup, latency, memory, maintenance, and
supply-chain cost, but `tests/eval/score.py` has no inputs or outputs for those
facts. Consequently an aggregate quality report can look complete while the
non-quality gates remain unrecorded. This blocks a reproducible go/no-go
decision even when classification metrics are available.

### G3 — There is no explicit decision result tied to predeclared gates

`tests/eval/README.md` instructs evaluators to pre-register thresholds and
review intervals and slices, but the scorer reports observations only. It does
not record threshold values, whether interval bounds are used, slice review,
resource acceptance, supply-chain acceptance, or a final `go`/`no-go`. The
absence is observable in both `render_report` and `OfflineOutputTests`: neither
defines or checks decision fields. A reviewer therefore cannot distinguish a
measured candidate from an approved candidate using the report alone.

### G4 — Privacy checks do not yet cover the missing decision metadata

Current fixed language/category labels prevent task text from being smuggled
into aggregate slice names, and sentinel tests cover existing reports and
validation errors. Once candidate names, resource evidence, or supply-chain
fields are accepted, they become new output-bearing inputs. Until those fields
have a bounded schema and redaction-focused tests, free-form metadata could
carry task text into a scored report. This blocks safely adding G1–G3 as
unrestricted strings.

No evidence-backed gap requires a production classifier, model package, network
access, credential access, vendor invocation, runtime task retention, or a
change to routing behavior.

## Compatibility constraints for the next gate

- Keep the mechanism evaluation-only and offline; candidate predictions must be
  supplied by the evaluator rather than generated by the scorer.
- Do not read the public fixture in candidate-comparison mode or represent its
  scores as fresh evidence.
- Emit aggregate values and reviewed, bounded metadata only. Never emit task
  text, excerpts, per-task predictions, task-derived hashes, or arbitrary slice
  labels.
- Do not add a dependency or model download. Treat candidate and package labels
  as opaque evaluator-supplied identifiers.
- Preserve byte-for-byte default `wclass classify` output and existing `route`
  output, fingerprints, source-vendor pinning, and route/run behavior.

## Phase 4 go/no-go decision template

The next implementation may encode this as a bounded machine-readable schema,
but a decision is not `go` unless every predeclared field is present and passes:

```text
candidate_id: <reviewed opaque identifier>
fresh_corpus: yes | no
baseline_id: <reviewed opaque identifier>
quality_gate:
  high_tier_recall_threshold: <predeclared value and CI rule>
  high_tier_recall_observed: <aggregate estimate and 95% CI>
  over_routing_limit: <predeclared value and CI rule>
  over_routing_observed: <aggregate estimate and 95% CI>
  slices_reviewed: yes | no
  unexplained_slice_regression: yes | no
resource_gate:
  startup_accepted: yes | no
  latency_accepted: yes | no
  memory_accepted: yes | no
  supported_platform_determinism_accepted: yes | no
supply_chain_gate:
  dependency_pin_reviewed: yes | no
  dependency_audit_accepted: yes | no
  model_download_required: yes | no
  maintenance_cost_accepted: yes | no
privacy_gate:
  aggregate_only_report: yes | no
  task_text_or_derived_identifier_emitted: yes | no
decision: go | no-go
```

Default to `no-go` for a missing field, a failed quality/resource/supply-chain/
privacy gate, any required model download, or evidence derived only from the
public regression fixture. At this baseline the Phase 4 decision is **no-go**:
G1–G4 prevent a complete, privacy-safe decision record, and no independently
supplied evidence has been evaluated against predeclared gates.

## Current branch resolution

The uncommitted `feat/offline-semantic-decision-gate` branch addresses the four
baseline gaps with an exact candidate schema, aggregate same-corpus candidate
and local-baseline metrics, structured quality/resource/supply-chain/privacy
fields, and an explicit machine `go`/`no-go`. It also rejects duplicate JSON
fields and the public fixture before candidate scoring, and distinguishes
evaluator-supplied assertions from facts the scorer can verify. In particular,
the scorer does not claim to verify corpus freshness or identifier provenance.

This resolves the missing mechanism, not the Phase 4 product decision. With no
independently supplied candidate, resource, or supply-chain evidence, the
current result remains **no-go** and no production model dependency is
authorized.
