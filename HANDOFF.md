# Handoff

_Last updated: 2026-09-04 KST by Claude (usability batch from the three-vendor review)_

_Flexible advisory vendor support follow-up: 2026-08-23 KST._

Current release: **0.30.0** on PyPI and the Homebrew tap. Unreleased on `main`: the scoped
agent-guidance split, the direction-research record, and explicit tier selection on `run` and
`route` — the last is a breaking CLI change and needs a minor version before it ships. Open as a
The usability batch below merged on 2026-09-04 as #186 → #191 → #188 → #189 → #190.

Per-release evidence, superseded working notes, and the abandoned approaches behind them live
in [`docs/handoff-archive.md`](docs/handoff-archive.md). This file carries only what a fresh
agent needs to continue.

## Usability batch from the three-vendor review (merged 2026-09-04)

On 2026-09-04 the same critique was put to GLM (through `packet-ask`), Antigravity (`agy`), and
Grok, each reading README, AGENTS.md, three docs, and the released `--help` text. The three
diagnoses agreed almost entirely, and the intersection became five PR-sized changes, each stacked
on the previous one and merged in order. #187 was auto-closed when its base branch was deleted and was reopened as #191 with the same commit. Every PR carried a GLM review of
its diff; the accepted and rejected findings are in each PR body.

| PR | Branch | Change |
| --- | --- | --- |
| #186 | `fix/integrations-tier-examples` | Five `docs/integrations.md` examples ran `route`/`run` without a tier source and failed on `main` with `invalid_input`. Fixed, plus a test that scans every shell block in every markdown file for the same defect. |
| #191 | `feature/run-help-and-terminal-review` | When stdout is a terminal, `run` reviews on the controlling terminal by default; `--no-review`, explicit `--json`, or `--ack-route-fingerprint` selects the non-interactive path, and a pipe never gains a prompt. Every `run`/`route` flag now has help text; `--source-vendor` and the tier group lead the usage line. |
| #188 | `docs/readme-value-first` | README leads with what the tool does, then Install, Quick start, "What you get, and what you do not", and only then the measured classifier record under "Why the classifier is opt-in". Nothing measured was deleted; the paired study got its own "Measured results for tier routing" heading. |
| #189 | `feature/minimal-top-level-help` | `wclass --help` lists seven daily commands; `profile`, `select`, `review-cost-profile`, `recommend`, `render`, `delegate`, `v2` still parse and run but are named in one epilog line. The description no longer says "Classify". |
| #190 | `feature/model-override-preset-names` | Packaged presets are `<vendor>-model-override`; the `<vendor>-cost-focused` names remain aliases everywhere. Policy files and command bytes are untouched, so no fingerprint moved. The `preset` receipt field reports the canonical name. |

What the three vendors agreed on and this batch did **not** do, because each needs a decision
rather than a PR:

- **Cheap-first with automatic escalation in one call.** The only lever the repository has
  measured as large is model grade, and the design in `docs/speculative-cheap-route-design.md`
  is still unimplemented. It does not conflict with the credential, HTTP, or task-persistence
  boundaries, but it does conflict with the V1 one-foreground-child contract in
  `src/weightclass/AGENTS.md` and with the rejected directions in `docs/routing-roadmap.md`.
  Relaxing that contract is a minor-version decision; do not slip it into an ergonomics PR.
- **Any usage or quota feedback loop that reads vendor state.** Conflicts with the product
  direction in `AGENTS.md` and stays out.
- **Context-size guards and prompt-shape hints** (warn on oversized stdin, human-readable
  `reason_code`). No boundary conflict; not started.

Things worth knowing before touching this stack:

- The installed 0.30.0 binary predates explicit tier selection, so its `--help` lacks
  `--suggest-tier`. Grok's "three front doors" finding was partly an artifact of sending that
  help snapshot; the real defect it led to was the integrations examples, fixed in #186.
- Stacked branches were rebased after each amend. If a base PR is amended again, rebase the
  branches above it with `git rebase --onto <new-base> <old-base-commit>`; a plain rebase
  replays the old base commit and conflicts.
- Each branch passed `./.weightclass/verify` (1,628 tests, 35 skips at the top of the stack),
  Ruff check and format-check, strict mypy over `src` and `tests`, and `git diff --check`.
- GLM claims that were checked and rejected: that `--review` wins over `--ack-route-fingerprint`
  (the code rejects the pair), that `delegate`/`v2`/`select` are aliases of `run`, that an
  unknown subcommand lists the hidden commands (it prints only `{"error": "invalid_input"}`),
  and a set of test-file weaknesses that turned out to be a stale diff base.
- The Homebrew formula test still calls `example-policy claude-cost-focused`; the alias keeps it
  green. Switching it to the new name is a packaging change with its own verification in
  `packaging/AGENTS.md`.

## Explicit tier selection on `run` and `route` (implemented, unreleased)

Next Steps item A is implemented for `run` and `route`. This is a **breaking CLI change** and
needs a minor version and release notes before it ships. Nothing is released yet.

The refuted classifier was still the default front door: absent `--tier`, `cli.py` classified the
task and routed on that judgement. The documentation called the classifier experimental while the
CLI ran it by default, and that gap is what this change closes.

