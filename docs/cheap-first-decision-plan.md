# Cheap-first routing: contract change scope and measurement plan

**Status: closed at Stage A0 on 2026-09-04 — no-go.** The verifier recall gate was
not met; see [`verifier-recall-result.md`](verifier-recall-result.md). Stage B was
never started. The rest of this document is kept as the pre-registration it was.

This document turns the proposal in
[`speculative-cheap-route-design.md`](speculative-cheap-route-design.md) into
two decisions the owner can take one at a time: what evidence would justify
moving the V1 boundary, and exactly what would move if it did. Nothing in core
`wclass` changes until Stage B is explicitly started.

Written 2026-09-04, after the three-vendor review and the 0.31.1 release.

## 1. Where things actually stand

Facts, with their source:

| Fact | Source |
| --- | --- |
| The only lever measured as large is model grade: estimated cost −69.02%, 95% CI [60.57%, 77.47%], on 90 pairs; rejected for 2 new critical failures in 90. | `README.md` "Measured results for tier routing"; `HANDOFF.md` Next Steps 5 |
| Effort-tier routing showed no benefit; a pinned `medium` beat routing on both vendors. | `paired-token-study.md` |
| Expected cost of cheap-first is `c + p`; at `c = 0.31` break-even is `p = 0.69`; the working target is `p < 0.20`. | `speculative-cheap-route-design.md`; `HANDOFF.md` Next Steps 5 |
| **The runner already exists and ships.** `wclass-advisory run` (`advisory/speculative_run.py`, 5,526 lines) runs cheap → verify → escalate in disposable clones, and `speculative_report.py` reads `p` and the modelled saving off its log with a `t`-quantile decision rule that rounds conservatively. | `src/weightclass/advisory/`; the Homebrew formula test exercises `wclass-advisory run --help` |
| The only `p` observation so far is cheap acceptance 2/3. `n = 3` is not an estimate. | `HANDOFF.md` Next Steps 5 and "What did not survive checking" |
| The current cheap **retry** shaping is harmful: of 8 cheap verification failures, 1 retry passed, 4 stayed the same, 3 degraded. | `HANDOFF.md` Next Steps C |
| Verification recovers safety, not quality: six documented real defects passed the cheap arm's acceptance tests (`True` as schema version, silent overwrite of malformed state, padded ledger IDs, mutable cache list). | `HANDOFF.md` Next Steps 4 and D; `QUALITY-RESULT.md` in the study repo |
| The advisory campaign gate is indexed to advised failures, so a good cheap route starves the study (14/60 tasks, 9/12 advised failures). | `HANDOFF.md` Next Steps C |

Two consequences drive the plan:

1. **No code is needed to measure `p`.** Stage A is a measurement campaign on
   the companion that exists, plus one harness the companion lacks.
2. **`p` alone cannot authorize the move.** A verifier that misses real defects
   makes a low `p` meaningless, because "passed verification" would not mean
   "acceptable". Verifier recall has to be measured first, on the defect classes
   this project has already documented.

## 2. Stage A — evidence, no contract change

Everything in Stage A runs inside the explicitly experimental companion and
touches no core routing contract.

### A0. Verifier recall harness (prerequisite)

Build the injected-defect harness that Next Steps item D already names, from the
documented defect classes, and measure what `verify.sh` catches.

- **Input:** at least 20 injected defects across the four documented classes
  (schema-version coercion, silent overwrite of malformed state, whitespace or
  unicode padding admitted into identifiers, mutable internal state returned by
  reference) plus the two credential-in-tree cases the design already scans for
  (content and pathname).
- **Metric:** recall `r` = defects that make the reviewed verify command exit
  non-zero / defects injected.
- **Gate:** `r ≥ 0.80` on the injected set, with the credential cases at 1.0.
  Below that, a cheap-first mode would promote defective work at a rate the
  saving does not justify, and Stage B is not started regardless of `p`.
- **Why first:** every other number in this plan is conditioned on "verified
  means acceptable". This is the cheapest way to find out whether that is true,
  and it is publishable, which the current private fixture is not.

### A1. Pre-registration

Before the first task is dispatched, commit a short pre-registration under
`docs/` fixing:

- the cost ratio `c` and where it comes from (a user-supplied single-origin
  price table, as `docs/advisory-vendor-profiles.md` requires; the 0.31 default
  is the 90-pair measurement and must be named as such);
- the two routes by reviewed fingerprint: the cheap route and the escalation
  route, both from the same vendor, model grade as the only difference;
- the verify command by fingerprint, and the `r` it scored in A0;
- the sample: **consecutive real maintenance tasks**, no selection, target
  `n = 30` with a pre-declared extension to `n = 60`;
- the decision rule below, verbatim.

### A2. Collection

Run every task through `wclass-advisory run` with the pre-registered routes.
Per task the log already records what is needed and nothing more: route
fingerprints, verdicts, token totals per attempt, timings. Task text and child
output never enter it. Three rules from the existing findings:

- **Escalation is the expensive route in a fresh clone, never a cheap retry.**
  Item C measured the cheap retry as harmful; it is not part of this design.
- **Do not stop early on good news.** The advisory campaign gate stalls when the
  cheap route succeeds; this campaign counts tasks, not failures.
