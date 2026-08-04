# Classifier evaluation corpus

`corpus.json` holds the 40 tasks behind the accuracy figures in `README.md`, so
those numbers can be re-derived instead of taken on faith.

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

`score.py` compares the local classifier against the consensus. It makes no
network calls and invokes no vendor CLI:

```sh
PYTHONPATH=src python3 tests/eval/score.py
```

To score the vendor path as well, pass `--vendor claude` or `--vendor codex`.
That **does** spend one call per task on your own subscription, so it is off by
default and never runs in CI.

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