The released-behaviour-to-be:

- `run` and `route` require **exactly one** of `--tier` or `--suggest-tier`. Neither infers a tier
  from an absent flag. The requirement is an argparse mutually exclusive group, so it fails before
  the task is read, and `SafeArgumentParser` keeps the diagnostic redacted to `invalid_input` —
  discoverability lives in the usage line, which is pinned by a test.
- `--suggest-tier` is exactly the old default judgement, plus the classifier's own record.
  `CLASSIFIER_MEASURED_AGREEMENT` in `classification.py` carries the 24-prompt blind evaluation and
  states **both** directions: agreement 10/24 (41.7%), high-tier recall 1/9 (11.1%), over-routing
  6/24 (25.0%), and under-routing 8/9 — the last is the stronger warning and the headline metrics
  hide it.
- A suggested tier **cannot start a vendor without `--review`**. `run`'s stdout belongs to the
  child and its stderr carries one closed JSON error object, so the console review is the only
  place the record can appear; without it the classifier would launch a child nobody looked at.
  Automation passes `--tier`. The refusal is the first thing `run_from_standard_input` does, so no
  later check can skip it, and it applies to the function rather than only to argv.
- An explicit tier adds **no** receipt field. The parser guarantees one of the two flags, so the
  absence of `tier_source` already means the operator chose, and the frozen schema-1 route output
  bytes in `test_legacy_contract` stay byte-identical.

Two claims were checked rather than assumed:

- **Item A's vendor half was already satisfied, and implementing it as written would have been
  wrong.** The vendor is never inferred from the task: it comes from `--source-vendor`, from
  `--preset` (which embeds it in the reviewed name), or from the policy file, whose first tier
  route pins it at `router.py:364-370` so a task cannot cross a billing boundary by declaration
  order. Making `--source-vendor` `required=True` would have broken `--preset` as redundant and
  broken the policy-file path outright. It was not changed.
- **No fingerprint migration is needed.** A suggested `low` and an explicit `low` produce the same
  route and the same `route_fingerprint`; only tier *selection* changed, not any route argv. This
  is unlike the 0.30.0 `agy` change, which did move every fingerprint for that vendor.

Verification:

- `./.weightclass/verify` exit 0 with 1,611 tests and 35 skips. Ruff check and format-check over
  258 files, strict mypy over 207 source files, compileall, and `git diff --check` all pass.
- The three new guards were shown non-vacuous by restoring each defect and watching the suite
  fail: relaxing the mutually exclusive group to `required=False`, dropping `tier_suggestion`
  from the receipt, and disabling the review requirement each broke the module.
- The first CI run failed on Linux and taught two things worth keeping. The new tests assumed a
  vendor CLI was installed, which was true on the author's machine and false on a runner, so they
  now take their routes from a temp policy file the way the rest of the suite does. They also
  spawned thirteen interpreters, and `test_redaction_is_fast_on_hostile_input` — whose own comment
  warns that a tight limit fails on a loaded machine — went 16.55 s against its 15 s bound. The
  module now runs in-process in 0.04 s instead of 5 s, so it adds no load to that measurement.
- 72 existing tests broke and were repaired without flipping what they assert. Route invocations
  took `--suggest-tier`, which is the same judgement they exercised before. Run invocations took
  an explicit tier, and the review-then-run helper now reads the tier out of its own review, which
  is the real two-step flow. `_rendered_route` checks and removes the suggestion keys the same way
  it already treats the fingerprint, because both are derived from the request rather than pinned
  per test; the contract itself is pinned in `tests/test_explicit_tier_selection.py`.
- Out-of-scope surfaces were left alone. `v2` (`cli.py:_v2_task_and_tier`) and `delegate` still
  classify when no tier is given, and edits that leaked into their tests were reverted.

Follow-ups this change did **not** take:

- `v2` and `delegate` have the same default-classify behaviour and were deliberately excluded to
  keep the diff reviewable. They should follow, and until they do the surfaces are inconsistent.
- The release itself. `RELEASING.md` makes a tag push the human approval; this branch only makes
  the change reviewable.

## Direction research with an external reviewer (2026-09-02, no code change)

Two scrubbed GLM packets explored where `wclass` and the advisory companion go next. No code,
document, or configuration outside this file changed; no vendor route ran; no campaign, usage
record, or credential was touched. This section records what the round produced and, equally,
which of its claims did not survive checking against the code.

Both packets carried only files that are already public, and the scrubber reported zero
redactions in each:

- Strategy packet: root, core, and advisory `AGENTS.md`, `README.md`, and
  `docs/advisory-product-roadmap-v2.md`, plus an inline brief assembled from this file.
  135,719 bytes, SHA-256 prefix `f4b4be0e2468`, 565 s.
- Harness-design packet: `AGENTS.md`, `docs/speculative-cheap-route-design.md`,
  `tools/check_test_vacuity.py`, `docs/policy4-fresh-blind-evaluation.md`,
  `docs/phase4-go-no-go-template.md`, and `docs/measuring-p-at-work.md`.
  77,342 bytes, SHA-256 prefix `cbff0490e80f`, 599 s.

Provider output was treated as untrusted throughout and verified against the source before
being recorded here.

### What survived checking

