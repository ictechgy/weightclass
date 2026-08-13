# weightclass repository review

Baseline reviewed commit: `3d747b37b4e32151b30d5cd772624a96bd6fd769`

Scope: repository-wide architecture, performance, security, release workflow, and test adequacy. This review did not invoke a vendor runtime or access credentials. The separately approved network work was limited to the repository push and GitHub protection-setting APIs.

## Executive summary

The runtime boundary is generally conservative: task input is bounded, ordinary routing launches one foreground child, subprocesses do not use a shell, API egress requires explicit acknowledgement, runtime dependencies are empty, and GitHub Actions are SHA-pinned. The current 800-test suite and strict static checks are strong regression evidence.

The baseline review found four actionable items. The current working tree implements repository-side remediations for all four: a hash-pinned release toolchain, centralized recursion redaction, a tag-to-main ancestry gate, and a lightweight classification entrypoint. GitHub now also has an active `v*` tag ruleset that blocks tag updates and deletions without a bypass, plus one required reviewer on the `pypi` environment.

## Remediation status

- R-01: repository fix implemented. The release uses a reviewed full transitive hash allow-list, exact CPython and setuptools versions, `--no-deps`, and `--no-isolation`. A clean Python 3.13 environment executes the entire toolchain; the next live Ubuntu release remains the platform-specific wheel-selection proof.
- R-02: fixed and covered by native/V2 public-CLI regressions on Python 3.10 and 3.14.
- R-03: fixed and independently re-read from GitHub. Repository defense in depth is behavior-tested with real temporary Git histories; the active `v*` ruleset blocks updates and deletions, and `pypi` has one required reviewer.
- R-04: the local `classify` command now bypasses the full CLI module and lazily imports vendor triage only when requested. Its measured cold-start median fell from 71.57 ms to 34.84 ms on the same local host.

## Findings

### R-01 — P1 — Pin the release build toolchain — remediated in working tree

Category: security / supply chain / release reliability

Evidence:

- `.github/workflows/release.yml:20` installs unversioned `ruff`, `mypy`, `build`, and `twine` from the network.
- `.github/workflows/release.yml:29` builds the sole publish candidate in that mutable environment.
- `pyproject.toml:2` permits any future `setuptools>=77` inside build isolation.
- `.github/workflows/release.yml:178-202` later grants trusted-publishing OIDC authority and publishes that candidate.
- The repository contains no release constraint or hash-lock file for these packages.

Impact:

An upstream compromise or incompatible future release can change or break the artifact produced from an otherwise reviewed tag. The candidate manifest preserves the exact bytes that were built, but it cannot make a mutable builder trustworthy.

Recommendation:

Use a reviewed, hash-pinned release constraints file for the networked tool installation and pin the build backend. Preinstall the pinned backend and build with isolation disabled, or otherwise ensure the isolated build requirements are locked to reviewed versions. Retain the existing SHA-pinned Actions and immutable-candidate checks.

Suggested regression evidence:

- A structural test requiring exact pinned versions and hashes for release tools.
- A build test proving no dependency resolution occurs after the pinned installation step.

### R-02 — P2 — Convert JSON decoder recursion failures to redacted invalid input — remediated

Category: reliability / security boundary

Evidence:

- `src/weightclass/json_input.py:100-102` catches `OSError` and `ValueError`, but not `RecursionError` from `json.loads`.
- `src/weightclass/cli.py:394-399` and `src/weightclass/v2.py:74-82` rely on the shared loader to normalize failures.
- On supported Python 3.10, a bounded 7.2 KiB JSON document nested 1,200 levels deep makes both native `wclass route --policy ...` and `wclass v2 route ...` exit 1 with a Python traceback ending in `RecursionError`.
- The same input is parser-version-dependent on Python 3.14, so a green 3.14 run does not cover the supported 3.10 boundary.

Impact:

A malformed local policy can crash automation and expose an implementation traceback instead of the documented redacted `invalid_input` response and exit code 2. The input is size-bounded, so this is not an unbounded-memory issue.

Recommendation:

Catch `RecursionError` centrally in `_load_json_object_from_open_fd` and translate it to `JsonInputError`. Add native and V2 public-CLI regressions under Python 3.10 so specialized callers do not need divergent exception lists.

### R-03 — P2 — Bind release tags to reviewed `main` with repository controls — remediated

