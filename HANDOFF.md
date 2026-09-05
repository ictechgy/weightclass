# Handoff

**Archived on 2026-09-05.** No further development is planned. 0.32.0 is the final
release; the repository is read-only and kept as a research record. If someone
unarchives it, start by reading this file and `docs/launcher-scope.md` §6 for the
questions that were left open on purpose.

_Last updated: 2026-09-05 KST. Current release: **0.32.0** on PyPI and the
Homebrew tap (release run `33899992985`, GitHub Release `v0.32.0` marked latest,
tap commit `e1b3f31`)._

Nothing is unreleased on `main` except this handoff note and the formula source
update. 0.32.0 is the launcher narrowing (#200–#206, below) released; 0.31.1 is
the last release that had the advisory companion, `delegate`, `v2`, `recommend`,
`review-cost-profile`, `render`, vendor triage, and `--suggest-escalation`.
Releasing is a human action; see [`RELEASING.md`](RELEASING.md).

Per-release evidence, superseded working notes, and abandoned approaches live in
[`docs/handoff-archive.md`](docs/handoff-archive.md). Everything this file used
to carry was moved there on 2026-09-05. Never start new work in the archive.

## What the tool is now

`wclass` starts one agent CLI process with the exact command you reviewed. It
discovers installed Codex, Claude Code, Antigravity (`agy`), and Grok
executables, selects a route for an explicitly chosen tier, prints the task-free
argv and a route fingerprint, asks on a terminal before starting anything, and
then runs exactly one foreground child it does not retry, supervise, or capture.
It reads no credentials, makes no network request, and persists no task content;
its only persisted state is the opt-in, aggregate-only usage store. Any vendor
weightclass ships no built-in for is reachable by writing its exact argv in a
reviewed policy. That is the whole product, and it is deliberately smaller than
the tool that shipped in 0.31.1.

## The narrowing PRs (#200–#204, unreleased)

| PR | Branch | What it did |
| --- | --- | --- |
| #200 | `docs/launcher-scope` | Proposed the scope and PR order for narrowing to the launcher (`docs/launcher-scope.md`). |
| #201 | `refactor/move-runtime-context-check` | Moved the child-status context check (`validate_runtime_process_context` and its error) into `process_context.py`, so nothing else had to import the delegation runtime. |
| #202 | `feature/remove-advisory` | Removed the `wclass-advisory` companion: package, tests, entrypoint, Skill bundle and ledger, release-workflow steps, formula test lines, and the two advisory verifiers. |
| #203 | `feature/remove-delegate-and-v2` | Removed the `delegate` family and `v2` API routing. |
| #204 | `feature/remove-recommend-render-triage` | Removed `recommend`, `review-cost-profile`, `render`, vendor triage, and `--suggest-escalation`. |

## What changed but stayed

- The packaged presets were renamed `<vendor>-model-override` in 0.31.1
  (`feature/model-override-preset-names`, #190); the `<vendor>-cost-focused`
  names remain aliases, the policy files and fingerprints did not move, and the
  `preset` field in `review-preset` receipts carries the current name.

## What was removed and when

Every row below last shipped in **0.31.1**. Anyone who needs one should pin
`uv tool install weightclass==0.31.1`; the Homebrew tap follows latest only.

| Surface | PR |
| --- | --- |
| `wclass-advisory` companion (`src/weightclass/advisory/`, the packaged Skill bundle and its upgrade ledger, `.weightclass/verify-review` and `verify-design`) | #202 |
| `delegate` (WCD1/WCD2 protocols, conformance drivers, the qualification registry) and the schema-3 `native_delegation` confirmation path | #203 |
| `v2` API routing (the `v2` subcommand, `--confirm-api-egress`) | #203 |
| `recommend` and `review-cost-profile` | #204 |
| `render` | #204 |
| `classify --ask-vendor` and `--show-triage-command` (vendor triage) | #204 |
| `run --suggest-escalation` | #204 |

Kept and stated so it is not re-litigated: `discover`, `classify`, `route`,
`run`, `usage`, `example-policy`, `review-preset`, the hidden `profile` and
`select`, schemas 1/2/3, `--bind-executable-identity`, and
`--confirm-endpoint-transition`. `v2_validation.py` and `canonical_v2.py` are
shared native validation and stay. Documents describing removed surfaces moved
to [`docs/archive/`](docs/archive/README.md) rather than being deleted.

## What is frozen

- **`.weightclass/verify` protects itself.** The gate refuses to run when a
  change to it is staged. It used to protect the advisory acceptance module too;
  that file was deleted with the surface it gated, which was a deliberate,
  disclosed act recorded in the #202 commit body. Read
  [`.weightclass/AGENTS.md`](.weightclass/AGENTS.md) before touching anything
  there, and never edit a verifier to make a candidate pass.
- **`tests/eval/verifier-recall-checks/` is fingerprint-bound.** Its bytes are
  hashed into the committed run reports, so it is excluded from repository ruff
  and mypy in `pyproject.toml`. Do not restyle it.
- **`tests/eval/` scorers are the research record, not product.** `score.py`,
  `token_benchmark.py`, `cost_benchmark.py`, `provider_usage_benchmark.py`, and
  `verifier_recall.py` are offline evidence. They cost nothing to keep and are
  not shipped in the wheel.

## Measured results that must not be overstated

Pointers only; the numbers live in the documents.

- **Paired token study** — [`docs/paired-token-study.md`](docs/paired-token-study.md).
  Pre-registered, closed at Phase 1b: 0 of 18 calibrated candidates were
  tier-sensitive against a floor of nine, so Phase 2 was never started. Routing
  up bought nothing on the work it measured. The separately measured downward
  direction (15 of 15 passed pinned at `low`) is a bound on that fixture, not a
  licence to route everything cheaply.
- **Fresh blind check** —
  [`docs/policy4-fresh-blind-evaluation.md`](docs/policy4-fresh-blind-evaluation.md).
  Classification policy 4 against 24 blind-rated prompts: agreement 10/24
  (41.7%), high-tier recall 1/9 (11.1%), over-routing 6/24 (25.0%). **State both
  directions.** Eight of the nine majority-`high` prompts were routed to
  `standard`; publishing over-routing while omitting that under-routing hides
  the stronger warning. `CLASSIFIER_MEASURED_AGREEMENT` in `classification.py`
  carries both, and `--suggest-tier` reports it in the receipt and the console
  review.
- **Verifier recall (Stage A0)** —
  [`docs/verifier-recall-result.md`](docs/verifier-recall-result.md). No-go: the
  best composition reached 24/33, Wilson 95% [0.56, 0.85], against a
  pre-registered lower bound of 0.80. Stage B was never started and the V1
  one-child contract is unchanged.

Do not tune the classifier against a corpus whose ratings have already been
spent, and do not describe tier routing as an established cost saving.

## Release procedure gotchas

- **`v0.31.0` is a retained failed candidate.** Its tag exists on `main` but
  nothing was published under it: the release workflow's advisory Skill upgrade
  ledger step failed before build because the 0.30.0 bundle was not registered,
  and the workflow required the previous tag's bundle every release, not only
  when it changed. That step and its verifier were removed with the advisory
  companion in #202, so this failure mode is gone — but `v0.31.0` stays
  unreused, like every published or protected tag.
- **Local `python3.13` sdist sentinel quirk (this machine only).**
  `RELEASING.md` step 2 invokes `python3.13` directly, but the Homebrew
  `python@3.13` framework `bin/` has no `python3`, so the extracted-sdist
  `test_parent_sentinel_is_absent` fails under it. The same verifier passes
  under the default `python3`, and the release workflow re-runs it on Linux. Use
  a Python whose real `bin/` contains `python3`.
- **Stacked PRs die when their base branch is deleted.** #187 was auto-closed
  that way and had to be reopened as #191 with the same commit. After amending a
  base, rebase the branches above it with
  `git rebase --onto <new-base> <old-base-commit>`; a plain rebase replays the
  old base commit and conflicts.
- **The `pypi` deployment environment needs an explicit human approval.** It has
  previously been given through the GitHub API at the maintainer's explicit
  instruction, with an approval comment naming the session. A tag push is the
  approval to release; a published PyPI version can never be reused or deleted.
- `.github/release-notes/v0.32.0.md` must exist in the tag commit, and the notes
  must state the breaking removals and the 0.31.1 pin.

## Open questions

None of these is authorized work; each needs owner agreement first.

- **Schema 2 is kept provisionally.** The usage store binds schema 3, not
  schema 2; schema 2 survives only through the shared dispatch layer and any
  existing schema-2 policy. It is a candidate for a later pass.
- **`profile` and `select`** still parse and run but are absent from the
  top-level listing. Whether they survive a later pass is undecided.
- **Task delivery is stdin-only** for the commands that read a task, with the
  narrow reviewed `{{task}}` argv exception for CLIs that accept a prompt only
  on the command line. Whether the tool should ever accept a task from a file or
  an argument is an ergonomics question for after the cut.
- **`--bind-executable-identity` and `--confirm-endpoint-transition` were kept
  deliberately.** The first is the only way to bind a custom route to an
  observed executable; without the second, a schema-3 policy that requires the
  confirmation could never run.

## Next steps

Nothing is scheduled. 0.32.0 shipped on 2026-09-05: the release workflow passed
every job on the first tag (the Skill ledger step that failed `v0.31.0` no longer
exists), the `pypi` environment was approved at the maintainer's instruction, the
tap formula was verified with `brew style`/`audit --strict`/`upgrade
--build-from-source`/`test`, and the user-level uv tool reports 0.32.0.

If work resumes, the open questions above are the candidates; none is
authorized by this file.

## Resume Prompt

Read `HANDOFF.md`, the root `AGENTS.md`, and the scoped `AGENTS.md` for the
subtree you will touch, then continue from: 0.31.1 is the current release; `main`
additionally carries the unreleased launcher narrowing (#200-#204) and this
documentation pass; `wclass` now only starts one reviewed agent CLI process; the
next action is the 0.32.0 release.
