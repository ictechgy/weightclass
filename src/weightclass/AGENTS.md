# Core router — `src/weightclass`

Scope: the `wclass` command and every module here. The root
[`AGENTS.md`](../../AGENTS.md) still applies; nothing here relaxes it.

## The V1 contract

`wclass run` may start **exactly one selected vendor process in the foreground**.
It does not retry, recover, background, or supervise that process. If a change
would add a second child, a retry, a supervisor, or a background lane to this
surface, it is out of scope for V1.

By default a route stays with its explicit source vendor. Cross-vendor routing
requires an explicit policy opt-in (`allow_mixed_vendors`), and that opt-in is
deliberately not directional.

## The V2 boundary

Schema-2 and schema-3 native policies stay: they select one installed vendor CLI
and start it in the foreground, and their shared parsing, validation, and
canonical-encoding modules (`native_v2_*`, `native_v3_*`, `canonical_v2.py`,
`v2_validation.py`, `task_v2.py`) are load-bearing for every policy this tool
accepts. The external API runtime that once sat behind `wclass v2` was removed
in 0.32.0 along with the `delegate` family; weightclass starts no user-supplied
orchestration or API runtime, and there is no egress confirmation left to give.

## Review before execution

This is the surviving value of the tool: the operator can see the exact argv and
a route fingerprint before anything starts.

- Built-in and policy-provided vendor commands must be reviewable with
  `wclass route` before `wclass run` is used.
- `route` and `run` read the policy separately. Only `--ack-route-fingerprint`
  binds a reviewed selection to the run; without it the policy can change in
  between and a different command executes.
- When stdout is a terminal, `run` reviews on the controlling terminal by
  default. `--no-review`, an explicit `--json`, or a fingerprint acknowledgement
  selects the non-interactive path, and a pipe never gains a prompt. Keep the
  default keyed to that signal only; do not infer it from the task or the route.
- If the reviewed output would not tell an operator what bytes the child
  receives, the review is incomplete. Disclose the delivery mode, not just the
  argv.

## Task delivery

Prefer standard input. Use it for local classification and for every child that
supports it.

The **native-routing argv exception** is narrow and applies only here: a CLI that
accepts its prompt only as a command-line argument may use an explicitly
reviewed `{{task}}` slot. The built-in core `agy` and `grok` routes currently use
it. Such a route must surface `"task_delivery": "argv"` before execution and
retains the documented local process-inspection exposure — the task is visible in
`ps` to any process of the same user.

Substitution happens once, immediately before spawn, so the reviewed output and
the fingerprint never contain the task. `execve` cannot carry NUL, so argv
delivery rejects it; stdin delivery does not need that restriction.

## Persisted state

Core `wclass` never writes routing state, adaptive state, or vendor
configuration. Its sole persisted state is the explicitly enabled,
aggregate-only schema-3 usage store.

- Validate the enabled store **before** task access, then record only after a
  real child status. Pre-child failures are not counted.
- Exit code `9` means accounting failed. If the child already completed, the
  code includes `"child_completed": true`; do not auto-retry it.
- Weights apply prospectively. A weight changed later does not rewrite already
  aggregated units.
- Relative weights are not provider prices. Do not present them as cost.

## Classification

**The classifier is not the front door.** `route` and `run` require exactly one
of `--tier` or `--suggest-tier`, and neither ever infers a tier from an absent
flag. A suggested tier may not start a vendor without `--review`, so a refuted
judgement never launches a child nobody looked at; automation passes `--tier`.

`--suggest-tier` carries `CLASSIFIER_MEASURED_AGREEMENT` in the receipt and in
the console review. That constant is the classifier's own blind-evaluation
record, and it must state **both** routing directions — publishing over-routing
while omitting that eight of nine majority-`high` prompts went to `standard`
would hide the stronger warning. An explicit tier adds no receipt field, which
keeps the frozen schema-1 route bytes unchanged.

Do not make either flag optional again, and do not add a default. The vendor is
already never inferred from the task: it comes from `--source-vendor`,
`--preset`, or the policy file, whose first tier route pins it.

`classification.py` holds the policy. Before changing it, read the measured
results in `docs/paired-token-study.md` and
`docs/policy4-fresh-blind-evaluation.md`.

- Do not tune the classifier against the visible public fixture or any corpus
  whose ratings have already been spent.
- A new policy candidate requires a new independently generated, rated, and
  sealed corpus.
- Do not narrow a signal on a two-sample result. Routing a data-destroying
  migration to `high` is a defensible posture independent of whether those two
  cases passed a tier down.