- **Record the escalation outcome too.** If the expensive route also fails
  verification, that task is neither a saving nor a loss of the mode; it is
  a task the tool cannot do, and it must not be dropped from `n`.

### A3. Decision rule (fixed before collection)

`speculative_report.py` computes the interval; the rule is applied to its
output, using its `t` quantile and its conservative rounding, not `z = 1.96`.

| Condition on the 95% interval for `p` | Decision |
| --- | --- |
| upper bound `< 0.20` and `r ≥ 0.80` | **Go to Stage B** |
| lower bound `> 0.69` | Dead. Record the result, keep the companion, close the item. |
| anything else at `n = 30` | Extend to `n = 60` once. Still undecided at 60: no-go, record, close. |

Secondary metrics, reported but not decisive: wall-clock overhead of
verification on tasks that would have passed anyway; share of tasks where both
arms failed; modelled saving from the report's `c + p` line. No saving is
claimed from token counts.

Budget: at `c = 0.31` and `p ≈ 0.2`, 30 tasks cost about 15 expensive-route
equivalents plus 30 verify runs of wall clock.

## 3. Stage B — what moves if the gate passes

Stage B is a minor version (`0.32.0`) and a change to two authoritative
documents before any code: `src/weightclass/AGENTS.md` "The V1 contract" and
`docs/routing-roadmap.md` "Non-negotiable constraints" and "Rejected
directions". The table is the exact scope; nothing else in the contract moves.

| V1 property today | After Stage B | Boundary kept |
| --- | --- | --- |
| Exactly one foreground vendor child. | One **reviewed sequence** of at most two vendor children and two verify children, each foreground, sequential, bounded by the existing per-child timeout. Never parallel. | Still no background lane, no supervisor, no unbounded loop. |
| Does not retry or recover. | Recovers **once**, on one mechanical signal: the reviewed verify command's non-zero exit on the cheap result. Never on the child's exit code alone, never on output parsing. | "Run standard first and retry at high after failure" stays rejected: exit-code retry is not verification. |
| Never creates or deletes directories. | Creates disposable clones under a user-named out-dir with the crash-safe registry and `prune` that the companion already has; deletes only what it registered. | Never writes to the user's repository. Output is a patch plus base commit; applying it stays a human action. |
| Runs nothing but the selected route. | Also runs the user's reviewed verify command, fingerprinted with the routes. | Same trust class as the vendor command the user already supplies; disclosed in the review before anything starts. |

Unchanged and restated in the same edit: no credential reads, no HTTP, no task
content in any log, receipt, argv, pathname, or diagnostic; model and effort
labels stay opaque; cross-vendor escalation stays a policy opt-in; every route
fingerprint stays unchanged for unchanged inputs; the usage store gains no new
field, only the existing `rework`/`escalation` counters populated by the mode
instead of by self-report.

Surface, in order of PR:

1. **Contract PR.** The two documents above, plus a `BREAKING CHANGE:` note in
   the commit body because the contract text changes even though every existing
   invocation keeps its behaviour.
2. **`wclass speculate`** (name to be settled; not a flag on `run`, so `run`'s
   one-child promise stays literally true). It takes a policy naming the cheap
   route, the escalation route, and the verify command; reviews all three with
   fingerprints on the terminal by default, exactly as `run` does; then executes
   the sequence by calling the companion's runtime. Non-terminal use requires
   the three acknowledged fingerprints.
3. **Receipts and usage.** One receipt per sequence: both route fingerprints,
   the verify fingerprint, the verdicts, and the token totals per attempt. The
   usage store's `rework`/`escalation` counters are written by the mode.
4. **Release 0.32.0** with the Stage A result linked from the release notes and
   the README's "Measured results" section updated with the measured `p` and
   `r`, intervals included. The README claim stays exactly as strong as the
   interval allows.

What Stage B still does not do, and says so:

- It does not recover quality. The documented defect classes that pass tests
  still pass. `r` from A0 is the honest ceiling and is printed with the receipt.
- It does not sandbox the verify command. Anyone running it on untrusted output
  puts the verifier in a container; the tool says this rather than pretending.
- It does not decide `c`. The saving is modelled from a user-supplied ratio.

## 4. Order of work and size

| Step | Where | Size | Blocks |
| --- | --- | --- | --- |
| A0 verifier recall harness | `tests/eval/` + a study directory | one PR, mostly fixtures | everything |
| A1 pre-registration | `docs/` | one short PR | A2 |
| A2 collection, `n = 30` | maintainer machine, real tasks | weeks of ordinary work, no code | A3 |
| A3 decision | `speculative_report.py` output + one doc | one PR recording the result | Stage B or closure |
| B1 contract PR | two `AGENTS.md`/roadmap edits | one PR | B2 |
| B2 `wclass speculate` | core + companion runtime reuse | one feature PR | B3 |
| B3 receipts and usage counters | core | one PR | B4 |
| B4 release 0.32.0 | notes, version, ledger entry for 0.31.1 bundle | one PR + tag | — |

If A0 fails its gate, the plan ends there with a finding worth publishing on
its own: the cheapest model grade is not usable because the verifiers this
project can write do not see what it breaks. That is the "verification, not
routing" hypothesis from item D, answered.
