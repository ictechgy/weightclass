# Handoff

_Last updated: 2026-08-03 by Claude Code_

## Goal

- Maintain public, local `weightclass`: a deterministic task router for native
  Codex and Claude Code workflows, with optional reviewed API routing through
  a separately distributed runtime.

## Current Status

- Main repository: `https://github.com/ictechgy/weightclass`, branch `main`,
  tracking `origin/main` at commit `7cb02d6`.
- Separate runtime repository: `https://github.com/ictechgy/weightclass-runtime`,
  branch `main`, tracking `origin/main` at commit `fe28566`.
- Both repositories are public-source ready; no registry release or GitHub
  Actions result has been inspected yet.
- The main worktree has an unrelated untracked `.omc/` directory. Preserve and
  exclude it from commits.

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

- `README.md`: installation, native/API boundaries, and local usage.
- `docs/integrations.md`: safe Codex/Claude invocation snippets.
- `src/sar/classification.py`: local deterministic tier selection; no remote
  triage or persistence.
- `src/sar/v2.py`: API route review/acknowledgement; it launches only a
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
  - Result: 54 tests passed.
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
    `weightclass 0.1.0` from the single `sar.__version__` source.
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

## Blockers & Open Questions

- Main-repository CI passed on PR #1 for Python 3.10, 3.12, and 3.13
  (install, installed-CLI check, tests). The runtime repository's first
  GitHub Actions run has still not been inspected.
- A package-registry publication, version/tag policy, and release artifacts
  have not been approved or created.
- Provider model/effort compatibility and actual API behavior remain
  user/provider dependent; do not test with real credentials without approval.

## What Worked

- Offline tests use fake provider connections and preserve the no-egress
  boundary.
- Explicit source-vendor commands make native routing reviewable without
  vendor configuration changes.

## What Did Not Work / Avoid

- A deliberate `ralplan` attempt produced no usable plan because its Planner
  recursively tried to launch `ralplan`; it made no repository changes. Do not
  rely on that run as approval evidence.
- Do not reintroduce retries, background execution, process supervision,
  durable task storage, arbitrary API commands, or automatic vendor config
  changes.

## Next Steps

0. Review and merge `fix/router-contract-hardening`. It changes observable
   behavior that a caller may depend on: `wclass route` always emits `vendor`
   and no longer emits `model`; omitting `--source-vendor` no longer crosses
   vendors; a policy declaring a `model` field is now rejected; the bare
   `wclass --policy ... --descriptor ...` form moved to `wclass render`; and
   the built-in Codex commands gained `-c model_reasoning_effort=...`.
1. Inspect CI results for commits `7cb02d6` and `fe28566`; fix only confirmed
   clean-install or test failures.
2. Set the runtime repository description, then decide whether to create a
   versioned release. If releasing, build in a clean environment and publish
   checksums per its `RELEASING.md`.
3. Before any PyPI publication, approve a release/versioning policy and verify
   package builds without reading credentials or invoking real APIs.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`,
`AGENTS.md`, and `README.md`. First inspect the GitHub Actions results for
`7cb02d6`; preserve the transient-task, one-foreground-process, no-credential
boundary. For runtime work, also open
`/Users/jinhongan/Desktop/sar-provider-runtime` and read its `AGENTS.md` before
making changes.
