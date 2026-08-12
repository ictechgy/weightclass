# Handoff

_Last updated: 2026-08-12 14:05 KST by Codex_

## Goal

- Maintain `weightclass` as a public, local router that deterministically
  selects one reviewed native agent CLI workflow from explicit user policy.
- Preserve the local privacy boundary: task content is transient only. Never
  persist, log, hash, echo, or include it in reviewed output or diagnostics.
- The router never owns provider credentials, HTTP, billing, quota, or
  subscription-entitlement discovery.

## Current Status

- `weightclass 0.8.2` is merged, tagged, published, and installable.
  - Release commit: `63258166471b393ca31ae0fc89c2ba18683528b6`.
  - Annotated tag: `v0.8.2`.
  - GitHub Release: <https://github.com/ictechgy/weightclass/releases/tag/v0.8.2>.
  - Release workflow: [31457913800](https://github.com/ictechgy/weightclass/actions/runs/31457913800), completed successfully.
  - PyPI: <https://pypi.org/project/weightclass/0.8.2/>.
- The source-of-truth Homebrew formula was updated on `main` by
  `7c82fde424c1e5807c5ca8a9fa1e9f76b4d5f004`. The matching tap commit is
  [`eaaf259`](https://github.com/ictechgy/homebrew-tap/commit/eaaf25947ea10560df050bceccd7f127f3af6fec).
- `v0.8.1` was never published to PyPI: its Release workflow failed only on
  Ruff import ordering. It had no GitHub Release and was deliberately deleted
  from both the local and `origin` tag namespaces. Do not recreate it.
- A `0.9.0` release candidate is prepared on `main` from
  `6eebf9c6e90ff55958d2172fbd8131f240ddafe8`; the version source is already
  `0.9.0`. The candidate is not yet committed, tagged, published, or deployed.
  The minor bump is intentional because Linux Claude semantic triage now fails
  closed before task egress; ordinary native Claude routing remains unchanged.

## Completed in 0.8.2

- V2 API runtime observation fails closed for malformed paths, including a
  symlink loop that raises `RuntimeError` on Python 3.10; diagnostics stay
  value-free as `invalid_input`.
- The V2 API egress-confirmation gate runs before runtime inspection and before
  task input is read. Invalid policy input still retains its existing
  `invalid_input` precedence.
- The reviewed runtime identity is observed again immediately before spawn;
  changed identity fails closed with `route_fingerprint_mismatch` and an
  unobservable runtime with `executor_unavailable`.
- Regression coverage covers the symlink loop, confirmation precedence, and
  runtime identity drift.
- The Homebrew formula installs the PyPI `0.8.2` source distribution:
  - sdist SHA-256:
    `c566a8f2835ba29e8fae6a30651fe0e40671a376ccca4db51459cc26770ca096`.

## 0.9.0 Release Candidate

- All ordinary foreground execution paths now own direct-child status through
  `waitpid`; `ECHILD` is a redacted `executor_failed`, never synthesized exit
  zero. Exact argv/input, inherited stdout/stderr, exit/signal status, one-child
  behavior, and `agy`/`grok` argv task delivery remain covered.
- V2 API execution rejects a missing reviewed route fingerprint before process
  context checks, runtime observation, or task input.
- Foreground spawn and cleanup defer SIGINT across ownership seams and always
  drive the owned child through close, TERM, KILL, and final reap as needed.
- Vendor triage validates process context adjacent to spawn, owns group cleanup
  and direct reap, and erases its pinned private tree with a linear,
  descriptor-relative traversal. Darwin uses a reviewed static `sandbox-exec`
  profile and adapter version 2; Linux Claude semantic triage fails closed
  before task egress because no equivalent filesystem containment is reviewed.
  Ordinary native Claude routing is unchanged.
- macOS CI and release boundary jobs explicitly include the new
  `tests.test_foreground_process` and `tests.test_process_context` suites.

## Key Files & State

- `src/weightclass/cli.py`: V2 route/run ordering and pre-spawn identity check.
- `src/weightclass/process_context.py`: shared direct-child wait-status
  ownership and safe process-context predicates.
- `src/weightclass/foreground_process.py`: behavior-preserving foreground
  stdin delivery, SIGINT deferral, escalation, and final reap.
- `src/weightclass/triage.py`: opt-in semantic triage containment, process-group
  lifecycle, and descriptor-relative private-tree cleanup.
- `src/weightclass/v2.py`: API runtime observation and redacted invalid-input
  conversion.
- `tests/test_process_context.py`, `tests/test_foreground_process.py`,
  `tests/test_triage.py`, and `tests/test_v2.py`: focused hardening regressions.
- `README.md`: documents the task-in-argv residual for `agy` and `grok`.
- `packaging/homebrew/weightclass.rb`: source of truth copied to
  `ictechgy/homebrew-tap` after a successful PyPI publish.
- `.github/workflows/release.yml`: builds one immutable candidate, validates it
  on Linux and macOS, then publishes the exact artifact through PyPI Trusted
  Publishing.
- `docs/completion-audit-v2.md`: requirement-to-test completion map. Goal g12 is leader-verified; retain this audit connection when refreshing this file.

## Important Context / Decisions

- Built-in native routing supports `claude`, `codex`, `agy`, and `grok`.
  Vendor/model/effort/profile labels are opaque user configuration; do not infer
  availability, entitlement, or billing state.
- A route normally stays with its source vendor. Cross-vendor routing requires
  an exact explicit directional policy grant; unknown, ambiguous, unsupported,
  or unsafe input must fail closed with redacted diagnostics.
- V1 and V2 start at most one reviewed foreground child. They do not retry,
  recover, supervise, background, proxy provider APIs, read credentials, or
  persist task content.
- Review/run binding is mandatory for policy routes: `route` produces the exact
  fingerprint and `run` requires acknowledgement before task access. File mode
  checks are defense in depth, not a replacement for the fingerprint.
- `agy` and `grok` accept prompts through the reserved `{{task}}` argv slot.
  This exposes task content to local process inspection for the child lifetime;
  it is documented residual risk, not a reason to log or persist the task.
- The built-in `grok` command cannot accept a task beginning with `-`; changing
  that requires a separately reviewed command-shape design.
- Do not populate a provider qualification registry or add a built-in command
  for an unmeasured CLI.
- Do not re-enable Linux Claude semantic triage with O_PATH cleanup alone.
  Exact-inode cleanup cannot prevent root replacement, writes outside the
  pinned tree, or process-group escape; a separate reviewed containment command
  is required.

## Verification

- Fresh verification of the `0.9.0` release candidate on 2026-08-12:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 738 tests passed on each interpreter.
  - Full pytest: 738 tests and 806 subtests passed; two existing Python 3.14 tar
    extraction deprecation warnings remain.
  - Ruff 0.16.2 check/format, strict mypy over 93 source files, and
    `git diff --check`: clean.
  - The `0.9.0` wheel/sdist build, strict Twine metadata check, and
    extracted-sdist isolation suite passed; 733 tests passed with 11 platform
    skips.
  - Release-tool installation and build isolation used the public Python
    package index within the approved release scope. No secret-bearing file was
    accessed.

- Local release verification before publishing:
  - Full source suite with `ResourceWarning` as an error on Python 3.14 and
    Python 3.10: 672 tests passed.
  - Ruff `0.16.2` check and format checks: clean.
  - Mypy, package build, strict Twine metadata check, distribution-isolation
    verification, installed-wheel version check, and `git diff --check`: clean.
- GitHub Release workflow `31457913800` succeeded:
  immutable Python 3.13 candidate; macOS routing boundaries on Python 3.10 and
  3.14; immutable candidate validation on Python 3.10 and 3.14; exact PyPI
  publish.
- A fresh virtual environment installed `weightclass==0.8.2` from the explicit
  PyPI simple index; `wclass --version` reported `weightclass 0.8.2` and the
  classification smoke test returned `{"tier": "low"}`.
- Homebrew verification passed for the `weightclass` formula: formula-scoped
  style and strict audit, `brew reinstall --build-from-source`, and `brew test`.
  The full tap style check has an unrelated existing `relay.rb` ordering
  violation; do not change it as part of weightclass maintenance.

## Blockers & Open Questions

- No known code blocker remains; independent final diff review reported no
  actionable findings.
- Commit and push the reviewed candidate, create annotated tag `v0.9.0`, and
  watch the immutable Release workflow through PyPI publication. No release,
  push, or deployment has yet been performed for these changes.
- Real installed-Claude compatibility under the Darwin sandbox was not tested
  because that would invoke an external runtime/network boundary.
- Optional future work: collect real-user routing feedback; qualify a concrete
  external runtime only with independently reviewed evidence; update pinned
  GitHub Actions only after reviewing upstream migrations.

## Next Steps

1. Commit and push the verified `0.9.0` candidate, then push annotated tag
   `v0.9.0` and monitor the Release workflow through PyPI publication.
2. Update `packaging/homebrew/weightclass.rb` from the published canonical sdist,
   verify the tap formula, push it, and refresh this handoff with exact commits,
   workflow run, release URL, and checksum.
3. Preserve Protocol 1 compatibility, explicit cross-vendor opt-in, task
   no-retention, and the single-reviewed-child boundary.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`
and `AGENTS.md`, then continue from: `publish the verified weightclass 0.9.0
candidate using the immutable v0.9.0 release workflow, then update and verify
the Homebrew formula; no candidate commit, tag, publish, or deployment has yet
completed.`