Category: security / release governance

Evidence:

- `.github/workflows/release.yml:3-5` accepts any pushed tag matching `v*`.
- `.github/workflows/release.yml:21-29` validates version and tests the tagged commit, but does not prove the commit is reachable from `origin/main`.
- `.github/workflows/release.yml:178-202` publishes from the tag workflow with OIDC authority.
- `RELEASING.md:37-39` describes a `pypi` environment reviewer as optional.
- No repository file can prove that protected-tag rules or mandatory environment reviewers are configured externally.

Impact:

An accidental tag on an unmerged commit can publish code that did not pass the reviewed-main path. A workflow ancestry check helps with operator error, but external tag protection and a mandatory publishing-environment reviewer are the stronger controls because the workflow itself comes from the tagged commit.

Recommendation:

Protect release tags in repository settings, require a reviewer on the `pypi` environment, and document both as mandatory. Also fetch `origin/main` in the workflow and reject a tag whose exact commit is not an ancestor of `origin/main`; treat this as defense in depth rather than the sole authorization control.

### R-04 — P3 — Split or lazily load CLI command families — remediated for local classification

Category: architecture / performance

Evidence:

- `src/weightclass/cli.py:14-104` eagerly imports all native, delegation, V2, runtime, and triage command families.
- The module is approximately 1,750 lines and `main` dispatches every command family at `src/weightclass/cli.py:1632-1750`.
- Local 20-process cold-start measurements on the reviewed commit were:
  - `import weightclass.classification`: median 25.05 ms, p95 25.88 ms
  - `import weightclass.cli`: median 66.45 ms, p95 74.18 ms
  - `python -m weightclass classify`: median 71.57 ms, p95 74.44 ms

Impact:

Simple classification pays roughly 40–45 ms for unrelated protocol imports. This is minor beside a vendor invocation, but visible in tight editor or shell loops. The monolithic dispatcher also broadens the regression surface of small option changes.

Recommendation:

Keep argument parsing and top-level dispatch thin, then import command-specific handlers only after parsing, or split native, preset, delegation, and API handlers into focused modules. Preserve the current fail-closed gate order and exact public-exit tests. Set a cold-start budget before treating this as release-blocking optimization work.

## Architecture assessment

Strengths:

- The one-child foreground execution contract is explicit and separated into owned-process helpers.
- Policy parsing, compilation, execution, and process-context checks are distinct modules even though the CLI composition root is large.
- Cross-vendor routing, API egress, task delivery, and route acknowledgement remain explicit rather than inferred.
- Unsupported or unsafe semantic triage fails closed on non-Darwin platforms.

Primary architectural risk:

- `cli.py` is the composition root and the main coupling hotspot. Further command growth should go into focused handler modules rather than additional imports and branches in this file.

## Performance assessment

- No algorithmic hot-path regression was found in classification or route selection.
- Runtime dependencies remain empty and each production run starts at most one selected vendor child.
- The measurable opportunity is cold-start import cost (R-04), not vendor-runtime throughput.
- The semantic triage filesystem cleanup has linear traversal and bounded descriptor ownership according to its focused tests; it was not benchmarked against a real vendor in this review.

## Security assessment

Strengths:

- Subprocess execution uses exact argv and no shell expansion.
- Ordinary task delivery uses stdin; argv delivery is explicit and surfaced for CLIs that require it.
- JSON/policy reads are size-bounded, regular-file-only, no-follow, descriptor-based, and owner-checked.
- Diagnostics are normally structured and redact task and path content.
- API egress requires explicit confirmation and exact reviewed fingerprint acknowledgement.
- Runtime dependencies are empty; Actions are pinned to commit SHAs; PyPI publishing uses trusted publishing rather than a repository token.

Documented residual boundaries, not new findings:

- Native route fingerprints bind reviewed argv, not executable file identity.
- `agy` and Grok argv task delivery can expose task text to local process inspection.
- Group-writable policy files and post-observation executable replacement retain documented local same-user trust assumptions.
- The external runtime owns authentication, network, billing, and vendor output behavior.

## Recommended order

1. Let the next live Ubuntu tag workflow validate the exact Linux artifacts selected by the release hash allow-list.
2. Preserve the Python 3.10 recursion and Git-history behavioral regressions.
3. Split further command families only behind behavior tests and a recorded cold-start target.
