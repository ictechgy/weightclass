# Handoff

_Last updated: 2026-08-10 by Claude Code_

## Goal

- Maintain `weightclass` as a public, local router that deterministically selects
  a reviewed native Codex or Claude workflow from explicit user policy.
- Version 0.5.0 added explicit model and effort routing, same-vendor profile
  routing, and directional opt-in cross-vendor/profile routing.
- Version 0.6.0 added no routing behavior. It fixed a Darwin child-observation
  race, removed the forked process helpers that caused it, and gated
  caller-supplied JSON on file write ownership.
- Preserve the local privacy boundary: task content is transient stdin only;
  weightclass does not retain it or own provider credentials, HTTP, billing, or
  subscription entitlement discovery.

## Current Status

- 0.6.0 is complete, merged, and deployed. It carries the review follow-ups
  listed under Completed.
- PR [#24](https://github.com/ictechgy/weightclass/pull/24) is merged.
- Merge commit: `a11d2d6`.
- Annotated tag `v0.6.0` points to that merge commit and is on `origin`.
- GitHub Release workflow run
  [31350378332](https://github.com/ictechgy/weightclass/actions/runs/31350378332)
  completed successfully.
- PyPI publishes
  [`weightclass 0.6.0`](https://pypi.org/project/weightclass/0.6.0/)
  as exactly one wheel and one sdist; neither is yanked.
- 0.6.0 changes no routing decision, descriptor, fingerprint, frame, or protocol
  stage. It is a correctness, deduplication, and documentation change plus one
  fail-closed gate on caller-supplied file permissions.
- 0.6.0 was never read by anyone other than its author before publication. The
  gates were exhaustive; the human review step in `AGENTS.md` was not performed.
  Treat its judgement calls as unconfirmed, not as settled precedent.
- The requested 0.5.0 scope was complete, reviewed, merged, and deployed. PR
  [#23](https://github.com/ictechgy/weightclass/pull/23), merge commit
  `3f0167abab2a4d79d2252aef0b75fa067d3c7337`, tag `v0.5.0`.
- No mandatory implementation, merge, or deployment work remains.

## Completed

- `03aea8a` — `feat: add protocol 2 model and profile routing`
- `9c7b7f8` — `fix: close protocol 2 review gaps`
- `8121f52` — `fix: validate native v2 child status context`
- Goal g12 is leader-verified. It adds full Linux/macOS CI and release-DAG gates
  plus the requirement map in `docs/completion-audit-v2.md`.

Released in 0.6.0:

- Restored the `Goal g12 is leader-verified` statement this file must contain.
  A previous refresh dropped it and left
  `tests/test_completion_audit_v2.py::test_handoff_points_to_current_g12_audit`
  failing in the working tree.
- Backported a Darwin child-process race fix from the conformance runner to
  `triage.py`. Where `os.waitid` is unavailable, Darwin refuses `EVFILT_PROC`
  registration with `ESRCH` for a child that has already exited even while its
  wait status is still owned. The runner returned an already-exited sentinel;
  the triage copy raised `TriageUnavailableError` and discarded whatever the
  vendor had already written. Reproduced directly before fixing.
- Moved the child-lifecycle observers (`has_leader_exit_observer`,
  `open_leader_exit_queue`, `observe_leader_exit`, `close_leader_exit_queue`,
  `signal_process_group`, `darwin_child_status_waitable`) into
  `process_context.py`, and the duplicate-JSON-key hook into `json_input.py`.
  These were forked copies that had already drifted; the drift is what produced
  the bug above. `_DeferredSigint` is deliberately **not** merged: the runtime
  and runner versions have genuinely different lifecycles.
- Corrected the process-context claim under Facts, which overstated a native-run
  guarantee as covering delegation protocol 2.
- Added a fail-closed permission gate on caller-supplied JSON: a policy,
  manifest, descriptor, or evidence file that is world-writable, or owned by
  neither the caller nor root, is rejected before parsing. Package-owned
  resources are exempt.
  The first attempt also rejected group-writable files. That failed 98 tests
  under `umask 002`, which is a correct simulation of real use rather than a
  test defect: `stat` cannot distinguish a user private group from a shared one
  such as Darwin's `staff`, so the check fired on correct setups far more often
  than on dangerous ones. Group-write is now a documented residual in
  `README.md` and in `json_input._has_exclusive_write_owner`. Both umasks are
  covered by tests; do not re-tighten this without rerunning under `umask 002`.
- Pinned the classifier's backtracking bound. The length floor is what keeps the
  nested bounded wildcards off full-length input; that ordering is now a
  documented contract with a regression test.
- Protocol 2 now binds model, effort, source profile, provider family,
  transport, capability, and directional cross-vendor grants into one reviewed
  descriptor/fingerprint.
- Same-vendor routing remains the default. Cross-vendor routing is never
  inferred and requires an exact explicit directional grant.
- Model, effort, account/profile, subscription, entitlement, and billing labels
  remain opaque user configuration; the router does not infer availability or
  quota.
- Orca-inspired orchestration structure was adopted where locally enforceable:
  task/dispatch provenance, explicit ownership, DAG readiness, typed gates,
  projections, write-conflict declarations, and completion-versus-settlement
  distinctions.
- Traceability covers Orca plus seven external orchestration tools and binds
  each adopted/non-goal claim to a schema path, importable validator, and
  collected test. This is design traceability, not a claim that weightclass
  reproduces every external tool.
- Protocol 1 behavior remains compatible. Protocol 2 still starts at most one
  reviewed foreground direct child and adds no retry, fallback, background
  execution, provider HTTP, credential access, or task persistence.
- The packaged qualification registry remains empty. Do not advertise a
  bundled, independently qualified real Claude/Codex delegation adapter.

## Key Files & State

- `src/weightclass/native_v2_schema.py`: native Protocol 2 policy validation.
- `src/weightclass/native_v2_compile.py`: deterministic native route compiler.
- `src/weightclass/native_v2_runtime.py`: reviewed single-child native runtime.
- `src/weightclass/delegation_v2_schema.py`: delegation Protocol 2 schema.
- `src/weightclass/delegation_v2_compile.py`: descriptor/fingerprint compiler.
- `src/weightclass/delegation_v2_graph.py`: orchestration DAG validation.
- `src/weightclass/delegation_v2_permissions.py`: capability/grant validation.
- `src/weightclass/delegation_v2_runtime.py`: reviewed external-runtime boundary.
- `tests/fixtures/orchestration_traceability.json`: adopted/non-goal research map.
- `docs/protocol-v2-specification.md`: normative Protocol 2 contract.
- `docs/protocol-v2-security.md`: security boundaries and residual risks.
- `docs/protocol-v2-migration.md`: Protocol 1 to Protocol 2 migration guidance.
- `docs/completion-audit-v2.md`: requirement-to-test completion map.
- `.github/workflows/release.yml`: immutable candidate validation and PyPI
  Trusted Publishing flow.

## Important Context / Decisions

- Facts:
  - Review/run bind one immutable descriptor, fingerprint, and argv truth.
  - Unknown, ambiguous, unsupported, or unsafe inputs fail closed with
    value-free diagnostics.
  - Native schema-2 `run` and delegation protocol-1 `run` check the process
    context before task access and again next to spawn. Delegation protocol-2
    `run` checks it only at the spawn seam; that is what
    `docs/protocol-v2-specification.md` requires, which scopes the pre-task
    check to native run. Do not restate this as a uniform guarantee. Exclusive
    direct-child wait-status ownership is a documented prerequisite; hostile
    concurrent native mutation remains a residual race.
  - External runtimes own provider authentication, network, quota, billing,
    descendants, and output behavior.
  - Task content must never be logged, persisted, hashed, or included in review
    artifacts or diagnostics.
- Assumptions:
  - Future work starts from `origin/main`, not by extending the merged feature
    branch.
  - No new routing behavior is required until real-user feedback or a new
    explicitly scoped version request arrives.

## Verification

For 0.6.0, as released:

- Full source suite with `ResourceWarning` as error, on the merge commit:
  628/628 passed on Python 3.10, 3.13, and 3.14 (616 before, plus 12 new
  regression tests).
- The suite passes identically under `umask 022` and `umask 002`. Keep both
  covered; the permission gate is the reason.
- Ruff check/format and strict mypy (90 source files): clean.
- `python -m build`, `twine check --strict`, and
  `verify_distribution_isolation.py --run-sdist-tests` (623 passed, 11 skips).
- GitHub CI on `a11d2d6` and every release job on tag `v0.6.0`: success.
- PyPI 0.6.0 files verified:
  - `weightclass-0.6.0-py3-none-any.whl`
    SHA-256 `f83697649dbf316e03bea439f3d7be7bb6fab5cb99dd188dd25eba0c7d0ed972`
  - `weightclass-0.6.0.tar.gz`
    SHA-256 `1fe1448ae1a3ab529aeade320fad3e6db8f33fe3aa2227f1adf862d2dbb3b308`
- Clean-environment `uv tool install weightclass`: `wclass --version` reports
  `weightclass 0.6.0`; classification smoke tests and the empty-task exit-2 path
  behave as documented; a `0o666` policy is refused while `0o644` and `0o664`
  are accepted.

Known flake, pre-existing and unrelated to 0.6.0: running two full suites
concurrently makes the SIGINT and process-group tests in
`DelegationConformanceRunnerTests` fail unpredictably. The same experiment on
`3f0167a` fails on the same test names, so do not attribute it to this release.
Sequential runs pass.

For 0.5.0, as released:

- Full source suite after final fixes:
  - Python 3.14: 616/616 passed with `ResourceWarning` as error.
  - Python 3.10: 616/616 passed with `ResourceWarning` as error.
- Fresh extracted sdist:
  - Python 3.14: 611/611 passed; 11 intentional skips.
  - Python 3.10: 611/611 passed; 11 intentional skips.
- Passed: Ruff check/format, strict mypy, Python 3.10/3.14 `compileall`, YAML
  parsing, `git diff --check`, offline wheel installation, CLI version and
  classification smoke tests.
- GitHub CI and all release jobs passed on exact reviewed head `8121f52` and
  merge/tag target `3f0167a`.
- PyPI 0.5.0 files verified:
  - `weightclass-0.5.0-py3-none-any.whl`
    SHA-256 `3c8730bb20f0a5ae4170879f7c8e08e6bdf115892f30abdc7133b384c51ad86d`
  - `weightclass-0.5.0.tar.gz`
    SHA-256 `5eba2c77ab5b96247ab027c6acb948e16e3481ce413fb8941e8052719b4ef0ca`

## Ultra Review Loop

- Five rounds were run over PR #23.
- Final reviewed head: `8121f5295766053a172a4171b19254ed176d5d8f`.
- Final target hash:
  `3abd9d35a359eca7b41b0c36550a27e3979bd09ba77dd03c476b10a7218a457c`.
- Final convergence used full native Codex coverage and valid Forge partial
  coverage; accepted CRITICAL/HIGH blockers: zero.
- Claude/Grok/Antigravity outputs that timed out or violated schema/citation
  contracts were discarded, never normalized into approvals.
- Review ledger/report are outside the repository at
  `~/.codex/artifacts/ultra-review-loop/pr23-1786315205/`.

## Blockers & Open Questions

- No blocker or required follow-up remains for 0.5.0 or 0.6.0.
- `packaging/homebrew/weightclass.rb` still pins the 0.4.0 sdist URL and hash,
  so the tap is two releases behind. It was already stale before 0.6.0.
  Updating it is a change to `ictechgy/homebrew-tap`, per `RELEASING.md`.
- 0.6.0 shipped without human review. Its judgement calls are the ones to
  revisit first if something turns out wrong: allowing group-writable policy
  files, leaving `_DeferredSigint` unmerged, and correcting the documentation
  rather than the code for the delegation protocol-2 process-context check.
- The permission gate is a narrow compatibility break: a world-writable policy,
  or one owned by another user, worked before and now fails closed with
  `invalid_input` (exit 2). `chmod o-w` is the fix. This is why 0.6.0 is a minor
  bump under the `0.x` policy in `RELEASING.md`.
- Considered and not done: adding the pre-task process-context check to the
  delegation protocol-2 run path so all three run paths match. The current code
  matches `docs/protocol-v2-specification.md`, so aligning them would be a
  normative change and needs its own scoped request.
- Optional future work only:
  - collect real-user routing feedback and add predeclared regressions;
  - independently qualify a concrete runtime only after external-oracle,
    hostile-negative-control, identity, and containment gates are satisfied;
  - update pinned GitHub actions when a reviewed upstream Node-runtime migration
    is needed.

## What Worked

- Tests-first fixes for every accepted review finding.
- Exact descriptor/fingerprint and immutable release-candidate boundaries.
- Requiring contract-valid reviewer output and independently reproducing claims
  before accepting them.
- Fresh Python 3.10/3.14 source, sdist, installed-wheel, macOS-boundary, and
  release validation before publishing.

## What Did Not Work / Avoid

- Do not treat provider prose, malformed JSON, invalid citations, or timeouts as
  review approval.
- One Forge review attempt could traverse from its session directory to the raw
  target; that entire track was discarded. Later Forge runs used an isolated
  prompt-only root. Preserve that isolation if external review is repeated.
- Do not rerun the full review/release workflow merely to reconfirm unchanged
  code. Re-run it only after a new code or policy delta.
- Do not populate the qualification registry or claim real provider delegation
  from synthetic/self-attested evidence.

## Next Steps

1. If no new feature is requested, stop; 0.6.0 is complete and deployed.
2. Optionally read 0.6.0 after the fact and record whether its three judgement
   calls stand. It was published without that pass.
3. For new work, fetch `origin/main`, create a new branch from `a11d2d6`, and
   re-read `AGENTS.md` plus the relevant Protocol 2 docs.
4. Preserve Protocol 1 compatibility, explicit cross-vendor opt-in, transient
   task handling, and the single-reviewed-child execution boundary.

## Resume Prompt

Open this repository at
`/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md` and
`AGENTS.md`, then continue from: `weightclass 0.6.0 is merged, tagged, and
published to PyPI; no mandatory work remains. It shipped without human review,
so its three judgement calls are unconfirmed. If a new request exists, branch
from origin/main at a11d2d6 and preserve the documented Protocol
1/privacy/process boundaries.`
