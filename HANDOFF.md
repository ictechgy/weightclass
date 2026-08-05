# Handoff

_Last updated: 2026-08-06 04:15 KST by Codex_

## Goal

- Maintain public, local `weightclass`: deterministically classify a task and
  select or start one reviewed native Codex or Claude Code workflow.
- Improve model/effort routing without inferring subscriptions, crossing
  vendors by default, retaining task content, or adding an unproven semantic
  model.

## Current Status

- PR #15 merged the Phase 4 offline evaluation gate at
  `e3c5aeee0f61c79467caeac34b08de2d7a1946b9` after all CI and review gates
  passed.
- `weightclass 0.3.0` is published on PyPI from tag `v0.3.0` at
  `da958f5c1bcf062d7c537774f2ab8e61ee97ec4d`; release workflow run
  `31038013636` completed successfully.
- PR #17 updated the repository's canonical Homebrew formula, and tap PR #9
  published the matching 0.3.0 URL and SHA-256 to `ictechgy/homebrew-tap`.
- Phase 4 delivery files:
  - `tests/eval/score.py`
  - `tests/test_eval_score.py`
  - `tests/eval/README.md`
  - `docs/phase4-evaluation-audit.md`
  - `docs/phase4-go-no-go-template.md`
  - `docs/routing-roadmap.md`
  - `HANDOFF.md`
- The Phase 4 decision is **no-go**: the repository now has a safe offline
  decision mechanism, but no independently supplied fresh-corpus candidate,
  resource, or supply-chain evidence has been evaluated.
- The separate `weightclass-runtime` repository remains at `fe28566`; GitHub
  Actions run `30774907673` was previously confirmed successful.

## Completed

- PR #13 added explainable English/Korean high-risk floors and regression
  coverage without changing the transient-task or source-vendor contracts.
- PR #14 added versioned routing reason metadata, `--explain`, reviewed
  `balanced`/`cautious` policy postures, and privacy-safe offline evaluation
  foundations. Its Python 3.10–3.13 and quality/build CI passed before merge.
- Roadmap Phases 0–3 are implemented:
  - public corpus is regression-only; fresh corpora use aggregate offline
    metrics and a blind release protocol;
  - high-risk floors and reason codes are deterministic and local;
  - risk posture remains explicit and source-vendor pinned;
  - vendor-only/raise-only triage comparisons are evaluator-driven offline
    experiments and do not change default routing.
- Phase 4 evaluation support is implemented on `main`:
  - `--candidate` accepts an exact, bounded schema with one opaque-ID-bound
    prediction per fresh-corpus record;
  - malformed, incomplete, duplicated, unknown, out-of-order, or label-mismatched
    evidence fails closed with value-free diagnostics;
  - output includes candidate and same-corpus local-baseline aggregate
    confusion, high-tier recall, over-routing, English/Korean and fixed category
    slices, Wilson 95% intervals, an observed raise-only comparison, explicit
    quality/resource/supply-chain/privacy gates, and `go`/`no-go`;
  - the committed public fixture, including symlink and hardlink aliases, is
    rejected before it is read in candidate mode;
  - reports are deterministic and contain no corpus task field, per-task result,
    candidate/baseline identifier value, or task hash;
  - reports state that the corpus was evaluator-supplied but do not claim the
    scorer verified freshness; that remains an independent provenance gate;
  - a reproducible human decision record requires provenance, evaluator
    independence, corpus/scorer versions, commands, predeclared slice and
    interval rules, aggregate evidence, and rationale.

## Key Files & State

- `AGENTS.md`: authoritative product, safety, and engineering constraints.
- `docs/routing-roadmap.md`: routing strategy and Phase 0–4 delivery status.
- `docs/phase4-evaluation-audit.md`: evidence-backed audit of the PR #14
  baseline and the four gaps addressed by PR #15.
- `docs/phase4-go-no-go-template.md`: required review record; incomplete or
  unresolved evidence defaults to `no-go`/`do-not-add`.
- `tests/eval/score.py`: offline scorer and candidate decision CLI. It is
  evaluation tooling, not installed runtime behavior.
- `tests/eval/README.md`: fresh blind-corpus and candidate evidence contracts.
- `tests/test_eval_score.py`: 37 focused evaluation, failure, privacy, and
  determinism tests.
- `RELEASING.md`: tag/version policy, Trusted Publishing, and Homebrew update
  procedure.
- `packaging/homebrew/weightclass.rb`: published-install behavior assertions.
- `.github/workflows/ci.yml`: supported-version tests and quality/package gate.
- `src/weightclass/`: installed runtime, currently versioned as 0.3.0.

## Important Context / Decisions

### Confirmed facts

- Runtime task content is transient standard input only. Never persist, log,
  echo, hash, cache, or include it in diagnostics.
- Public `tests/eval/corpus.json` cannot support a new accuracy or Phase 4
  acceptance claim. Do not inspect or tune against it for candidate approval.
- Candidate predictions and the fresh corpus are evaluator-owned local inputs
  kept outside the repository. The scorer neither runs a candidate nor makes a
  provider/network call.
- A scorer `go` is necessary but not sufficient. The broader reviewed record
  must also satisfy provenance, confidence sufficiency, slices, privacy,
  resource, maintenance, and supply-chain gates.
- A production semantic dependency remains a separate reviewed change after
  all gates pass. Until then, retain the deterministic policy engine.
- Native routing stays with its explicit source vendor unless a reviewed policy
  opts into `allow_mixed_vendors`. Model labels and subscription availability
  remain opaque user configuration.