**A membership rule for the shipped wheel.** A surface stays in the wheel only if a first-time
user can obtain its entire honest value in a single invocation — no init, no sealed contract,
no price table, no accumulated population — and every claim it makes is enforced by code inside
the wheel. This is the advisory `AGENTS.md` definition of `ask` promoted to a project-wide rule.
It is durable because it is indexed to the user's job rather than to code quality, and the
campaign apparatus fails it by construction: its maximum payoff is `eligible_for_human_review`.

**Abandon the 60-task/12-advised-failure gate rather than re-index it.** Next Steps item C is
right that the gate counts the wrong event, but re-indexing means changing a pre-registered
counting rule to make completion easier. That is the move this project refused at Phase 2, when
it reported the 9/36 shortfall instead of lowering the floor. The alternative is to close the
natural-population study as under-powered by design mismatch, publish the descriptive record
(`s = 0/2`, retry 1 passed / 4 same / 3 degraded, 14/60 tasks, 9/12 advised failures), and
replace the instrument rather than feed it.

**The verification hypothesis reduces to a three-to-four day pilot, not a project.** The
strongest form of Next Steps item D is not injection. Running the cheap model on synthetic
tasks under the production setup and harvesting the defects it produces naturally makes the
tamper-artifact confound structurally impossible — nothing was injected, so no authorship signal
can exist — and the harvested process is the same one that produced the original finding.
Injection survives only as a supplement for classes the harvest underproduces.

**`tools/check_test_vacuity.py` is the engine, not the estimand.** Identity mutation asks
whether a suite's verdict depends on the component at all; that is a necessary condition and the
easiest mutant to catch. Semantic mutation asks whether the verdict separates correct from
plausibly wrong. A suite can pass the vacuity audit on every leaf and still miss a `p21`-class
defect entirely, because its assertions check that output is produced rather than that a
boundary rejects `True`. The reusable part is therefore the temp-copy isolation, the fail-closed
exactly-once `neutralize_source` check, `LeafRecorder`, the unrepresented-method-to-NG move, and
byte-stable output — verified present at `tools/check_test_vacuity.py:79`, `:148-155`, and
`:182-183`. Its natural role is the calibration arm: the identity operator run against each
task's reference solution is the pre-registered proof that the corpus's suites are not vacuous.

**Split `list-don't-judge` rather than transferring it.** Mechanical outcomes — suite exit code,
compile failure, diff size — are property checks and must be scored mechanically. Materiality is
the one predicate no probe covers, and it is where the judgment lives: three of the original nine
routed-tier wins were test organisation and line wrapping. The binding budget is therefore rater
hours, not model calls.

**What aggregating above the leaf hides.** Aggregating to the task hides which classes escape, so
a suite that catches five of six reads as checked. Aggregating to the class hides suite-weakness
concentration, so one class's miss rate may be three suite-poor tasks wearing a class's face.

**`p21` is a property, not a shape.** In Python `True == 1`, so the natural `if version != 1`
already admits `True`; the reference must deliberately exclude `bool`, and the defect is dropping
that exclusion. Two injections written differently satisfy the identical property and the probe
cannot separate them, which is what makes the class machine-checkable under the repository rule
that a format is judged on its own properties.

**Arithmetic that was checked and is correct.** Wilson 95% for 6/6 is `[0.610, 1.000]`; the
per-class half-width at `p = 0.6` is 24.4 points at `n = 12` and 17.9 points at `n = 25`; the
two-sided 5% critical proportion for 180 forced-choice trials is 57.3%. A leave-one-task-out
jackknife was proposed as the clustered estimator and matches the delete-one convention already
implemented at `src/weightclass/advisory/speculative_report.py:865`.

### What did not survive checking

**Item B's symbol count is one release stale.** `ask` uses eight symbols from
`speculative_run.py`, not seven: `declares_stream_json_input` was added in 0.30.0 and appears at
`src/weightclass/advisory/advisory_quick.py:1069`. The low-coupling conclusion is unaffected.
More consequentially, four other modules import that file — `managed_advisory.py` (13 references),
`advisory_consult.py` (8), `wclass_advisory.py` (3), `managed_cli.py` (1) — so extracting the
shared runtime decouples the `ask` path but does not by itself remove the 5,526-line file from
the wheel. Item B is cheaper than the rest of the move, not cheap in absolute terms.

**A small-`n` observation was labelled as evidence.** The review presented cheap acceptance 2/3
as `p ~ 0.33 < 0.69` and concluded the speculative route's economics are fine. Next Steps item 5
already states that three observations are far too few to estimate `p`, and that moving the V1
boundary needs a larger sample landing under 20%. The reviewer's operational conclusion — freeze
that work until verifier recall is known — is unaffected, but the evidence label was wrong and is
rejected.

**The proposed decision rule uses the wrong quantile and flips branch under this project's own
convention.** The review fixed `z = 1.96` throughout and concluded that at `n = 25` a point
estimate of 0.60 rejects `m <= 0.40`. `speculative_report.py:798-843` deliberately uses a
`t` quantile at small `n`, states that a narrow interval can flip a break-even judgment, and
requires rounding to the conservative table entry. Recomputed:

