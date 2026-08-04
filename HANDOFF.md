# Handoff

_Last updated: 2026-08-04 by Claude Code_

## Goal

- Maintain public, local `weightclass`: a deterministic task router for native
  Codex and Claude Code workflows, with optional reviewed API routing through
  a separately distributed runtime.

## Current Status

- Main repository: `https://github.com/ictechgy/weightclass`, branch `main`,
  tracking `origin/main` at commit `9da2039` (PR #1 and #2 merged).
- Separate runtime repository: `https://github.com/ictechgy/weightclass-runtime`,
  branch `main`, tracking `origin/main` at commit `fe28566`.
- Both repositories are public-source ready. Main-repository CI is green on
  `main`. `weightclass 0.2.0` is published to PyPI and installable with
  `brew install ictechgy/tap/weightclass` or `pip install weightclass`.
- The main worktree has unrelated `.omc/` and `.serena/` directories holding
  local agent state. They are now in `.gitignore`; 172 of these files were
  committed by mistake in `fd1fbc0` and reached `origin/main` before being
  untracked. They contain no credentials and no conversation text, but they do
  carry local absolute paths, a session title, and session UUIDs. They remain in
  git history; purging that needs a force-push and has not been done.

## Completed

- Contract-hardening pass on branch `fix/router-contract-hardening` (PR #1).
  An audit found that several documented guarantees were not upheld:
  - Task text is now read as bounded UTF-8 bytes and handed to the child as
    UTF-8 bytes. Previously invalid UTF-8 produced a traceback instead of a
    redacted diagnostic, `text=True` leaked task characters through
    `UnicodeEncodeError` under a non-UTF-8 locale, and the size limit was
    checked only after buffering the whole stream (443 MB RSS for a 200 MB
    input, now 24 MB).
  - A V1 route no longer has a `model` field; a policy declaring one is
    rejected. It was render-only, so a reviewed descriptor could advertise one
    model while `wclass run` executed another. Requiring the label to appear in
    `command` was tried first and rejected in review: membership of any token
    still admits `model: "haiku"` alongside `--model opus --append-system-prompt
    haiku`, while legitimate `--model=opus` and `-c model=X` forms were refused.
    Verifying a label properly needs vendor CLI semantics the tool declines to
    assert, so the model is declared once, inside `command`.
  - Vendor is pinned even without `--source-vendor` (to the vendor of the first
    declared tier route), and `vendor` is always present in `wclass route`
    output. Previously the high tier silently crossed to a second vendor.
  - Built-in Codex routes differentiate tiers via
    `-c model_reasoning_effort=low|medium|high`; all three were identical.
  - ASCII classification signals match on word boundaries for both tiers, and
    Korean/inflected vocabulary was extended.
  - The CLI is argparse subcommands with `--help`, `--version`, and
    `allow_abbrev=False`; `--c` used to satisfy `--confirm-api-egress`.
  - Tests: 27 -> 54. Each guard listed under Verification was mutation-checked:
    deleting it fails at least one test.
  - Two independent reviews (Claude, Codex) then found that applying word
    boundaries to HIGH signals had dropped every inflected form
    (`credentials`, `migrations`, `race conditions`, `refactoring`), which
    combined with the widened LOW vocabulary to move
    "Reformatting the credentials file" from `high` to `low`. Fixed by allowing
    common suffixes inside the boundary. Codex additionally found that
    `data loss` missed its hyphenated spelling and that argparse's built-in
    version action exited before validating the rest of argv.
- Executor-result pass on branch `fix/executor-result-reporting` (PR #2),
  prompted by running the real vendor CLIs end to end for the first time:
  - The built-in Claude command used `--permission-mode manual`. Print mode is
    non-interactive, so every edit was refused and `claude` still exited `0` —
    the router reported success having changed nothing, while the Codex route
    could write. Now `acceptEdits`, which auto-accepts file edits only; Codex's
    `workspace-write` still also runs commands, so the asymmetry is narrowed,
    not removed.
  - `run` and `v2 run` no longer return the child's exit code. A non-zero child
    yields exit `7` plus `{"error": "executor_failed", "executor_exit_code": N}`
    (or `executor_signal`) on a fresh line, so the diagnostic is always the last
    line of inherited standard error and stays parseable after unterminated
    child output. Previously a child exiting `3` was indistinguishable from
    `unsupported_route`, and signal deaths were mangled into values like 241.
  - Two reviews (Codex, Claude) ran on this PR and found non-overlapping
    defects. Codex found that the first commit's justification for `acceptEdits`
    was false — `route` and `run` read the policy independently, so reviewing a
    route does not bind the command a later `run` executes. That claim was
    retracted and the limitation documented. Claude's reviewer found the stderr
    concatenation defect above.
- Vendor-CLI triage (0.2.0). The keyword classifier was measured against 40
  tasks rated by three independent raters (unanimous on 39): it agreed 15 times.
  Repairing a real inflection defect — `deployment` matched, `deploy` did not,
  10 of 17 signals affected — moved it to 15/40, i.e. not at all, because the 12
  misses contain no signal vocabulary at all. People describe hard problems in
  plain language. Asking an already-installed vendor CLI scored 33/40 with zero
  over-rating. A pre-registered hybrid ("keyword high wins, else vendor") scored
  31/40 and was rejected; `max(local, vendor)`, suggested by both reviewers as
  an injection defence, scored 21/40 and was rejected with that measurement.
  - `wclass classify --source-vendor X --ask-vendor` is opt-in, terminal, and
    fails closed with exit `8` rather than reverting to keywords.
  - `route` and `run` never contact a vendor to classify; they accept `--tier`.
  - Both triage commands are pinned non-mutating (`--permission-mode plan`,
    `--sandbox read-only`) because the prompt carries untrusted task text.
  - The corpus and scorer are committed, so the figures above are reproducible.
- `weightclass` package metadata exposes the `wclass` command and uses MIT.
- Main CI installs the package, runs the installed CLI, and runs tests on
  Python 3.10, 3.12, and 3.13.
- `docs/integrations.md` documents explicit, reviewable Codex and Claude Code
  invocation with `--source-vendor`; it never changes vendor-global config.
- The classifier has static, non-sensitive regression coverage for deployment
  rollback, privacy, credentials, Korean operational tasks, whitespace, and
  punctuation edits.
- `weightclass-runtime` has the public package/CLI name, MIT license, CI,
  offline tests, and checksum release guidance in `RELEASING.md`.

## Key Files & State

- `README.md`: installation, classification behaviour and its measured ceiling,
  native/API boundaries, and local usage.
- `src/weightclass/triage.py`: asks an installed vendor CLI for a tier. Owns the
  rubric prompt so it cannot drift into another repository.
- `tests/eval/`: the 40-task corpus behind the accuracy figures, plus
  `score.py`, which re-derives them offline.
- `RELEASING.md`: version/tag policy, PyPI Trusted Publishing setup, and the
  Homebrew formula update procedure.
- `docs/integrations.md`: safe Codex/Claude invocation snippets.
- `src/weightclass/classification.py`: local deterministic tier selection; no remote
  triage or persistence.
- `src/weightclass/v2.py`: API route review/acknowledgement; it launches only a
  supplied external runtime and never handles credentials or HTTP.
- `tests/test_classification.py`: regression examples for tier selection.
- `.github/workflows/ci.yml`: clean-install and test verification for the main
  package.
- Sibling runtime repository `/Users/jinhongan/Desktop/sar-provider-runtime`:
  separate API runtime; read its `AGENTS.md`, `README.md`, and `RELEASING.md`
  before changing it.

## Important Context / Decisions

- Task text is transient standard input only: never persist, log, echo, hash,
  or include it in diagnostics.
- `--source-vendor codex|claude` is explicit. Native routes remain on their
  source vendor unless a reviewed policy sets `allow_mixed_vendors: true`.
- Model and effort arguments are opaque, user-reviewed values that live inside
  a policy route's `command`. A route carries no separate `model` label, since
  the tool cannot verify one without asserting vendor CLI semantics. Never infer
  entitlement, pricing, remaining usage, or model availability.
- V2 permits only declarative `openai`/`anthropic` API routes. It needs both
  `--confirm-api-egress` and the exact reviewed fingerprint. The runtime stays
  separate; do not turn the main tool into an API client or credential manager.
- Runtime credentials are inherited environment variables only. Never access
  `.env`, keychains, auth files, shell profiles, or real provider endpoints
  without explicit approval.
- Use an immutable runtime path plus a published SHA-256 checksum. The main
  tool fingerprints the path, not file contents, so same-path replacement is a
  known TOCTOU risk.

## Verification

- Ran in the main repository:
  `PYTHONPATH=src python3 -m unittest discover -s tests`
  - Result: 95 tests passed. No test invokes a real vendor CLI; the triage
    tests put a fake executable on `PATH` so the real pipe handling is still
    exercised.
- Installed `weightclass 0.2.0` from PyPI and from the Homebrew tap in clean
  environments and ran the documented commands.
  - Result: both report `weightclass 0.2.0` and behave as documented.
- Differential-classified 760 generated tasks against `main` to bound the
  classifier change.
  - Result: the only downgrades from `high` are the two intended ones
    (`reproduction`, `preproduction` no longer matching `production`); every
    `standard` -> `low` move contains no high-tier word.
- Ran in the main repository:
  `PYTHONPATH=src python3 -m compileall -q src`
  - Result: passed.
- Reproduced the committed CI job locally in a clean venv: `pip install .`,
  then the installed-CLI check, then the suite against the installed package.
  - Result: all three steps passed; `wclass --version` reports
    `weightclass 0.1.0` from the single `weightclass.__version__` source.
- Mutation-checked the new tests by deleting each guard in turn: vendor filter,
  vendor pinning on tier routes, `allow_api`, runtime path validation, the
  stdin byte bound, `allow_abbrev=False`, `--version` exclusivity, signal
  inflection, and the hyphenated multi-word separator.
  - Result: every deletion failed at least one test. Two tests that did not
    were rewritten, and one missing test was added, before this was true.
- Ran in the runtime repository:
  `PYTHONPATH=src python3 -m unittest discover -s tests`
  - Result: 8 tests passed; fake connections only.
- Ran in the runtime repository:
  `PYTHONPATH=src python3 -m compileall -q src`
  - Result: passed.
- Verified runtime missing-credential behavior with keys removed from the
  process environment: exit code `10`, redacted diagnostic, no network call.
- Local Python installations lack `setuptools`, so a local wheel build was not
  run. The committed CI workflows are intended to validate clean installation.
- Ran both built-in native routes end to end against the real vendor CLIs, from
  an installed `wclass` in a clean venv, inside a throwaway git repository.
  - Codex: created the requested file, verified it itself, exit `0`.
  - Claude with `manual`: refused the write, created nothing, exit `0` — the
    defect PR #2 fixes. With `acceptEdits`: created the file.
  - Codex outside a git repository: exits `1` ("Not inside a trusted directory
    and --skip-git-repo-check was not specified"). Left as-is; it fails closed.

## Blockers & Open Questions

- Main-repository CI passes on Python 3.10, 3.12, and 3.13 (install,
  installed-CLI check, tests). The runtime repository's first GitHub Actions
  run has still not been inspected.
- Known limitations carried deliberately, each documented where it applies:
  `wclass run` binds a review only when given `--ack-route-fingerprint`, and the
  binding covers the policy's selection — not the task (binding a task would
  require hashing it, which this project forbids) and not the identity of the
  executable behind the argv; V2 likewise fingerprints the runtime path, not its
  contents; a task of 1,200+ characters is `high` on length alone; a vendor CLI
  that declines work while exiting `0` cannot be detected.
- Releasing works end to end and has been exercised twice (0.1.0, 0.2.0).
  Push a `v*` tag; `.github/workflows/release.yml` re-runs every gate, refuses a
  tag that disagrees with `weightclass.__version__`, and uploads to PyPI through
  Trusted Publishing. No token is stored anywhere. Then update
  `packaging/homebrew/weightclass.rb` with the new sdist URL and sha256 and copy
  it into `ictechgy/homebrew-tap`. `RELEASING.md` has the exact commands.
- The vendor triage path under-rates 7 of the 15 genuinely hard tasks in
  `tests/eval/corpus.json` and never over-rates — a bias toward the cheap tier,
  which is the dangerous direction. Improving it is prompt work, but tuning
  against that corpus would measure the tuner: build a fresh corpus first.
- `HIGH_SIGNAL_NO_INFLECTION_RATIONALE` defers one decision on purpose:
  whether bare `race` should be a HIGH signal. It over-fires in ordinary
  English but `race condition` misses "fix the race in the reconnect path".
  Decide it with a measurement, not by omission.
- Provider model/effort compatibility and actual API behavior remain
  user/provider dependent; do not test with real credentials without approval.

## What Worked

- Offline tests use fake provider connections and preserve the no-egress
  boundary. The triage tests extend this by putting a fake executable on `PATH`
  rather than mocking `subprocess`, which is what makes the output cap, the
  timeout, and stderr discarding testable at all.
- Explicit source-vendor commands make native routing reviewable without
  vendor configuration changes.
- **Blind evaluation.** Tasks were generated by one agent, rated by three others
  that never saw classifier output, and only then scored. The raters agreed
  unanimously on 39 of 40, which is what makes the corpus worth measuring
  against. Every accuracy claim in this repository comes from that method.
- **Mutation testing found roughly ten tests that could not fail.** Delete the
  guard, re-run, and see whether anything breaks. It caught a timeout test that
  passed with the timeout disabled (the fake vendor exited on its own, so the
  same exception was raised — just slowly), a rubric test that survived
  replacing the rubric, and an input-validation guard with no test at all. Assert
  elapsed time or a side effect, not merely that an exception occurred.
- **Two independent reviewers with different prompts.** Across four review
  rounds they overlapped on roughly one finding each time; everything else was
  disjoint. One found the unbounded output buffer, the other found that only
  `codex` had a read-only pin while `claude` had none.

## What Did Not Work / Avoid

Each of these was measured against `tests/eval/corpus.json`. Re-deriving them
costs one command; re-arguing them costs a day.

- **Adding vocabulary does not fix classification.** Repairing a genuine
  inflection defect (10 of 17 signals; `deployment` matched, `deploy` did not)
  moved agreement 15/40 → 15/40. The 12 misses contain no signal vocabulary at
  all, because people describe hard problems in plain language: "balances
  sometimes go negative", "the same job runs twice when a pod is rescheduled".
  The inflection fix was kept — it is correct for tasks that do use those
  words — but it is not a path to accuracy.
- **Combining the keyword tier with the vendor tier makes things worse.**
  "Keyword `high` wins, otherwise vendor" scored 31/40; `max(local, vendor)`
  scored 21/40 with 13 over-ratings, against 33/40 and zero for the vendor
  alone. The keyword classifier over-rates too much to be a useful floor. Both
  reviewers proposed `max` as an injection defence; the measurement is the
  reason it was declined.
- **A dedicated classification runtime was never needed.** An 850-line plan was
  built around adding `--mode classify` to `weightclass-runtime`, and consensus
  review then found that runtime has no classify mode, no rubric, and no phase
  creating one. `wclass run` already spawns the vendor CLI and hands it the
  task; asking that same CLI for a tier adds no credential, no billing boundary,
  and for `run` no new destination. Prefer what is already installed.
- A deliberate `ralplan` attempt produced no usable plan because its Planner
  recursively tried to launch `ralplan`; it made no repository changes. Do not
  rely on that run as approval evidence.
- Do not reintroduce retries, background execution, process supervision,
  durable task storage, arbitrary API commands, or automatic vendor config
  changes.

## Traps in this repository

Three mistakes were made more than once here. They are cheap to avoid and
expensive to notice.

- **Commit before mutation testing.** `git checkout -- src/` after a mutation
  reverts uncommitted work too. This silently discarded a finished fix three
  separate times, once leaving a commit whose tests imported a symbol its source
  no longer defined.
- **Never `git add -A` unscoped.** It swept 172 local agent-state files into a
  commit that reached the public `main`; removing them needed a history rewrite
  and a force-push, and the pre-rewrite commits are still reachable by SHA.
  Stage explicit paths.
- **The exact-string assertions are load-bearing.** `packaging/homebrew/
  weightclass.rb` and `.github/workflows/ci.yml` both assert
  `{"tier": "low"}` byte for byte, and the formula is published. Any new key on
  a default `wclass classify` path breaks `brew test` for installed users. Add
  keys only on paths reached by a new flag.

## Next Steps

1. Inspect the runtime repository's GitHub Actions result for `fe28566`; fix
   only confirmed clean-install or test failures. This is the last unexamined
   CI in the project. It only matters if V2 API routing is used.
2. Set the runtime repository description, then decide whether to create a
   versioned release. If releasing, build in a clean environment and publish
   checksums per its `RELEASING.md`.
3. Optional, and previously declined as disproportionate: ask GitHub Support to
   garbage-collect unreachable objects. A force-push removed 172 accidentally
   committed local-state files from `main`, but the pre-rewrite commits remain
   reachable by their old SHAs. They hold no credentials and no conversation
   text — only local paths, a session title, and UUIDs.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router` and read
`HANDOFF.md`, `AGENTS.md`, and `README.md`. `weightclass 0.2.0` is published and
`main` is green, so nothing is mid-flight; pick up from Next Steps.

Preserve the boundaries the tool is built on: task text is transient stdin only,
one foreground process, no credentials, no HTTP from weightclass itself, and
fail closed with a redacted JSON diagnostic. Read "What Did Not Work" before
proposing a classifier change — the obvious ideas have been measured and lost.

For runtime work, also open `/Users/jinhongan/Desktop/sar-provider-runtime`
(the local checkout of `github.com/ictechgy/weightclass-runtime`) and read its
`AGENTS.md` first.
