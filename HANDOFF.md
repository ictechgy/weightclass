# Handoff

_Last updated: 2026-08-11 13:46 KST by Codex_

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
- No mandatory implementation, release, or distribution work remains.

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

## Key Files & State

- `src/weightclass/cli.py`: V2 route/run ordering and pre-spawn identity check.
- `src/weightclass/v2.py`: API runtime observation and redacted invalid-input
  conversion.
- `tests/test_v2.py`: V2 runtime hardening regressions.
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

## Verification

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

- No blocker or required follow-up remains.
- Optional future work: collect real-user routing feedback; qualify a concrete
  external runtime only with independently reviewed evidence; update pinned
  GitHub Actions only after reviewing upstream migrations.

## Next Steps

1. If no new scoped request exists, stop.
2. For new work, fetch `origin/main`, branch from its head, and re-read
   `AGENTS.md` plus the relevant Protocol 2 documentation.
3. Preserve Protocol 1 compatibility, explicit cross-vendor opt-in, task
   no-retention, and the single-reviewed-child boundary.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`
and `AGENTS.md`, then continue from: `weightclass 0.8.2 is fully deployed;
there is no mandatory follow-up. Start any new work from origin/main and retain
the documented privacy, fingerprint, and process-boundary contracts.`
