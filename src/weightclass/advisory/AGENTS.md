# Advisory companion — `src/weightclass/advisory`

Scope: the separately installed `wclass-advisory` command. The root
[`AGENTS.md`](../../../AGENTS.md) and the core
[`AGENTS.md`](../AGENTS.md) both still apply.

## What this surface is, and is not

`wclass-advisory` is an **explicit, experimental** command. It is not a
`wclass run` mode.

- It must never be selected automatically.
- It must never weaken the one-foreground-child contract of core `wclass`.
- It must never claim effectiveness before the documented evidence gates pass.
- No campaign result may authorize core routing. Even a passing gate only
  reaches `eligible_for_human_review`; `policy_decision_allowed` and
  `core_routing_changed` stay false.

What it *may* do is execute the bounded cheap/advisor/retry/expensive sequence
sealed by a campaign, and only that sequence. Implementation work returns a
**patch-only handoff**: a candidate patch is inspected, checked with `git apply
--check`, and applied by a human. The companion does not write into the
operator's worktree on their behalf.

`ask` is the stateless one-shot surface: no init, no campaign, no project
verifier, no persisted sample. Everything under `campaign`, `consult`,
`dispatch`, `experiment`, and `portfolio` is measurement apparatus, not a
product promise.

## Egress consent

Every task-bearing run needs explicit consent before the task is read. The three
sources are recorded in the receipt as `task_egress_confirmation_source`:

- `flag` — `--confirm-task-egress` on the command.
- `terminal` — an answer on the controlling terminal.
- `session_environment` — `WCLASS_ADVISORY_EGRESS` set to exactly `session`.

The session grant is a **standing, inheritable** grant, not a per-invocation one.
It covers every later call in that shell and every descendant process, and
exporting it from a shell rc file makes the prompt permanently unreachable.
Treat it as a deliberate contract change that enables automation, and never
describe it as merely ergonomic.

Two invariants protect it and must not be removed:

- **Skipping the question never skips the disclosure.** When a terminal is
  available, the vendor list, per-member delivery mode, and the context warning
  still print under a session grant. Losing the confirmation prompt is
  acceptable; losing the only per-run disclosure is not.
- The variable stays out of `default_child_env`, so no vendor CLI receives it.

Nothing about consent is written to disk. A repository- or timestamp-scoped
grant would violate the root rule on forbidden store fields, which is why the
grant lives only for the shell session.

## Task delivery

**No built-in advisory route carries a task in argv.** Keep it that way.

- `claude` and `codex` read the task on stdin.
- `agy` reads one NDJSON user message on stdin under
  `--input-format stream-json`. The CLI itself rejects an argv prompt in that
  mode, so there is no task slot to review. The local capability check requires
  `--input-format`, so an agy build without stream-json input fails closed
  before any task is read.
- `grok` reads it through an inherited `/dev/fd/N` pipe with no task pathname.

The stdin payload is wrapped only when a route **declares** NDJSON input, judged
by argv token position — never by executable name or substring, because a
renamed wrapper would silently lose the envelope and a flag value containing the
same characters would silently gain one.

A route that declares NDJSON input while also carrying a `{{task}}` or
`{{task_file}}` slot describes two destinations for one task. Reject it; do not
resolve it in one direction.

Route review and `--preview` report `task_stdin_encoding`, because the reviewed
argv alone would not tell an operator what bytes the child receives.

## Councils

`ask --council` names two to four distinct built-in vendors. Members start
together in fresh independent processes and one member's output never enters
another's prompt.

- Each member's timeout is fixed once at submission. Re-reading the clock inside
  a worker makes identical input produce different budgets across runs.
- Results are always reported in the requested vendor order, never in completion
  order.
- A failed peer is not cancelled. Cancelling discards work the vendor has
  already charged for; this matches the campaign coordinator's decision.
- The target worktree is compared once before the members start and once after
  all of them finish.
- The council is descriptive. It reports consensus and dissent and never ranks
  or selects a winner.

`advisory_parallel.run_parallel` coordinates campaign commands and is not a
drop-in for councils: it is command-oriented and cannot carry grok's anonymous
task pipe.

## Managed state

The opt-in `init` command may persist only caller-supplied, task-free
model/effort profiles, price tables, sealed campaign contracts, owner-private
result lanes, and the package verifier, all under a separate managed advisory
root. It never discovers or writes vendor configuration.

The forbidden-field rule from the root file applies without exception: no task
content, task identifiers, timestamps, profiles/accounts, repository paths, or
task-derived fingerprints.

Aggregate campaign records follow the same rule. Raw provider output is never
retained.

## Sealed populations and migrations

Sealed records are immutable.

- Never synthesize a sample, repair a fingerprint, backfill a task category or
  timestamp, or merge two populations.
- Report failures by their exact codes. Only `managed_lane_unavailable` means
  contention; `managed_campaign_capacity_reached` and `campaign_record_*` mean
  something else.
- When a change alters a vendor's reviewed argv, every fingerprint for that
  vendor changes with it. Existing sealed populations must complete the
  documented `migrate-routes --vendor <vendor>` or `migrate-evidence --vendor
  <vendor>` step, which creates a **new empty generation**. No old record is
  rewritten or merged.
- A formal new claim needs `migrate-gate` before the first dispatch and exactly
  one primary vendor/workflow per managed state root. The pre-gate source
  generation stays available only for exploratory, never promotion-eligible
  analysis.

Schema validity is not factual validity. Every campaign still needs a
pre-registered task-specific verifier; see
[`../../../.weightclass/AGENTS.md`](../../../.weightclass/AGENTS.md).

## The packaged Skill bundle

`skill/` holds the exact package-owned bundle. Publishing it is the
**sole vendor-recognized-path exception** in this project: `skill install` may
write only that exact reviewed bundle into the selected personal Codex or
Claude Skill directory, and `skill uninstall --confirm` may remove only an exact
package-owned bundle. Neither discovers or rewrites any other vendor
configuration, and a customized destination must fail closed.

When any bundle file changes:

1. Bump `managed_onboarding` in `skill/manifest.json`.
2. Add the **exact previously published** bundle's four file hashes to the
   ledger in `install_advisory_skill.py` and register them in
   `historical_bundle_file_sha256()`. Compute them from the previous release
   tag, not from an assumption about the working tree.
3. Confirm with
   `python3 tests/verify_advisory_skill_ledger.py --repository . --previous-ref v<previous>`.

The bundle is agent-facing instructions. If a change makes a statement in
`SKILL.md` or `references/modes.md` false, fixing the bundle is part of that
change, not a follow-up.

## Evidence discipline

The sealed campaigns need at least 60 usable tasks and 12 advised failures each,
under a user-supplied single-origin price table, before any effectiveness claim.
Observed advised rescue is small and the economic verdict has never been
identifiable, because no reviewed price table has ever been supplied.

Do not integrate retry or advice into core `wclass` from these pilots. Report
what was measured, including when the answer is that nothing was established.