- 15/25 at `z = 1.96` gives a lower bound of 0.4074, which rejects.
- 15/25 at `t(df=24) = 2.060` gives 0.3983, which does not, and lands in the shortfall branch.
- Clearing 0.40 under the project's rule needs 16/25, a point estimate of 0.64.

One reclassified instance changes the outcome. The review's own table reports the 18-point width
at `n = 25` and the decision rule then ignores it.

**The proposed pilot kill gate is indexed to the wrong quantity.** It would declare the
phenomenon absent at fewer than 8 confirmed material defects in 200 runs, a 4% yield. The
original finding's own rate is six material defects across fourteen cheap-arm tasks, roughly 43%.
A floor an order of magnitude below the effect it is meant to detect cannot distinguish
reproduction from a tenfold weaker effect, and it is the same failure item C identifies in the
campaign gate. Any pilot must set this near the fixture's observed rate before it is run.

### Status

Nothing here is authorized. Next Steps items A-D remain the reviewed direction rather than a plan
of record, and this section does not promote them. It narrows item D: its strongest form is a
harvest pilot of roughly three to four days that writes almost no new code, with the two defects
above corrected first, and with the taxonomy document as the standalone deliverable that survives
either result. The reviewer's own recommendation was to build the pilot and nothing else until it
reports, and to keep the result out of the shipped tool in every branch.

The next safe action is unchanged: ordinary explicit use of `$advisory` or `wclass-advisory ask`.

## Scoped agent guidance (merged, unreleased)

`AGENTS.md` is no longer one file. PR #181 merged at `57ae7ae` split it into a root
file plus five scoped children, and `CLAUDE.md` still points at the root.

The split was **not** forced by size: the old root was 37 lines / 5,223 bytes, well under
the 250-line / 12 KiB threshold. Two things forced it.

- The root file had gone stale. Its argv exception read as if it covered the advisory
  companion, which stopped being true in 0.30.0, and it said nothing about session-scoped
  egress consent, `task_stdin_encoding`, parallel councils, or the `migrate-routes`
  obligation that a route argv change creates.
- High-stakes rules were invisible at the moment they applied. The `.weightclass/verify-review`
  seeds are `file:line` anchors checked in a +/-8 line window, so editing the cited source
  shifts them — and that rule lived only in the root, where a directory-scoped reader
  editing that file would never see it. That failure mode occurred during the 0.30.0 work.

Current layout, all under threshold with no missing links:

- `AGENTS.md` (101 lines): global rules, product direction, the scoped guidance index, and
  the documentation map.
- `src/weightclass/AGENTS.md`: the V1 one-child contract, the V2 boundary, review before
  execution, the argv exception **narrowed to core native routing**, the aggregate usage
  store, and the rule against tuning the classifier on a spent corpus.
- `src/weightclass/advisory/AGENTS.md`: the three consent sources and the standing,
  inheritable nature of the session grant; the invariant that skipping the question never
  skips the disclosure; the no-argv delivery contract; sealed-population migrations; and the
  Skill bundle ledger procedure.
- `tests/AGENTS.md`: that the gate is `./.weightclass/verify` and not `pytest -q`, what a new
  test must prove, and how to change an existing assertion without silently flipping it.
- `.weightclass/AGENTS.md`: never edit a verifier to make a candidate pass, how to re-anchor a
  shifted seed, and the exit-42 baseline-probe contract.
- `packaging/AGENTS.md`: the formula is the source of truth, verification only counts inside a
  tap, the hashed PyPI URL, and testing an exact entrypoint.

Verification: a mechanical sweep of 25 contract phrases from the original file against the new
set caught two directives the drafting pass had dropped (`patch-only handoff` and the bounded
`cheap/advisor/retry/expensive` sequence); both were restored before commit. The audit script
reports six files, all under threshold, zero missing links, no marker blocks.
`.weightclass/verify` exit 0 with 1,599 tests and 35 skips; Ruff, strict mypy over 206 source
files, and `git diff --check` pass; CI was 8/8.

One existing test changed. `test_install_skill_is_the_documented_vendor_path_exception`
required the phrase in the root file. The contract it protected was that the exception is
documented in agent guidance, so it now asserts the rule in the scoped file that governs the
installer **and** that the root still links there. That is stricter than before, and the
change was stated rather than flipped silently.

This is documentation only; no packaged behavior changed and no release is required for it.

## One-shot advisory usability batch (released in 0.30.0)

Implemented, externally reviewed, published, and installed. Implementation PR #177 merged at
`d1a123a`; release PR #178 and tag `v0.30.0` point to exact release commit `8062cfb`.
Source-formula PR #179 and tap PR `ictechgy/homebrew-tap#39` are merged.

The released behavior:

- **No built-in advisory route carries a task in argv.** agy reads the prompt from one NDJSON
  message on standard input under `--input-format stream-json`, and the CLI itself rejects an argv
  prompt in that mode, so the exposure is closed by the vendor rather than narrowed by us. Verified
  against agy 1.1.23. The stdin payload is wrapped only when a route *declares* that input format,
  judged by argv token position rather than executable name. A route that declares it while also
  carrying a `{{task}}`/`{{task_file}}` slot is rejected instead of resolved in one direction.
  The agy local capability check now requires `--input-format`, so an older agy fails closed before
  any task is read. Route review and `--preview` report `task_stdin_encoding`.
