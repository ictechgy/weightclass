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
   auditability.
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

### No-retention boundary

The scorer reads one supplied file, holds its contents in process memory for
the run, and emits aggregate results only. It does not write task content,
hashes, predictions per task, caches, or diagnostics containing field values.
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
