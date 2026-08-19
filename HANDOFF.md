# Handoff

_Last updated: 2026-08-18 KST by Claude_

## Goal

- Maintain `weightclass` as a public, local router that can discover and let
  the user select installed Codex, Claude Code, Antigravity (`agy`), and Grok
  agents/models/efforts without hand-writing vendor commands.
- Allow explicit reviewed cross-agent/schema-3 native execution while keeping
  the default source-vendor boundary and one-foreground-child contract.
- Measure whether routing helps cost using opt-in local aggregate counters and
  user-supplied relative weights, without inferring prices, subscription usage,
  entitlement, or persisting task content.

## Current Status

- Project root: `/Users/jinhongan/Desktop/subscription-agent-router`.
- Root worktree: branch `release/0.15.1-routing-followups`, based on `a489044`
  (`origin/main`). The pre-existing `HANDOFF.md` rewrite remains intentional and
  is now being integrated with the follow-up fixes below.
- The current change set fixes cross-vendor counterfactual accounting without
  persisting the source vendor, and prevents repeated whitespace from hiding a
  costly outcome across scan windows. Focused tests, Ruff, formatting, strict
  mypy, a 1,119-test pytest run, build/Twine checks, and extracted-sdist tests
  pass locally; final release gates must be rerun after the remaining work.
