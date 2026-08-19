# Policy 4 fresh blind direction check

_Run on 2026-08-19 KST. This is evaluation evidence, not a promotion gate._

## Protocol

- Two independent generators produced 24 synthetic maintenance prompts in an
  empty repository: 12 English and 12 Korean. They could not inspect
  weightclass, its public regression fixture, or classifier output.
- Three independent raters received only the tier definitions and the sealed
  prompt set. They could not inspect weightclass, classifier predictions, the
  public fixture, or one another's labels.
- The three rating files were structurally validated and committed in the
  outside-repository study workspace before any policy prediction ran.
  Majority vote set the reference tier; a three-way split would have been
  excluded without negotiation.
- The already installed `weightclass 0.15.1` classifier then ran locally with
  explanation output confirming policy version 4 for every prompt. Prediction
  made no network call. No policy code was changed after seeing this corpus.
- Only the aggregate result is retained here. Prompt text, opaque row IDs,
  per-row ratings, predictions, and model envelopes are not repository data.

This design reduces direct tuning leakage, but it does not make 24 synthetic
prompts representative of real repositories. The intervals below are
two-sided Wilson 95% intervals with `z = 1.96`.

## Result

The raters had a majority on all 24 prompts and were unanimous on 20/24
(83.3%; 95% CI 64.1%–93.3%). The reference distribution was 8 `low`, 7
`standard`, and 9 `high`. Policy 4 predicted 2 `low`, 21 `standard`, and 1
`high`.

| Metric | Result | 95% CI |
| --- | ---: | ---: |
| Agreement | 10/24 (41.7%) | 24.5%–61.2% |
| High-tier recall | 1/9 (11.1%) | 2.0%–43.5% |
| Over-routing | 6/24 (25.0%) | 12.0%–44.9% |

Confusion matrix (reference rows, policy predictions as columns):

| Reference | `low` | `standard` | `high` |
| --- | ---: | ---: | ---: |
| `low` | 2 | 6 | 0 |
| `standard` | 0 | 7 | 0 |
| `high` | 0 | 8 | 1 |

The English slice had 3/12 agreement, 0/5 high-tier recall, and 4/12
over-routing. The Korean slice had 7/12 agreement, 1/4 high-tier recall, and
2/12 over-routing. Those slices are too small for comparative language claims.

## Interpretation

This result does not justify lowering the default tier or broadening cheap
rules. The strongest directional warning is under-routing: eight of nine
majority-`high` prompts went to `standard`. Over-routing also remains visible,
with six majority-`low` prompts going to `standard`. The sample and its
intervals are too small for a general accuracy estimate, but they are enough to
reject a claim that policy 4 has already solved either side of the routing
tradeoff.

The corpus is now spent. Any policy change motivated by these observations
needs a new independently generated and rated corpus; re-scoring or tuning on
these 24 prompts is regression work, not blind evidence.
