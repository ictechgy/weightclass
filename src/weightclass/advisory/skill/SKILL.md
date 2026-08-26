---
name: advisory
description: Run the local sealed weightclass advisory workflow when the user explicitly asks for advisory mode. Select implementation, review, research, diagnosis, or design; do not trigger for ordinary work or open-ended brainstorming.
---

# Advisory

Use the installed `wclass-advisory` command to obtain per-vendor cost and
acceptance evidence without mixing workflow populations. Compare vendors only
when the same admitted task was deliberately dispatched to each one.

Reviewed route profiles may use schema-1 Claude, Codex, agy, or Grok, or schema-2
arbitrary-vendor command matrices. Before execution, inspect the task-free route
review and report its delivery contract: `stdin`, `argv`, or private file, plus
whether any selected role puts task text in argv. An agy `{{task}}` route has the
documented local process-inspection exposure. A Grok `{{task_file}}` route uses
an owner-only transient file outside the Git workspace. Never guess model
quality, cost, entitlement, or subscription availability; agy/Grok model labels
are user-selected opaque configuration.

## Gate the run

- Run only when the user explicitly requests advisory or invokes this skill.
  The request authorizes one bounded dispatch for the stated task; retain the
  CLI's required `--confirm-task-egress` flag. A generic request to review,
  research, diagnose, design, or implement is not advisory authorization.
- Require a clean Git checkout and `command -v wclass-advisory`.
- Run `wclass-advisory doctor --vendor <vendor-or-all> --workflow <workflow>`
  before preparing task input. `doctor` validates every existing anonymous lane,
  locally invokes installed CLI `--help`/`--version` with a minimal environment
  and temporary working directory, sends no task or provider prompt, and reports
  `campaign_ready` separately from `dispatch_ready`. Stop on a false
  `dispatch_ready`; dispatch repeats this local check for every role before task
  inspection. A record-binding error is therefore reported before dispatch. Do not ask the user for
  profile, campaign, verifier-wrapper, price-table, or result-root paths when
  managed onboarding is ready.
- If authentication, model availability, account limits, or an opaque provider
  failure must be distinguished, explain that the next command makes three
  task-free provider calls per vendor and may consume quota or incur cost. Run
  `wclass-advisory provider-check --vendor <vendor-or-all> --workflow <workflow>
  --confirm-provider-egress` only after that explicit approval. It stores no
  output and emits `sample_recorded:false`; never count it as campaign evidence.
- This managed skill always uses `init`, `doctor`, managed `review`, and
  `dispatch`; `run --campaign-root` is the advanced low-level compatibility
  surface, not this skill's entry point. If loaded instructions ask for opaque
  campaign paths, or a session claims egress confirmation is unsupported after
  inspecting only `run --help`, treat that session or skill as stale and reload
  the installed skill in a new session. Do not fall back to direct implementation
  as a substitute for an explicitly requested advisory run.
- Never invoke a machine-specific `wclass-advisory-local run` legacy campaign
  from this skill. The canonical cross-project entry point is managed
  `wclass-advisory dispatch`; legacy result populations remain read-only and
  must not be repaired, resealed, or combined with managed records.
- Report lane contention only for the exact `managed_lane_unavailable` error.
  `managed_campaign_capacity_reached` means the sealed sample cap was reached;
  `managed_allocator_busy` means the short cross-lane allocator exceeded its
  bounded wait and is not a vendor/model run;
  `managed_dispatch_rejected` is a non-lane preflight or binding rejection and
  must not be relabeled as contention. `doctor` reports the configured lane
  count plus a point-in-time `free`/`busy` snapshot for each vendor/workflow;
  availability may change immediately after the snapshot. Ten lanes are
  configured per vendor and workflow by default.
- Treat `campaign_record_binding_mismatch` and the other fixed
  `campaign_record_*`/`campaign_records_invalid` errors as an unhealthy sealed
  population, not contention. Never rewrite records, copy a current
  fingerprint into old records, reseal over the population, or silently start
  a replacement campaign. Report the exact value-free code and stop. Do not
  substitute direct implementation for the explicitly requested advisory run.
- Claude evidence executors use a structured-output generation distinct from
  the earlier plan-mode population. If Claude implementation doctor is ready
  but an evidence workflow reports managed configuration unavailable after an
  upgrade, run `wclass-advisory migrate-evidence --vendor claude --dry-run`.
  Explain that all old Claude evidence records remain separate and read-only,
  obtain approval for the state change, then run the same command without
  `--dry-run`. Never copy old records into the new generation.
- Release 0.17.8 also gives agy a new all-workflow route generation and Grok a
  new evidence generation. If their doctor reports managed configuration
  unavailable after upgrade, preview and then run `migrate-routes --vendor agy`
  or `migrate-evidence --vendor grok`. Explain that every previous manifest and
  record remains separate and read-only; never copy or merge them.
- Select one workflow from the user's outcome. Read
  [references/modes.md](references/modes.md) when choosing a mode or preparing
  its verifier.
- Do not route brainstorming through this tool. Explain that brainstorming is
  experimental and use an ordinary non-campaign workflow unless the user asks
  to run a separately pre-registered generator-critic study.

## Fix acceptance before dispatch

The clean baseline commit must already contain the prospective verifier for the
selected task. Create and commit it first when the user's requested workflow
includes end-to-end implementation and normal repository commits are in scope.
Otherwise stop and explain what verifier is missing.

- `implementation`: `.weightclass/verify`
- `review`: `.weightclass/verify-review`
- `research`: `.weightclass/verify-research`
- `diagnosis`: `.weightclass/verify-diagnosis`
- `design`: `.weightclass/verify-design`