- **Councils start their members together.** Wall clock is the slowest member, not the sum, and the
  whole-council deadline is shared instead of starving the members requested last. Each member's
  timeout is fixed at submission, results are always in requested order, a failed peer is not
  cancelled, and the worktree is compared once before and once after the whole council.
  `advisory_parallel.run_parallel` was not reused: it is command-oriented and cannot carry grok's
  anonymous `{{task_file}}` pipe.
- **Egress consent can be granted for one shell** with `WCLASS_ADVISORY_EGRESS=session`, which must
  equal exactly that value. Nothing is written to disk, because `AGENTS.md` forbids repository paths
  and timestamps in advisory state. Skipping the question does not skip the disclosure: the vendor
  list, delivery mode, and context warning still print. Receipts record
  `task_egress_confirmation_source`; preview reports `session_egress_grant_active`. The variable is
  absent from the vendor child environment allowlist.
- **`--human` preview is a summary**, not a raw receipt dump. Arguments over 120 characters render
  as a length plus a SHA-256 prefix; `--json` keeps exact bytes.
- **Skill onboarding generation 18** removes the now-false argv-delivery instruction, states the
  parallel council contract, and documents the session grant.

Review and verification evidence:

- One scrubbed GLM packet covered the two changed advisory modules: 74,400 bytes, SHA-256 prefix
  `48fe159e668c`. Provider output was treated as untrusted. Four retained findings were fixed: the
  session grant deleted the only per-run disclosure; the member-exception path filled failed slots
  with a misleading `ask_cli_unavailable`; the reviewable surface hid the stdin envelope; and the
  agy version floor was silent. Two provider findings were checked and rejected with evidence —
  `MAX_COMMAND_TOKEN_BYTES` is 4,096 (the reviewer saw a packet-scrubber false positive), and
  `bounded_capture` rejects only non-positive timeouts, so a small budget times out normally rather
  than raising. The provider's concurrency analysis independently matched ours.
- Full local gates pass 1,599 tests with 35 skips, Ruff check and format-check, strict mypy over
  206 source files, compileall, `git diff --check`, and the Skill ledger against `v0.29.0`.
  Extracted-sdist isolation passes 1,592 tests with 80 skips. `cli-check --vendor all` reports
  codex 0.152.0, claude 2.1.252, agy 1.1.23, and grok 1.0.13 all `ready`.
- Implementation, release, and source-formula PRs each passed all eight Linux Python 3.10-3.14,
  macOS Python 3.10/3.14, lint, type, and build checks.
- Live end-to-end against the real agy CLI on the released Homebrew build: `ask --vendor agy
  --workflow review --context task` returns a valid schema-1 result with `task_delivery: "stdin"`.
  A live `--council agy,codex` finished in 13.9 s and preserved the partial result with exit 3; the
  codex member's `ask_executor_failed` reproduces on a single-vendor run and is a local CLI
  condition unrelated to this batch.

Release and installation evidence:

- Release run `33524435074` passed the immutable Python 3.13 build, macOS 3.10/3.14 boundaries,
  Python 3.10/3.14 candidate validation, the protected PyPI environment approval, exact publication,
  and final GitHub Release creation.
- PyPI exposes one non-yanked wheel with SHA-256
  `3caa9331fcfd719fb69df81f3ed3f05dbda222bb6c3a5bf7cfdbc75afc1ffeb2` and canonical sdist
  `https://files.pythonhosted.org/packages/1b/8b/77f6c09def78ee43c495be674bcfbcc822c584d972e3c78ba543811cce53/weightclass-0.30.0.tar.gz`
  with SHA-256 `37fb319cdb9fec9e60bbec1a027921ac9f546d98ff35a3c41d78ffafac2bf5ae`.
- GitHub Release `v0.30.0` is final, non-prerelease, and latest. The tap formula passed targeted
  style, strict audit, source upgrade, and `brew test`. Whole-tap style still has one unrelated
  pre-existing `relay.rb` component-order warning.
- The user-level uv tool and the exact Homebrew binary both report `0.30.0`. Exact 0.29.0 Codex and
  Claude Skill bundles upgraded to generation 18 and both now report `already_installed`.

Two breaking changes are recorded in `.github/release-notes/v0.30.0.md`:

1. Schema-1 `agy` advisory route argv changed, so every `agy` profile digest and route fingerprint
   changed with it. A sealed campaign population bound to an older `agy` route must complete
   `migrate-routes --vendor agy` before its next dispatch. No existing record is rewritten.
2. Egress consent became expressible as configuration state. The gate was strictly per action
   before; a shell variable now authorizes every later call in that shell and every descendant
   process. This is deliberate and is what makes non-interactive use possible, but it changes the
   nature of the gate, not only its ergonomics.

No real project task or campaign was dispatched, no credential or vendor configuration was read, and
no persistent campaign/usage record was changed. The next safe action is ordinary explicit use of
`$advisory` or `wclass-advisory ask`.

Open items this batch did **not** address:

- Core `wclass` native `agy` and `grok` routes still deliver the task in argv, so the `AGENTS.md`
  exception stands for native routing. Moving core `agy` to stdin would change every built-in `agy`
  route fingerprint and needs its own release handling.