- Preserve byte-for-byte default `wclass classify` and route output unless a
  separately reviewed compatibility change updates the CI, published Homebrew
  assertions, and downstream contract together.
- V2 execution requires both `--confirm-api-egress` and the exact reviewed
  route fingerprint. The main tool never handles provider credentials or HTTP;
  credentials remain external-runtime environment concerns. Never access
  `.env`, keychain, auth, or shell-profile files without explicit approval.
- Vendor triage commands remain non-mutating (`plan`/read-only) because they
  receive untrusted task text. Do not relax those pins without a separate
  security review.

### Assumptions

- No independent Phase 4 candidate evidence is currently available locally.
- Normal GitHub delivery actions were used for the 0.3.0 release. Force
  operations and production-model adoption remain out of scope.

## Verification

- Ran: `PYTHONPATH=src python3 -m unittest tests.test_eval_score`
  - Result: 37 tests passed.
- Ran: `PYTHONPATH=src python3 -m unittest discover -s tests`
  - Result: 150 tests passed on local Python 3.14. Existing triage fake-process
    tests emitted `ResourceWarning` messages for unclosed streams.
- Ran: `python3 -m compileall -q src tests`
  - Result: passed.
- Ran: `git diff --check` and a 100-column check over changed Python files.
  - Result: passed.
- Ran: `uv build --offline --out-dir <temporary-directory>`
  - Result: source distribution and wheel built successfully.
- Ran the explicit public-fixture regression test red/green:
  - before the filesystem-identity guard, a hardlink reached `_load_corpus`;
  - after the guard, direct, symlink, and hardlink paths are rejected before
    read.
- Ran: `ruff check src tests`, `ruff format --check src tests`, and strict
  `mypy` using isolated tool execution.
  - Result: passed after the PR quality-fix commit.
- Release workflow run `31038013636` passed tag/version, Python 3.10–3.13
  tests, lint, formatting, types, package build, metadata validation, and PyPI
  Trusted Publishing.
- A clean virtual environment installed `weightclass==0.3.0` from public PyPI;
  `wclass --version` and representative classification checks passed.
- The Homebrew formula passed scoped style and strict audit checks, upgraded a
  local source-built install from 0.2.0 to 0.3.0, and passed `brew test` plus a
  stdin classification check. Whole-tap style still reports one unrelated,
  pre-existing component-order offense in `Formula/relay.rb`.

## Blockers & Open Questions

- Phase 4 cannot advance to a production model without independently supplied
  fresh blind-corpus predictions plus resource and supply-chain evidence.
- Pre-existing triage subprocess `ResourceWarning`s are outside the Phase 4
  diff; address separately rather than mixing them into this change.
- Existing route fingerprints bind reviewed policy selection, not task content
  or the executable behind an argv/path. Binding task content would require a
  forbidden task hash; executable identity remains a documented TOCTOU limit.
- `.omc/` and `.serena/` are ignored and untracked now, but local paths and
  session metadata entered history in `fd1fbc0`. No credentials or conversation
  text were found. Purging history would require an explicitly authorized
  force-push and is not part of this branch.

## What Worked

- Exact, versioned candidate schemas keep output-bearing metadata bounded.
- Strict JSON loading rejects duplicate object fields rather than silently
  accepting the last value.
- Opaque record IDs plus repeated consensus labels detect reordered or
  mismatched prediction files without hashing task content.
- Wilson bounds and explicit empty-slice records make small or missing samples
  visible and fail closed.
- A test that patched `_load_corpus` exposed that merely failing on missing IDs
  still read the public fixture; filesystem-identity rejection now enforces the
  documented pre-read boundary for direct paths, symlinks, and hardlinks.
- Output distinguishes what the scorer observed from what an independent
  reviewer must establish: corpus freshness and identifier provenance are not
  falsely marked as scorer-verified.
- Offline `uv build` provides package evidence without network access.

## What Did Not Work / Avoid

- Do not produce or commit synthetic “passing” candidate evidence to move the
  gate. Evidence must come from an independent evaluator after thresholds are
  frozen.
- Do not use the public regression fixture through an alternate path, symlink,
  hardlink, or copy as fresh evidence.
- Do not add a semantic dependency, model download, benchmark claim, vendor
  invocation, or runtime routing change while the decision is `no-go`.
- Stage only explicit reviewed paths; do not use an unscoped `git add -A` in
  this public repository.
- An `ultragoal retry` did not carry the verification reason into the next
  worker prompt in this run; the narrow public-fixture guard was completed and
  red/green verified directly instead of repeating that retry.

## Next Steps

1. Keep the decision `no-go` unless an independent evaluator supplies a fresh
   sealed corpus, ID-bound candidate predictions, and all required resource and
   supply-chain evidence under predeclared rules.
2. If such evidence arrives, run only the documented offline scorer and fill
   `docs/phase4-go-no-go-template.md` with aggregate, non-sensitive results.
3. Address the pre-existing triage subprocess `ResourceWarning`s and the
   unrelated Homebrew `Formula/relay.rb` style offense only in separately
   scoped changes.

## Resume Prompt

Open the `subscription-agent-router` repository root, read `HANDOFF.md` and
`AGENTS.md`, then confirm local `main` matches `origin/main`. Preserve the
Phase 4 `no-go` decision and do not add a production model unless independently
supplied evidence satisfies every predeclared gate. The current public package
and Homebrew formula version is 0.3.0.
