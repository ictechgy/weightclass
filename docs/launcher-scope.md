# Narrowing weightclass to the launcher it is

**Status: proposal for review.** After the cheap-first plan closed at A0
([`verifier-recall-result.md`](verifier-recall-result.md)), the owner chose to
narrow the tool rather than keep a cost-saving framing nothing measured supports.
This document fixes what stays, what goes, and in what order, so each removal is
one reviewable PR. Written 2026-09-05.

## 1. The product after narrowing

`wclass` starts one agent CLI process with the exact command you reviewed. That is
the whole product.

- **Commands kept:** `discover`, `classify` (local, offline), `route`, `run`,
  `usage`, `example-policy`, `review-preset`, and the two policy generators
  `profile` and `select` (still absent from the top-level listing).
- **Policy schemas kept:** 1 (built-in and simple custom routes), 2 and 3 (native
  policies with profiles, targets, observation-bound review, endpoint-transition
  confirmation, and the aggregate usage store). Schema 3 stays because the usage
  store is bound to it; dropping it would delete `usage` too.
- **Boundaries unchanged:** exactly one foreground child; no credential reads, no
  network, no persisted task content; opaque labels; fail-closed input; fingerprint
  binding between `route` and `run`; the terminal review default.
- **Research record kept:** every study under `docs/` and every offline scorer
  under `tests/eval/`, including the verifier-recall harness. They are evidence,
  not product, and they cost nothing to keep.

## 2. What goes, and why each item is safe to remove

| Removal | What it is | Why it goes | Coupling to untangle |
| --- | --- | --- | --- |
| `wclass-advisory` companion (`src/weightclass/advisory/`, 20,466 lines, 43 test modules, the packaged Skill bundle and its upgrade ledger) | Campaigns, verifiers, councils, speculative cheap-first runner, managed state | It is the research harness for a hypothesis A0 answered. Its `ask` one-shot is a thin wrapper over a vendor CLI the user already has. HANDOFF Next Steps B already recommended moving it out. | Release workflow ledger step and smoke lines; Homebrew formula test (20 lines); `.weightclass/verify-review` and `verify-design`; the protected `tests/test_advisory_hardening_batch.py` and the `verify` gate that names it; README "Advisory companion" (232 lines); `docs/advisory-*.md` (moved, not deleted). |
| `delegate` (`delegation_*.py`, `delegation_v2_*.py`, `delegation_qualifications.json`, 33 test modules) | Role delegation protocols WCD1/WCD2, conformance drivers, an intentionally empty qualification registry | Every qualified path fails closed with `unsupported_route` today; the README says so. | `native_v2_runtime.py` imports `validate_runtime_process_context` from `delegation_runtime.py`; move that function (and its `DelegationRuntimeUnavailableError`) into `process_context.py` first. `native_v3_delegation` route/run under `delegate native` goes with it. |
| `v2` API routing (`v2.py`, the `v2` subcommand, `--confirm-api-egress`) | Declarative API route served by a user-supplied external runtime | Unrelated to launching an installed CLI; no runtime ships. | `advisory/` imports it (gone together). `v2_validation.py` and `canonical_v2.py` are shared validation for native schemas and **stay**. |
| `recommend`, `review-cost-profile` (`cost_recommendation.py`, 4 test modules) | Evidence-gated cost recommendation with qualification cards | Only ever authorised one candidate; the economics it gates are the ones this project measured as absent. | `docs/cost-recommendation.md` moves to the archive. |
| `render` | Prints a policy route's command from a workflow descriptor | Subset of `route`. | None found. |
| `classify --ask-vendor` and `--show-triage-command` (`triage.py`, 931 lines, 8 test modules) | Vendor-assisted triage | Sends the task to a vendor twice; the README already says it is not a saving path. `classify` stays local. | `classification_cli.py` imports it; the entrypoint fast path must not regress (`test_cli_startup`). |

Not removed, and stated so it is not re-litigated: `agent_discovery.py`
(`discover`, `profile`, `select`), `usage_aggregation.py`, `native_v2_*`,
`native_v3_*`, `router.py`, `classification.py`, `foreground_process.py`,
`process_context.py`, `executable_observation.py`, `json_input.py`,
`task_v2.py`, `adapter_registry.py`.

## 3. Documents

Nothing is deleted. Documents that describe removed surfaces move to
`docs/archive/` with one index file saying what each described and in which
release it last shipped (0.31.1). The README loses "Advisory companion",
"Reviewed role delegation", "V2 API routing", the `recommend` subsection, and
the `--ask-vendor` paragraphs; it keeps the measured-results section and the
security boundary. `AGENTS.md` drops the advisory scoped file from its index;
`src/weightclass/advisory/AGENTS.md` goes with the package.

## 4. The verify gate

`.weightclass/verify` protects `tests/test_advisory_hardening_batch.py`. Removing
the advisory package removes that file, so the gate must be edited. Per
`.weightclass/AGENTS.md` that is allowed as a deliberate, disclosed act after the
campaign is complete: the commit body states that the protected acceptance file is
deleted with the surface it gated, that no result is being made to pass, and that
`verify` keeps protecting itself. `verify-review` and `verify-design` are advisory
verifiers and go with it.

## 5. Order of PRs

1. **This document** (review, then merge).
2. **Remove the advisory companion.** Code, tests, entrypoint, Skill bundle and
   ledger, release-workflow steps, formula test lines, the two advisory verifiers,
   the `verify` edit, README section, docs moved to the archive. One PR; large but
   mechanical, and the gate proves nothing else depended on it.
3. **Remove `delegate` and `v2`.** Move `validate_runtime_process_context` first,
   then delete the families and their tests.
4. **Remove `recommend`, `review-cost-profile`, `render`, and vendor triage.**
5. **Rewrite the README and `HANDOFF.md` for the launcher; refresh
   `src/weightclass/AGENTS.md`.** The measured record stays where it is.
6. **Release 0.32.0.** Breaking: `wclass-advisory` and five subcommands are gone;
   0.31.1 is the last release that has them. The advisory Skill ledger step leaves
   the release workflow in the same PR that removes the bundle, and the release
   notes say why the "previous bundle" check no longer applies.

Each PR runs the full gate. After PR 2 the distribution isolation tests must be
re-pointed at what still ships (`REQUIRED_WHEEL_ADVISORY_PATHS` and friends in
`tests/verify_distribution_isolation.py`).

## 6. What this does not decide

Whether `profile`/`select` and schema 2 survive a later pass, and whether the
tool should eventually accept a task from a file or an argument instead of
standard input only. Both are ergonomics questions for after the cut, not part of
it.
