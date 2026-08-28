# Advisory experiment records

`wclass-advisory experiment` is an offline analyzer for pre-registered,
aggregate-only evidence. It does not invoke a provider, inspect a repository,
write advisory state, or update a route. Input is UTF-8 JSONL with one closed
schema-1 object per non-empty line. The file is capped at 4 MiB and each line at
64 KiB, with at most 10,000 records; duplicate or unknown keys, excessive JSON
nesting, non-finite numbers, special files, and values outside the documented
bounds fail with `invalid_experiment_input` without echoing a record.

Do not put task text, task identifiers, timestamps, profile names, paths,
fingerprints, credentials, or provider output in these files. The schemas have
no field for any of them.

## Sequential acceptance

```json
{"schema_version":1,"experiment":"sequential","accepted":true}
```

```sh
wclass-advisory experiment sequential --records outcomes.jsonl \
  --target-rate-bps 7500 --alpha-bps 500 \
  --minimum-samples 20 --maximum-samples 60
```

Rates and error levels use basis points (`10000` = 100%). The analyzer uses a
simultaneous Hoeffding interval with a summable error budget across looks. It
returns `signal_above_target`, `signal_below_target`, `continue`, or
`capacity_reached`. Caller JSONL always reports `promotion_eligible:false` and
never changes production routing. Sequential analysis output uses schema 2;
input records remain schema 1.

For a sealed managed population, use the separate gate:

```sh
wclass-advisory campaign-gate --vendor codex --workflow design \
  --metric cheap_acceptance --target-rate-bps 7500
```

It accepts exactly one vendor/workflow, validates the manifest and every lane
binding, and derives outcomes without writing another record.
`eligible_for_human_review` requires both campaign minimums and the statistical
target. `policy_decision_allowed` and `core_routing_changed` remain false.

## Context Guard × advisory

Each cell must be sampled independently under the same pre-registered task and
acceptance protocol:

```json
{"schema_version":1,"experiment":"context_2x2","cell":"baseline","accepted":true,"input_tokens":100,"output_tokens":20,"elapsed_ms":800}
```

`cell` is exactly one of `baseline`, `guard`, `advisory`, or
`guard_advisory`. Token and elapsed counts are non-negative integers. The report
shows per-cell acceptance and means. Its difference-in-differences interaction
is descriptive, not a causal estimate; it is null until all four cells have at
least one record.

## Generator–critic brainstorming

One record represents one blinded paired comparison:

```json
{"schema_version":1,"experiment":"brainstorm_generator_critic","baseline_compliant":true,"treatment_compliant":true,"baseline_critical_violation":false,"treatment_critical_violation":false,"baseline_diversity_bps":5000,"treatment_diversity_bps":8000,"baseline_duplicate_rate_bps":2500,"treatment_duplicate_rate_bps":1000,"preference":"treatment","raters_agree":true}
```

`preference` is `baseline`, `treatment`, or `tie`. Diversity and duplicate
rates are bounded from 0 through 10000 and must use a blinded, pre-registered
rating method. The report keeps preference, constraint compliance, critical
violations, diversity, duplicate rate, and rater agreement separate. It cannot
establish novelty from a verifier and does not enable a brainstorming
production workflow.

## Confidence and abstention

A prediction record uses an integer probability in basis points:

```json
{"schema_version":1,"experiment":"confidence","predicted_probability_bps":8000,"accepted":true,"abstained":false}
```

An abstention must not invent a prediction or outcome:

```json
{"schema_version":1,"experiment":"confidence","predicted_probability_bps":null,"accepted":null,"abstained":true}
```

The report gives the abstention rate and an exact Brier numerator
(`brier_squared_error_sum_bps2`) with denominator
`evaluated * 100000000`. Metrics are available with at least one non-abstained
outcome, but the analyzer never claims the sample is sufficient for calibration.

## Interpretation boundary

The analyzer checks shape and arithmetic, not study validity. Before collecting
records, fix the sampling population, exclusions, acceptance criteria, model
and prompt variants, human-rating procedure, stopping rule, and primary metric.
Do not tune those choices after inspecting outcomes. A report is evidence for a
human promotion decision, never authorization for automatic model selection.
