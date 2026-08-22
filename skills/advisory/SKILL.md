---
name: advisory
description: Run the local sealed weightclass advisory workflow when the user explicitly asks for advisory mode. Select implementation, review, research, diagnosis, or design; do not trigger for ordinary work or open-ended brainstorming.
---

# Advisory

Use the installed `wclass-advisory` command to obtain paired Claude/Codex cost
and acceptance evidence without mixing workflow populations.

## Gate the run

- Run only when the user explicitly requests advisory or invokes this skill.
  The request authorizes one bounded dispatch for the stated task; retain the
  CLI's required `--confirm-task-egress` flag. A generic request to review,
  research, diagnose, design, or implement is not advisory authorization.
- Require a clean Git checkout and `command -v wclass-advisory`.
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

## Dispatch safely

1. Put the task in a new owner-only regular temporary file outside the
   repository. Never put it in argv, echo it, hash it, include it in a label, or
   retain it in a diagnostic.
2. Review the exact routes with `wclass-advisory review` when they have not
   already been reviewed for this machine configuration.
3. Run:

   ```sh
   wclass-advisory run \
     --workflow <workflow> \
     --repo <absolute-clean-repo> \
     --task-file <owner-only-task-file> \
     --vendor both \
     --confirm-task-egress
   ```

   Use one vendor only when the user explicitly narrows the run. Do not add
   retries around this command; its sealed Shape-B policy owns the bounded
   retry and fallback behavior.
4. Delete exactly the temporary task file in a finally/cleanup path, including
   on preflight or provider failure.

## Handle the result

- `implementation` returns a verified patch path. Inspect it and apply it only
  when repository changes are within the user's request; rerun the repository's
  ordinary checks after applying it.
- Evidence workflows print the winning canonical JSON only after aggregate
  logging. Use that transient output to answer the user. Do not save the result
  body, verifier output, advice, task, source paths, or fingerprints elsewhere.
- Report which vendor arms ran, whether acceptance passed, and whether a patch
  or structured result was retained. Treat provider/timeout failures as
  campaign evidence, not permission to run unbounded manual retries.
- Keep every workflow and vendor in its already-sealed campaign. Never move or
  combine logs to manufacture a larger sample.