- The `agy` CLI applies its own `--print-timeout`, defaulting to five minutes, while the advisory
  per-child ceiling is 3,600 seconds. A long agy review can be cut off by the vendor CLI before the
  reviewed ceiling. This predates the batch.

## Goal

- Maintain `weightclass` as a public, local router that can discover and let
  the user select installed Codex, Claude Code, Antigravity (`agy`), and Grok
  agents/models/efforts without hand-writing vendor commands.
- Allow explicit reviewed cross-agent/schema-3 native execution while keeping
  the default source-vendor boundary and one-foreground-child contract.
- Measure whether routing helps cost using opt-in local aggregate counters and
  user-supplied relative weights, without inferring prices, subscription usage,
  entitlement, or persisting task content.

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
  (a separate study repository outside this repo), deliberately not vendored here.

## Key Files & State

- `HANDOFF.md`: this restart-safe state. A rewrite that drops required strings turns
  `tests/test_completion_audit_v2.py` red in the working tree while CI stays
  green. Keep the `docs/completion-audit-v2.md` line below.
- `docs/completion-audit-v2.md`: requirement-to-test completion map.
  Goal g12 is leader-verified; retain this audit connection when refreshing
  this file. Both that path and that sentence are asserted verbatim by
  `test_handoff_points_to_current_g12_audit`, so do not let a rewrap split them.
- `AGENTS.md`: global rules and the scoped guidance index. Path-specific rules now live in
  `src/weightclass/AGENTS.md`, `src/weightclass/advisory/AGENTS.md`, `tests/AGENTS.md`,
  `.weightclass/AGENTS.md`, and `packaging/AGENTS.md`. Read the one that governs the subtree
  you are about to change; a rule you need is often not visible from the repository root.
- `docs/paired-token-study.md`: the closed study — design, pre-registered gate,
  and the Phase 1b result that stopped it.
- `src/weightclass/classification.py`: classification policy 4.
- `docs/policy4-fresh-blind-evaluation.md`: aggregate-only protocol and result
  from the fresh 24-prompt blind direction check.
- `docs/security-performance-followup.md`: current security findings,
  cold-start/CI evidence, implemented local fixes, and deferred architecture.
- `src/weightclass/advisory/advisory_campaign.py` and
  `managed_advisory.py`: schema-3 primary gate sealing, source-generation
  exploration, provider-role checks, and managed generation selection.
- `src/weightclass/advisory/readonly_snapshot.py`: bounded no-follow evidence
  mutation detection and relocated-workspace identity search; successful
  evidence still verifies in a fresh clean clone.
- `README.md`: public 0.20 advisory onboarding, gate, provider concurrency,
  snapshot boundary, and onboarding-14 skill upgrade guidance.
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

- Reading a vendor CLI's own refusal message instead of designing around its documented
  shape. `agy --print` was documented here for a year as argv-only. Probing
  `--input-format stream-json` produced the error "a prompt given on the command line would
  be ignored", which says the CLI *enforces* stdin-only in that mode. The exposure was then
  closed by the vendor rather than mitigated by us, and the route lost its task slot entirely.
