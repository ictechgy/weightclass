# Handoff

_Last updated: 2026-08-03 09:40 KST by Codex_

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
- Model and effort labels are opaque, user-reviewed policy values. Never infer
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
  - Result: 27 tests passed.
- Ran in the main repository:
  `PYTHONPATH=src python3 -m compileall -q src`
  - Result: passed.
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

- Inspect the first GitHub Actions runs for both repositories before claiming
  clean-install CI has passed.
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