- PRs **#40 through #51 are all merged**. Merge commits: `c8a3311` (#40),
  `1a7c91f` (#41), `fe93a5c` (#42), `ec5eb29` (#43), `a763d9c` (#44),
  `887159a` (#45), `bf1ad11` (#46, version bump), `4145745` (#47, downward
  result), `77802ed` (#48, speculative-run measurement tooling), `8ae5a8f`
  (#49, measured cheap-path cost from API-key billing), `7ff2917` (#50, the
  vendor-neutral advisor arm), `a489044` (#51, the release-gate fix below).
  Every merge was gated on an `ultra-review-loop` run and full CI.
- A linked worktree remains at `/private/tmp/weightclass-ralplan.7coU17/worktree`
  (detached at `a763d9c`; merged, safe to remove).
- **The paired token study is closed.** See "The routing-economics result" below.
- **`weightclass 0.15.0` is published (2026-08-18).** The annotated tag
  `v0.15.0` points at `a489044`; every `Release` job passed and the maintainer
  approved the `pypi` environment. PyPI holds exactly two artifacts, the wheel
  and the sdist. A PyPI version can never be reused, replaced, or deleted — a
  defect needs `0.15.1`, never a re-upload of `0.15.0`.
- **The Homebrew formula is the one remaining release step.**
  `packaging/homebrew/weightclass.rb` in this repo is the source of truth; copy
  it into `ictechgy/homebrew-tap` rather than editing the tap by hand, because
  `brew style`/`brew audit` only apply tap rules to a file already inside a tap.
  The published sdist, read from PyPI's own JSON (the `/packages/source/w/...`
  redirect form fails Homebrew's audit):
  - url: `https://files.pythonhosted.org/packages/f8/c9/5b39f36d6b72ac011de21137062c5f0a9903696e497d81d4a38e9c7d0e96/weightclass-0.15.0.tar.gz`
  - sha256: `32a62a33607f0a56da22e0f2b79b067c49d247a9daa3250775e3c931c8ca21fd`
- Release notes live only in the session scratchpad
  (`release-notes-0.15.0.md`); rewrite them from `git log v0.14.0..main` if
  lost. The two breaking changes must appear there because no commit body
  carries a `BREAKING CHANGE:` trailer: aggregate schema 1 -> 2 (a 0.14.0 build
  cannot read a schema-2 store) and classification policy 2 -> 3 (same input
  can route to a different tier).

## The routing-economics result

This is the reason the study existed, so keep the conclusion with the code.

- **Phase 1 pilot** (36 real invocations, two vendors): pinning effort to
  `medium` came out ahead of weightclass routing by point estimate on both
  vendors — 5.1% on Claude, 5.5% on Codex — but only Claude's fixed-arm interval
  excluded zero; Codex was a wide null. Zero rework in 36 runs.
- **Phase 1b calibration** (18 candidates, Codex, 23 invocations, 981,613
  tokens): **0 tasks were tier-sensitive.** 15 passed at the routed tier; 3
  failed at both their routed tier and one step up. The pre-registered floor for
  entering Phase 2 was 9 tier-sensitive tasks out of 36, and the design
  instructed reporting a shortfall as the finding rather than lowering the bar.
  **Phase 2 was never started.**
- Two tasks routed to `high` (`p08`, `p21`) also passed a tier down. Of the nine
  calibrated tasks blind raters called `high`, seven were routed `standard` and
  five passed there.
- What this supports: **routing up bought nothing on work of this shape.**
- **The downward question was measured afterwards** (2026-08-16, outside the
  closed study; `DOWNWARD-REPORT.md` in the study repo). The 15 tasks that
  passed at their routed tier were re-run pinned at `low`: **15 of 15 passed**,
  0 critical failures. `p08`, which the router sent to `high` for 77,170 tokens,
  passed at `low` on 31,727. So the classifier over-routed on all 15.
  Token savings are directionally favourable but **not established**: `low`
  used 506,529 tokens against the routed tiers' 632,983, or 20.0% fewer, but
  that aggregate is dominated by the largest tasks. The per-task mean saving is
  14.1% with a 95% interval of [−1.0%, +29.1%], which includes zero, and 4 of
  the 15 cost *more* at `low`.
  This does not license "always route `low`" — those 15 were selected for having
  already passed, and a binary acceptance test cannot see work that passes while
  being subtly worse.
- Two of the three failures were traced to defects in the study's own acceptance
  tests, which over-specify the interface for tasks that ask for a new API
  (`p27` misread a raise as a hang; `p13` looked only for a tuple return when
  the agent used a callback). `p26` was not investigated. The defect did not
  reach Phase 1: all 36 pilot runs recorded `completed: true`, and an
  over-specified test can only reject correct work, never accept wrong work.
- Scope: one vendor for calibration (Codex), one synthetic fixture, small
  well-specified maintenance tasks. This does not show effort never matters.
- Full study design and the closure section: `docs/paired-token-study.md`. The
  public summary is in `README.md`. The fixture, 36 rated tasks, harness, and
  per-task verdicts live in a **separate study repository outside this repo**
  (`~/weightclass-token-study/`), deliberately not vendored here.

## The advisor arm and what its redaction cost

PR #50 reimplements the **pattern** behind Anthropic's advisory tool locally
instead of adopting the tool. That keeps it usable on a subscription or an API
key, on Claude or Codex, and on paths that cannot carry a beta header. Two
shapes are measured under separate flags: Shape A (`--advise-first`, worth it
when `a_A + (c_A - c) < p - p'`) and Shape B (`--advise-on-failure`, worth it
when `s > a_B + q*r`). Shape B's condition has no `c` in it, so a
subscription log with zero cost can still decide it.

The advisor is **the only path in this design that sends text off the machine**,
so verification output, diffs, and task text all cross it. That one function
took 47 adversarial review rounds and never converged; roughly half of each
round's findings were created by the previous round's fix. Two things ended
that pattern, and they are the transferable part:

- **Judge on a format's own properties, not on what the text looks like.**
  base64 comes in groups of four, DER puts a length after its tag, and a PuTTY
  key declares its own extent with `Private-Lines:`. Every judgement moved onto
  a property stopped being re-litigated; every shape heuristic kept coming back.
- **A knob that is wrong in both directions should be removed, not tuned.**
  Several findings were "narrowing this leaks, widening it destroys the failure
  signal". Those have no stable setting. Deleting the knob or writing the limit
  into a named test (`test_a_dotted_lowercase_value_is_a_known_limit`,
  `test_a_context_line_head_with_changed_body_is_a_known_limit`) held; splitting
  the difference did not.

`tools/check_test_vacuity.py` exists because a test of mine passed while
checking nothing — its probe string was not in the input — and hid a real key
leak for five rounds. It reruns the suite against a copy of the runner whose
redaction functions are replaced by identity and lists what still passes.
Baseline on `a489044`: **227 leaves fail, 107 pass, 334 total**. Passing is not
by itself a defect (preservation tests are supposed to pass); the list is for a
human to read.

## Completed

- Released state is `weightclass 0.15.0`. Both `v0.14.0` and `v0.15.0` are
  protected tags; neither they nor their published artifacts may be moved,
  reused, relabelled, or republished.
- The in-progress follow-up passes the already validated source vendor to usage
  accounting only for the model-free `medium` counterfactual lookup. Actual
  usage remains in the destination-agent bucket; schema 2 and its persisted key
  set are unchanged, and no per-run source-vendor field is stored.
- Harmful-outcome scanning now collapses consecutive whitespace only for the
  outcome matcher. This closes a window-boundary evasion caused by unbounded
  `\s+`/`\s*` separators without changing task length or mechanical-pair distance.
- The schema-3 branch adds installed-agent discovery, interactive selection,
  reviewed cross-vendor/profile grants, observation-bound execution, and
  `wclass delegate native route|run` for one bounded child.
- Commit `5fddc38` adds opt-in aggregate-only accounting:
  - `wclass usage enable|weight|report`;
  - automatic recording for installed-entrypoint schema-3 `run` and
    `delegate native run` after a real child status is obtained;
  - cumulative agent/model/effort/tier, success/failure/status, rework, and
    escalation counters;
  - user-supplied prospective relative-cost weights and savings versus `1.0`;
  - private regular-file validation, locking, atomic replacement, bounds,
    symlink/shared-directory rejection, and redacted failure code `9`;
  - no task text/hash, per-run event, timestamp, profile/account, executable
    path, or route fingerprint in the store.
- PR #41 (`fix/routing-cost-evidence`, merged as `1a7c91f`) answers a review of
  whether routing can actually reduce cost. The review found three defects with
  evidence:
  - the classifier over-routed (public fixture: `low` recall 2/12, over-routing
    32.5%), so the cheap lever was effectively closed;
  - length alone forced `high`, making a pasted file list the most expensive
    route;
  - the savings metric compared against `weighted_runs x 1.0`, so it was an
    identity on user-supplied weights, and rework inflated the baseline. A
    measured 30% overrun (13.0 vs 10.0 units) was reported as 13.3% savings.
  - model grade, the largest cost variable, was unreachable from built-in
    routes.
- What those three commits changed:
  - classification policy `2` -> `3`. Length no longer raises a tier
    (`standard.length_floor`); the backtracking bound moved to
    `PATTERN_SCAN_CHARACTERS` while signals scan the full task;
    `low.mechanical_pair` and `low.substitution` opened the cheap tier; `auth`
    became a high signal. Fixture agreement 17/40 -> 22/40, `low` recall
    2/12 -> 6/12, over-routing 32.5% -> 22.5%. `high` recall unchanged at 5/15.
  - aggregate store schema `1` -> `2`. Savings compare against the same tasks
    on the fixed `medium` route; only first attempts count as tasks; the report
    abstains with `savings_reason_code` unless every run is weighted and every
    task has a baseline weight; per-bucket savings fields are gone. Schema 1
    stores are promoted on read without inventing baseline evidence.
  - `--{tier}-model` / `--{tier}-effort` now bind to built-in routes with a
    required `--source-vendor`, sharing one insertion-rule function with the
    preset path.
- Review-loop fixes merged on top of those three commits:
  - `b321893` `_load_store` now normalizes `RecursionError`. `json.loads` raises
    it (a `RuntimeError`, not `ValueError`) on deeply nested input, so a bounded
    hostile store crashed the router instead of failing closed. CI Python
    3.10/3.11 were red on this; 3.12+ hid it behind a different recursion limit.
  - `f0ae444` harmful-outcome detection scans overlapping windows over the whole
    task. Once length stopped raising a tier, leading filler could deterministically
    hide a costly-outcome description past the first 1,200 characters.
  - `dbea212` the counterfactual baseline is looked up without a model, matching
    the built-in standard route. Reusing the routed model priced a counterfactual
    that never existed and cancelled the very saving model routing produces.
  - `b5bea06` the cheap rules no longer fire on multi-instruction requests, and
    English `from/to` needs a numeric value swap or a mechanical verb.
- **PR #42** (`fe93a5c`) removed `\.\s+\S` from the multi-instruction guard.
  A plain sentence boundary was matching, so the guard fired on 34 of 36 study
  tasks and disabled the cheap rules almost everywhere.
- **PR #43** (`ec5eb29`) added `wclass run --suggest-escalation`. On a failed
  run it names the next tier up, with `from_tier`/`to_tier`/`route`/`vendor`/
  `route_fingerprint`/`record_as_rework`/`failure_cause_diagnosed`. It
  deliberately **does not print a runnable command**: two review tracks raised
  that as a blocker, because escalation fires on a failure path for a route the
  user never reviewed, unlike `wclass route` which is a deliberate review
  command.
- **PR #44** (`a763d9c`) added `docs/paired-token-study.md`, the pre-registered
  study design. **PR #45** (`887159a`) closed it with the Phase 1b result.
- **This repo has two test runners and they gate different things.** CI
  (`ci.yml`) installs pytest and runs `pytest -q`. The release workflow
  (`release.yml`) installs only `requirements/release.txt` — no pytest — and
  runs `python -m unittest discover -s tests`. A module written in pytest style
  is green on CI and either dies on import or, worse, **contributes zero tests**
  under the release gate, because unittest does not collect module-level
  `test_*` functions. Always reproduce with `unittest discover` before tagging.
  `tests/test_suite_structure.py` now fails on either cause.
- Verified on merged `main` (`a489044`): `unittest discover` runs **1117**
  tests, `pytest -q` reports 1117 passed and 1420 subtests, `ruff check` and
  `format --check` are clean on 152 files, and `mypy --strict src tests` is
  clean on 124 files. Note the mypy target: `src` **and** `tests`. Checking only
  `tools/` hides real errors — that is how 139 of them reached `main`. Ruff and
  mypy run through `uvx --offline` against the local uv cache; neither venv has
  them installed, and neither does the default `python3`.
- One local test fails on this machine and is **not** a code defect:
  `test_parent_sentinel_is_absent`, reached through
  `test_extracted_sdist_tests_do_not_inherit_parent_environment`. The minimal
  PATH's `python3` resolves to a different interpreter here than the one running
  the suite. It fails identically at `8ae5a8f`, before any of this work, and
  passes on CI.
- The installed `~/.local/bin/wclass` still predates these commits (0.15.0 is
  on PyPI but has not been installed here), so the user's enabled store stays
  schema 1 until a new wheel is built and installed.
  A schema-1 store is readable by the new code; a schema-2 store is not
  readable by the installed build. Do not reinstall without asking.
- A verified local wheel from `5fddc38` is installed with `uv tool` at
  `~/.local/bin/wclass`; it precedes and leaves intact the Homebrew executable
  at `/opt/homebrew/bin/wclass`.
- The default macOS aggregate store is enabled at
  `~/Library/Application Support/weightclass/usage-v1.json`.
  The directory is `0700`; store and lock are `0600`.
- Current report is intentionally empty: `runs=0`, `weights=[]`. No historical
  provider/session data was read or backfilled.

## Key Files & State

- `HANDOFF.md`: this restart-safe state; intentionally uncommitted on root main.
  Because it is uncommitted, a rewrite that drops required strings turns
  `tests/test_completion_audit_v2.py` red in the working tree while CI stays
  green. Keep the `docs/completion-audit-v2.md` line below.
- `docs/completion-audit-v2.md`: requirement-to-test completion map.
  Goal g12 is leader-verified; retain this audit connection when refreshing
  this file. Both that path and that sentence are asserted verbatim by
  `test_handoff_points_to_current_g12_audit`, so do not let a rewrap split them.
- `AGENTS.md`: privacy, networking, one-child, and persisted-aggregate boundary.
- `docs/paired-token-study.md`: the closed study — design, pre-registered gate,
  and the Phase 1b result that stopped it.
- `src/weightclass/classification.py`: classification policy 3.
- `src/weightclass/router.py`: `_TIER_LADDER` and `next_tier()` for escalation.
- `src/weightclass/usage_aggregation.py`: aggregate schema, validation, locking,
  atomic writes, reporting, and default platform paths.
- `src/weightclass/cli.py`: `usage` surface, schema-3 recording integration, and
  `_print_escalation_suggestion()`.
- `src/weightclass/entrypoint.py`: enables automatic default-store resolution
  only for the installed CLI entrypoint, preventing in-process tests from
  mutating a user's store.
- `tests/test_usage_aggregation.py`: privacy, failure ordering, concurrency,
  atomicity, weight semantics, ordinary run, and native-delegation coverage.
- `README.md` and `docs/native-schema-3.md`: public accounting contract and
  exit-code `9` semantics.

## Important Context / Decisions

### Confirmed facts

- Model, effort, availability, price, and subscription/quota claims remain
  opaque user assertions. Relative weights are not provider prices.
- Weights apply prospectively. A weight changed later does not rewrite already
  aggregated units; configure weights before comparison runs.
- Omitting `--model` from `wclass usage weight` selects the native default.
  Passing `--model default` means a literal opaque model named `default`.
- Pre-child failures are not counted. If aggregate validation fails before
  execution, code `9` starts no child. If the child completed but persistence
  failed, code `9` includes `"child_completed": true`; do not auto-retry.
- Claude/Codex tasks use stdin; built-in `agy`/Grok schema-3 routes use the
  reviewed argv task slot and retain local process-inspection exposure.
- The aggregate feature does not edit Grok/Codex/Claude/vendor configuration.
  Investigation of an unexpected Grok startup prompt found no weightclass
  config-write path. The installed Grok route adds only `-p <task>`,
  `--permission-mode acceptEdits`, and `--reasoning-effort`; the user concluded
  the displayed rules were likely the session's automatic system prompt.

### Assumptions

- The user has not yet chosen defensible relative weights, so leaving every
  bucket unweighted is safer than inventing price/subscription claims.

## What Worked

- RED→GREEN tests fixed the privacy contract before runtime integration.
- Validate the enabled store before task access, then record only after a real
  child status. This prevents counting attempts and makes retry risk explicit.
- A private owner-only directory plus no-follow regular files, advisory locking,
  bounded exact JSON, fsync, and atomic replace survived concurrent-process
  tests without lost updates.
- Installing a verified local wheel through `uv tool` preserved the Homebrew
  installation and made rollback simple (`uv tool uninstall weightclass`).
- Pre-registering the study's stopping condition. When calibration returned 0
  tier-sensitive tasks, the rule was already written, so the null result was
  reportable instead of negotiable.
- The review loop earned its cost on a docs-only PR: 12 rounds, blocker count 0
  every round, but non-blocker findings caught a genuine over-generalization
  ("the cheapest tier is always correct" — never measured), an off-by-one in a
  task count, and an unchecked blast radius for the acceptance-test defect.
- Moving a judgement onto a format property instead of a shape heuristic. See
  "The advisor arm" above: those fixes stayed fixed, the heuristics did not.
- Writing a deliberate limit into a named test instead of tuning a two-sided
  knob toward a setting that does not exist.
- Proving a new guard is not vacuous by restoring the defect and watching it
  fail. `tests/test_suite_structure.py` was checked against the pre-conversion
  file: all three of its checks failed, then passed on the fixed tree.

## What Did Not Work / Avoid

- Do not claim aggregation already happened: the store is enabled but has zero
  real runs and zero weights.
- Do not infer or scrape provider pricing, bills, quotas, credentials, auth, or
  raw session histories. Do not retroactively synthesize usage.
- Do not read `.grok`, auth, credential, key, cookie, or token files to explain
  startup prompts. Ask for a redacted prompt excerpt if the issue recurs.
- Do not overwrite `/opt/homebrew/bin/wclass`; the user-level executable is the
  reversible precedence layer.
- Do not reuse/relabel the published `0.14.0` artifacts or protected tag for
  unreleased work.
- Do not narrow `HIGH_SIGNALS` on the calibration result. `p08`/`p21` both
  matched the `migration` signal and both passed a tier down, but that is n=2 on
  a synthetic fixture, and routing data-destroying migrations to `high` is a
  defensible posture independent of those two outcomes.
- Do not tune the classifier against the visible public fixture.
- **Do not treat `pytest -q` as the gate.** It is CI's runner, not the release
  gate's. Running it alone is how a whole test module reached `main` in a form
  the release workflow could not execute, and how `mypy` errors in `tests/`
  went unseen for a full review cycle.
- Do not let a tool that audits the suite use a different runner than the gate.
  `check_test_vacuity.py` counted pytest node IDs, which do not exist for
  `subTest` cases, so it silently lost per-parameter resolution and reported
  leak-direction tests as "passes under identity redaction".

## Next Steps

1. **Finish the release: update the Homebrew formula.** This is the only
   outstanding step for `0.15.0`. Put the published `url`/`sha256` recorded
   under Current Status into `packaging/homebrew/weightclass.rb`, then copy that
   file into the tap and verify there: `brew style ictechgy/tap`,
   `brew audit --strict --tap=ictechgy/tap weightclass`,
   `brew install --build-from-source ictechgy/tap/weightclass`, and
   `brew test ictechgy/tap/weightclass`. Confirming the published artifact from
   a clean environment (`uv tool install weightclass`) is worth doing first.
   Any fetch, push, PR, review-loop, tag, or publish needs purpose/target/scope
   stated and network approval per `AGENTS.md`. Do not reinstall
   `~/.local/bin/wclass` without asking.
2. **Run the first real advisor measurement.** The tooling is merged and gated
   but has never been pointed at real work. Both shapes need the same task
   measured under two configurations before the report will state `a`, `q`, `r`,
   or `s`; it abstains rather than mix populations. Shape B is the one worth
   trying first, because its benefit condition contains no `c` and so can be
   decided from a subscription log.
3. **The default tier is not being lowered. That question is settled for now.**
   The quality instrument was built, calibrated, and run
   (`QUALITY-INSTRUMENT.md`, `PRE-REGISTRATION-quality.md`, `QUALITY-RESULT.md`
   in the study repo). Blind pairwise review of both arms on 14 tasks: `low` won
   3 of 12 decisive tasks, 25.0%, 95% CI [8.9%, 53.2%]. Under the rule fixed
   before collection that is inconclusive, and the pre-registered consequence of
   inconclusive was **do not lower the default**.
   The reasons matter more than the tally. Six of the routed tier's nine wins
   were real defects in the cheap arm that its acceptance test passed anyway:
   `p21` accepts `True` as schema version 1, `p08` silently overwrites a
   malformed state, `p32` admits padded email-like ledger IDs, `p07` hands back
   a mutable cache list. The other three were test organisation and line
   wrapping. So the 20% token saving from routing down is not free — it is paid
   for in input validation.
   Do not reopen this by pointing at `DOWNWARD-REPORT.md`'s "all 15 passed".
   That result was always qualified as "relative to what the acceptance test
   required", and this is what that qualifier was hiding.
4. **The one cheap lever still standing is model grade, and it is measurable
   now.** A 90-pair qualification put a cheaper Codex model 69.02% below the
   stronger one on estimated API cost (95% interval [60.57%, 77.47%]) at equal
   quality (85/90 both arms), and it was rejected only for two new critical
   failures out of ninety. Those failures are mechanically detectable and
   reversible, so `docs/speculative-cheap-route-design.md` proposes running the
   cheap route in a disposable clone, verifying, and escalating only on failure.
   Expected cost is `c + p`; at c = 0.31 break-even is p = 0.69, so the cheap
   route can fail two times in three and still not lose money.
   **Measure `p` before building any of it.** `tools/speculative_run.py` and
   `tools/speculative_report.py` (merged in #48, not shipped in the
   distribution) do exactly that. If `p` lands under 20% the saving justifies
   moving the V1 boundary; near 69% the idea is dead. Note this recovers safety,
   not quality — the defects in `QUALITY-RESULT.md` all passed their tests.
5. Known follow-ups the reviews surfaced but did not gate a merge (all
   single-track or MEDIUM, recorded in the loop ledgers):
   - The multi-instruction guard is a connective word list, not a parse.
6. If the study is ever reopened, fix the three acceptance tests first
   (`p13`, `p26`, `p27`). They reject correct implementations that chose a
   different interface, which would mark a correct arm `completed: false` and
   fail the study's completion gate for reasons unrelated to routing.
7. `high` recall is still 5/15 on the public fixture. The misses are
   symptom-describing debugging requests a vocabulary classifier cannot reach.
   Treat as a separate design task, not a tuning pass. Note the calibration
   result reframes its urgency: on work of this shape, missing `high` cost
   nothing measurable.
8. If measuring routing economics, set the `medium` weight first — it is the
   counterfactual the report compares against, and without it the report
   abstains with `missing_baseline_weight`. Pass `--usage-rework` on any retry of
   an already counted task; a failed run prints
   `{"usage_hint": "record_retry_with_usage_rework"}` as a reminder.
9. Review an exact schema-3 route/fingerprint before any real run. Never launch a
   vendor merely to populate metrics without explicit task authorization.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md` and
applicable `AGENTS.md` files, then continue from: `main is a489044 with PRs #40
through #51 merged and CI green. weightclass 0.15.0 is published to PyPI under
tag v0.15.0; the only unfinished release step is the Homebrew formula, whose url
and sha256 are recorded in HANDOFF.md. The paired token study is closed: Phase 1b
found 0 tier-sensitive tasks against a pre-registered floor of 9, so routing up
is measured to buy nothing on small well-specified tasks. The one cheap lever
still standing is model grade, and the advisor tooling merged in #50 exists to
measure it but has not yet been run on real work. Before tagging anything run
python -m unittest discover -s tests and mypy --strict src tests — pytest alone
is CI's runner, not the release gate's. Never infer prices, read vendor
credentials/config, backfill task/session data, or run a vendor just to create
metrics; get network approval before any fetch, push, PR, tag, or publish.`