- Verifying an external reviewer's findings against the code before acting. Of six findings in
  the 0.30.0 GLM review, four were real and two were wrong: `MAX_COMMAND_TOKEN_BYTES` is 4,096
  (the reviewer saw a packet scrubber's false positive and called it a ship-stopper), and
  `bounded_capture` rejects only non-positive timeouts, so a small budget times out normally
  rather than raising. Both rejections took one grep each. Accepting them would have produced
  two pointless changes and one false "fixed" claim.
- A mechanical phrase sweep when moving prose that carries contracts. Diffing 25 contract
  phrases from the old `AGENTS.md` against the new file set caught two directives that a
  careful read-through had already missed.
- Splitting a commit by hunk with `git apply` on a filtered patch, then proving the reassembled
  tree was byte-identical to the pre-split state before committing any of it.

## What Did Not Work / Avoid

- Do not claim aggregation already happened: the store is enabled but has zero
  real runs and zero weights.
- Do not infer or scrape provider pricing, bills, quotas, credentials, auth, or
  raw session histories. Do not retroactively synthesize usage.
- Do not read `.grok`, auth, credential, key, cookie, or token files to explain
  startup prompts. Ask for a redacted prompt excerpt if the issue recurs.
- Do not assume plain `wclass` exercises the Homebrew build: the user-level
  executable can shadow the separate Homebrew entrypoint, although both now
  report 0.22.0. Test an exact entrypoint when packaging provenance matters.
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

- **Do not trust `gh pr checks` without comparing the head ref.** After pushing a fix commit,
  `gh pr checks` reported 8/8 green — for the *previous* commit, because GitHub had not yet
  refreshed the PR head. The mistake was caught only because the merge was refused as "out of
  date". Compare `gh pr view <n> --json headRefOid` against local `HEAD` before reading any
  check result, and treat `mergeStateStatus: UNKNOWN` as "not yet computed", not as a failure.
- Do not assume an existing coordinator is reusable because it is described as parallel.
  `advisory_parallel.run_parallel` was claimed here as a drop-in for `ask --council`; it is not.
  It is command-oriented with a `stdin_bytes` field and cannot carry grok's anonymous
  `{{task_file}}` pipe, so the council needed its own bounded pool over `_run_member`.
- Do not act on a review finding about a constant without checking the constant. A scrubbed
  review packet can redact a numeric literal, and the reviewer will then reason about
  `"[REDACTED]"` as if it were the source.
- Do not edit a file cited by `.weightclass/verify-review` without re-checking its seeds.
  Adding lines above a seed shifts the anchor even when the reviewed behavior is untouched.
  Re-anchor to the same symbol; never widen the window or drop the seed.

## Next Steps

**Standing strategic finding (2026-09-02).** Four independent reviews of this project —
Antigravity, Grok, GLM, and the session agent — converged on the same conclusion without
seeing each other's answers, and it should be read before choosing any next task.

The headline hypothesis is refuted by this project's own pre-registered study, and the
classifier is still the front door of `wclass run`. Repository adoption is measurably zero:
0 stars, 0 forks, 0 watchers, and 0 issues at one month old, against 633 commits and 30
releases. The project has instrumented its release process exhaustively and its demand not at
all. That also means the cost of deleting surface area is near zero right now, which will not
stay true if adoption ever arrives.

All four reviews agreed on five items. All five now have work behind them; B, C, and D do not.

- **A. Demote tier classification from the front door. Done for `run` and `route`, unreleased.**
  Both now require exactly one of `--tier` or `--suggest-tier`, a suggested tier cannot start a
  vendor without `--review`, and the suggestion carries `CLASSIFIER_MEASURED_AGREEMENT`. The
  vendor half of this item was **rejected as written**: the vendor is never inferred from the
  task, so making `--source-vendor` required would have broken `--preset` and the policy-file
  path. `v2` and `delegate` still classify by default and are the remaining inconsistency.
  See the explicit-tier section at the top of this file.
- **B. Move the campaign apparatus out of the shipped tool.** `experiment`, `portfolio`,
  `campaign-gate`, `seal`, and `dispatch` are research machinery whose maximum payoff, by this
  repository's own contracts, is permission to request human review. `ask` is the product. A
  concrete first step exists and is cheap: `ask` uses only seven symbols from the
  5,463-line `speculative_run.py` (`run_child`, `extract_evidence_result`, `default_child_env`,
  `AGENT_SCAFFOLDING`, `CHILD_TIMEOUT`, `RunFailure`, `MAX_TASK_FILE_BYTES`). Extracting those
  into a small shared runtime module decouples the shipped path from the research runner and
  makes the "vacuity anchors cannot move" constraint irrelevant to `ask`. That symbol
  count is one release stale; see the direction-research section above.
- **C. The 60-task / 12-advised-failure gate is indexed to the wrong quantity.** The event it
  counts is an advised failure, so the cheap route succeeding — good product news — starves the
  study. That is why the population sits at 14/60 tasks while already at 9/12 advised failures.
  Separately, the mechanism under test is currently harmful: of eight cheap verification
  failures, one retry passed, four stayed the same, and three degraded to a worse failure class.
  Accumulating natural work to n=60 on v1 retry shaping measures a prototype, not a mechanism.
- **D. The next hypothesis with real support is verification, not routing.** Six of the routed
  tier's nine blind-instrument wins were real defects that the cheap arm's acceptance tests
  passed anyway (accepting `True` as schema version 1, silently overwriting malformed state,
  admitting padded email-like ledger IDs, returning a mutable cache list). The router did not
  win; the cheap arm's verifiers lost. An injected-defect harness built from those documented
  classes would be publishable, unlike the current private fixture, and would fix the
  reproducibility gap in the headline result for any future study.

None of A-D is authorized by this file. They are the reviewed direction, not a plan of record;
converge with the owner before starting one.

1. **Collect real advisory evidence without mislabeling failures.** Ten lanes
   per vendor/workflow are available, but sample caps remain independent of
   lane availability. Treat only `managed_lane_unavailable` as contention;
   report `managed_campaign_capacity_reached`, `campaign_record_*`, and generic
   dispatch rejection by their exact codes. Do not synthesize samples, repair
   fingerprints, or merge sealed populations. The legacy local implementation
   population is still unhealthy by design; use only an independently valid,
   explicitly selected managed population. For a formal new claim, run
   `migrate-gate` before the first dispatch and choose exactly one primary
   vendor/workflow in that managed state root. The pre-gate source remains
   available only for exploratory, never promotion-eligible analysis.
2. **Verified-object execution remains an open architecture item.** Current
   double observation narrows replacement but `Popen` still resolves a path.
   Before enforcing safe ancestors, settle sticky-directory and group-writable
   installation semantics and run the macOS/Linux compatibility matrix in the
   completed hardening plan. Do not claim the medium finding is fixed by another
   metadata comparison.
3. **Custom usage-store ancestry remains a low residual.** The parser is fixed,
   and lock/read/temp/replace/cleanup/fsync transactions are parent-directory-fd
   anchored: every one of them is relative to a single opened and revalidated
   private parent descriptor, and a deterministic parent-swap test proves a
   replacement directory is not written and the staged file is recovered. The
   remaining residual is ancestor pathname resolution performed
   before the parent is opened. That is narrower than the original
   finding and is not closed. Prefer the default private home location;
   implement ancestor admission only after the sticky and shared-group rules are
   fixed, or move directly to a dirfd transaction if privileged/shared-tree
   support becomes a requirement.
4. **The default tier is not being lowered. That question is settled for now.**
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
5. **The one cheap lever still standing is model grade, and it is measurable
   now.** A 90-pair qualification put a cheaper Codex model 69.02% below the
   stronger one on estimated API cost (95% interval [60.57%, 77.47%]) at equal
   quality (85/90 both arms), and it was rejected only for two new critical
   failures out of ninety. Those failures are mechanically detectable and
   reversible, so `docs/speculative-cheap-route-design.md` proposes running the
   cheap route in a disposable clone, verifying, and escalating only on failure.
   Expected cost is `c + p`; at c = 0.31 break-even is p = 0.69, so the cheap
   route can fail two times in three and still not lose money.
   **Measure `p` before changing core routing.** The installed
   `weightclass.advisory.speculative_run` and `speculative_report` modules do
   exactly that. The latest three real maintenance tasks had
   cheap acceptance 2/3, far too few to estimate `p`; if a larger sample lands
   under 20% the saving may justify moving the V1 boundary, while near 69% the
   idea is dead. Note this recovers safety, not quality — the defects in
   `QUALITY-RESULT.md` all passed their tests.
6. **Advisor adoption remains undecided.** Two corrected Shape-B failures now
   have observed rescue `s=0/2`, and neither study had a price-derived `a` or
   `r`. Deterministic installed profiles now compile operator-selected
   Claude and Codex model/effort labels into the same reviewed argv for seal and
   run, require explicit task-egress confirmation, and support disjoint Codex
   cached-input pricing. Current Claude Sonnet/Opus and Codex Luna/Sol synthetic
   stdin probes exited successfully, but they are connectivity checks, not
   evidence. The separate sealed campaigns still need at least 60 usable tasks
   and 12 advised failures each under a user-supplied single-origin price table;
   see `docs/advisory-vendor-profiles.md`. Do not integrate retry/advice into
   core `wclass` from these pilots; the companion remains explicit and experimental.
7. **Policy 4 needs broader high-tier evidence before another classifier
   change.** The public fixture remains 5/15 high recall; the fresh blind
   direction check found 1/9 with a wide interval and sent the other eight to
   `standard`. Do not tune on either visible corpus. A new policy candidate
   requires a new independently generated, rated, and sealed corpus.
8. If the paired token study is ever reopened, fix the three acceptance tests first
   (`p13`, `p26`, `p27`). They reject correct implementations that chose a
   different interface, which would mark a correct arm `completed: false` and
   fail the study's completion gate for reasons unrelated to routing.
9. If measuring routing economics, set the `medium` weight first — it is the
   counterfactual the report compares against, and without it the report
   abstains with `missing_baseline_weight`. Pass `--usage-rework` on any retry of
   an already counted task; a failed run prints
   `{"usage_hint": "record_retry_with_usage_rework"}` as a reminder.
10. Review an exact schema-3 route/fingerprint before any real run. Never launch a
   vendor merely to populate metrics without explicit task authorization.

## Resume Prompt

Open the current repository checkout, read `HANDOFF.md`, the root `AGENTS.md`, and
the scoped `AGENTS.md` for whatever subtree you will touch, then continue from:
`weightclass 0.30.0 is published on PyPI, GitHub Releases, and Homebrew.
Implementation PR #177, release PR #178, source-formula PR #179, tap PR #39,
handoff PR #180, and scoped-guidance PR #181 are merged. Tag v0.30.0 points to
reviewed release commit 8062cfb and Release run 33524435074 completed every gate
including the protected PyPI approval. Both the local uv tool and the exact
Homebrew entrypoint report 0.30.0, brew test passes, and the Codex/Claude
advisory skills are exact onboarding-18 bundles. No built-in advisory route
carries a task in argv: agy reads its prompt from one NDJSON message on stdin
under --input-format stream-json and the CLI itself rejects an argv prompt there.
Councils start their members together under a shared deadline and report in
requested order. Egress consent may be granted for one shell with
WCLASS_ADVISORY_EGRESS=session, which never touches disk and never skips the
per-run disclosure. Agent guidance is now a root AGENTS.md plus scoped children
under src/weightclass, src/weightclass/advisory, tests, .weightclass, and
packaging; read the one governing your subtree because the rule you need is often
invisible from the root. Core wclass routing, the one-child contract, campaign
records, gates, and sealed manifests are unchanged, and core native agy/grok
routes still deliver the task in argv. Verified-object execution, external
hostile-code sandboxing, custom usage-store ancestry, and agy's own five-minute
--print-timeout remain documented residuals. A four-way review recorded under
Next Steps found the tier-routing hypothesis refuted by this project's own study
and repository adoption at zero; treat items A-D there as reviewed direction
needing owner agreement, not as authorized work. The advisory companion remains
explicit and experimental; no campaign gate may authorize core routing. Never
infer prices, read vendor credentials/config, backfill task/session data, or
reuse a published version or tag.`