The verifier must return `42` for the tool's task-free baseline probe, `0` only
for a result meeting the prospectively fixed task criteria, and another code
for unrelated or infrastructure failure. Evidence verifiers read the closed
JSON result from stdin and check `WCLASS_ADVISORY_WORKFLOW`. Do not use schema
validity alone as the oracle for factual, diagnostic, design, or review quality.

Commit the verifier and focused failing acceptance before observing any model
candidate. Keep the verifier unchanged during the campaign.

## Initialize once when needed

If `doctor` reports `managed_configuration_unavailable`, explain that one-time
owner-private initialization is required. Ask only for the selected vendor's
exact cheap, advisor, and expensive model and effort labels, plus an optional
price table when the user wants comparable cost estimates. Treat every label
and subscription entitlement as opaque user input; never infer it.

After the user approves persisting that task-free configuration, run:

```sh
wclass-advisory init \
  --vendor <codex|claude|agy|grok> \
  --model cheap=<label> --model advisor=<label> --model expensive=<label> \
  --effort cheap=<label> --effort advisor=<label> --effort expensive=<label>
```

Add `--prices <user-supplied-table>` only when provided. Without it, the
campaign uses the vendor-reported cost basis and may abstain from an economic
verdict when that vendor does not report complete cost. An arbitrary schema-2
vendor may instead use `init --profile <reviewed-profile>`. Initialization is
idempotent for identical inputs and refuses to overwrite a different campaign.

## Dispatch safely

1. Put the task in a new owner-only regular temporary file outside the
   repository. Never put it in argv, echo it, hash it, include it in a label, or
   retain it in a diagnostic.
2. Review the exact routes with
   `wclass-advisory review --vendor <vendor-or-all> --workflow <workflow>`
   when they have not already been reviewed for this machine configuration.
3. Run:

   ```sh
   wclass-advisory dispatch \
     --workflow <workflow> \
     --repo <absolute-clean-repo> \
     --task-file <owner-only-task-file> \
     --vendor <configured-vendor-or-all> \
     --confirm-task-egress
   ```

   Managed dispatch validates the bound profiles, campaigns, price tables,
   central verifier, project verifier, and ordinals before reading task input.
   After lane allocation it immediately emits one task-free
   `managed_dispatch_started` event with vendor and anonymous lane index. This
   event means model work has started; silence before it is preflight or the
   bounded allocator wait, while silence after it can be a long-running arm.
   Multiple selected vendors start independent campaigns concurrently;
   each vendor's cheap/advisor/retry/expensive stages remain sequential inside
   its anonymous fixed lane. Each command acquires one free lane for its exact
   vendor/workflow before it starts a child; no free lane fails closed. Lane 0 is the
   existing campaign root. Ten lanes are available by default; the extra lanes
   are bounded `.lanes/lane-XX`
   directories with no project-derived names. Reports and promotion gates must
   include every existing lane after independent manifest validation and
   transient-only ordinal merging. Each top-level job has a finite outer
   deadline and bounded retained output. Each lane owns an independent local
   ordinal. Use one vendor only when the user narrows the run. Do not add retries
   around this command; its sealed Shape-B policy owns the bounded retry and
   fallback behavior.
   Before a new campaign attempt creates a workspace, the runner automatically
   removes registered residue in that same lane while holding its
   campaign lock. An `advisory_stale_workspace_cleanup` receipt contains only
   registered, removed, and retained counts. For manual recovery, run managed
   `cleanup`; it cleans every inactive lane independently, skips active lanes,
   and returns a task-free `managed_cleanup` receipt. Rerun only when
   `complete` is false; never delete state directories or campaign logs by hand.
4. Delete exactly the temporary task file in a finally/cleanup path, including
   on preflight or provider failure.

## Handle the result

- `implementation` returns a verified patch path. Inspect it and apply it only
  when repository changes are within the user's request; rerun the repository's
  ordinary checks after applying it.
- A failed cheap, retry, or expensive acceptance arm emits one
  schema-2 `advisory_attempt_failed` JSON receipt with fixed `vendor` and `role`
  fields. Advisor calls retain their existing
  separate route status and do not emit attempt receipts. Receipt objects are
  JSON lines on stderr among other human diagnostics; parse only lines whose
  `event` is exactly `advisory_attempt_failed`. Use only the receipt's
  fixed `failure_stage`, child/verifier exit and timeout fields, and numeric
  candidate counts to explain where the attempt stopped. A fixed
  `child_failure_code` and stdout/stderr presence booleans distinguish common
  provider/CLI failures without retaining either stream. Treat the category as
  a bounded heuristic, not provider-authored proof. For
  `failure_stage=verification`, compare `verify_exit_code` with the committed
  task-specific verifier's documented exit codes. A
  `failure_stage=verification_integrity` receipt means the verifier exited but
  changed patched files, so its numeric exit code is not the final acceptance
  verdict. A prospective verifier should
  use distinct nonzero exit codes for materially different acceptance phases
  when that distinction is useful. Never request a deleted workspace, infer a
  failure from model prose, or claim the receipt contains raw verifier output.
  The receipt is operational evidence, not an effectiveness verdict.
  Evidence receipts additionally expose only a fixed `result_shape` and an
  `envelope_extracted` boolean. Use these to distinguish structured output,
  JSON text, fenced JSON, prose, empty output, and malformed/empty envelopes;
  they never contain the child result itself.
- Evidence workflows print the winning canonical JSON only after aggregate
  logging. Use that transient output to answer the user. Do not save the result
  body, verifier output, advice, task, source paths, or fingerprints elsewhere.
- Report which vendor arms ran, whether acceptance passed, and whether a patch
  or structured result was retained. Treat provider/timeout failures as
  campaign evidence, not permission to run unbounded manual retries.
- Keep every workflow and vendor in its already-sealed campaign. Never move or
  combine logs to manufacture a larger sample.
