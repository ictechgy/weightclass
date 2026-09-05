# weightclass

> **Archived.** Development stopped on 2026-09-05 after release **0.32.0**, which is
> the final release. The repository is kept read-only as a research record: a
> pre-registered study found no benefit from effort-tier routing, and a blind
> injected-defect study found that the verify gate a cheap-first strategy depends
> on catches about a quarter to three quarters of defects depending on how much
> the verifier's author already knew. See
> [Measured results for tier routing](#measured-results-for-tier-routing) and
> [`docs/verifier-recall-result.md`](docs/verifier-recall-result.md). Installed
> copies keep working; `0.31.1` is the last release with the advisory companion.

**weightclass** starts one agent CLI process — Codex, Claude Code, Antigravity
(`agy`), or Grok — with the exact command you reviewed. You choose the tier,
`wclass` prints the task-free argv and a route fingerprint, and on a terminal
it asks before starting anything. Any other vendor is reachable by writing its
exact command in a local policy.

It is a local, policy-driven router and nothing more: it reads no credentials,
makes no network requests, and persists no task content. It is not a
subscription-savings tool. The heuristic classifier that can suggest a tier is
experimental and opt-in; its measured record is in
[Why the classifier is opt-in](#why-the-classifier-is-opt-in).

## Install

weightclass has no runtime dependencies beyond Python 3.10 or later.

```sh
uv tool install weightclass      # or: pipx install weightclass
brew install ictechgy/tap/weightclass
```

Or from a local checkout:

```sh
git clone https://github.com/ictechgy/weightclass.git
cd weightclass
python3 -m pip install .
```

All three install the core `wclass` command. weightclass bundles no vendor CLI
itself: whichever built-in route you use — `codex`, `claude`, `agy`, or
`grok` — that vendor's own CLI must already be installed and authenticated on
the machine that runs it. weightclass never reads or changes their
authentication or subscription state.

## Quick start

```sh
printf '%s' 'Fix a spelling typo.' | wclass classify
printf '%s' 'Fix a spelling typo.' | wclass route --source-vendor codex --tier low
printf '%s' 'Fix a spelling typo.' | wclass run --source-vendor codex --tier low --review

# Or let the classifier suggest, and read its record before confirming.
printf '%s' 'Fix a spelling typo.' | wclass route --source-vendor codex --suggest-tier
```

The first command classifies locally; the second reviews the selected command.
The third keeps review and execution in one process: it shows the task-free
route on the controlling terminal, asks for confirmation, and then starts at
most one vendor child. No copied fingerprint is needed. When stdout is a
terminal, `run` reviews by default, so `--review` is only spelling out what
already happens; pass `--no-review`, `--json`, or `--ack-route-fingerprint` to
take the non-interactive path, which is also what a pipe or a script gets
without asking. A pty-backed harness (`script`, `expect`, a tmux pane driven
by a script) looks like a terminal, so give it one of those flags rather than
letting it wait on a prompt nobody will answer.
`route` requires the selected vendor executable to be installed and to pass the
local admission checks described below; otherwise it exits `3` without starting
the vendor.
Releases are cut by pushing a tag; see [RELEASING.md](RELEASING.md). See
[Native integrations](docs/integrations.md) for reviewed Codex and Claude Code
examples. Current security status, including the still-open path-based spawn
boundary, is documented in [the security follow-up](docs/security-performance-followup.md).

## What you get, and what you do not

You get:

- A deterministic route: the same policy, vendor, and tier always select the
  same command, and `wclass route` shows it before anything runs.
- Review before execution. On a terminal `run` asks first. In a script, passing
  the fingerprint from `route` binds a policy run to the command you reviewed;
  a built-in route can still start unacknowledged there, and the
  [security boundary](#security-boundary-and-non-goals) says exactly when.
- Exactly one foreground vendor child, started without a shell, inheriting your
  terminal. `wclass` does not capture, retry, or supervise it.
- No credential reads, no network requests, and no persisted task content. The
  vendor CLI owns authentication, billing, and network.
- Opt-in, aggregate-only usage accounting with weights you declare yourself.

You do not get:

- Pricing, quota, or remaining-subscription knowledge. Model and effort labels
  are opaque configuration; weightclass never infers what they cost.
- A measured saving. Tier routing showed no benefit on the work that was
  measured; see [Why the classifier is opt-in](#why-the-classifier-is-opt-in)
  and [Measured results for tier routing](#measured-results-for-tier-routing).
- Automatic retries, escalation, or rework accounting. When a child fails,
  `wclass` reports its status and stops; choosing another tier is your
  decision, and the usage store counts rework or escalation only when you
  declare it with `--usage-rework` or `--usage-escalation`.
- Quality verification or filesystem confinement. `wclass` never reads or
  judges the child's output, and nothing here confines the child to a
  directory.

## Why the classifier is opt-in

This is effort routing, not a token-saving claim. weightclass does not read
provider usage data, infer pricing, or know whether an effort label reduces
the total tokens needed to finish a task. Retries and rework happen outside the
router and can outweigh a cheaper first attempt.

The heuristic classifier is experimental, and it is **not** the front door.
`route` and `run` require exactly one of `--tier` (you choose) or
`--suggest-tier` (the classifier suggests, and reports its own record next to
the suggestion). A suggested tier cannot start a vendor without a terminal
review (the default on a terminal, or `--review`), so the classifier never
launches a child nobody looked at.

Against 24 blind-rated prompts, policy 4 measured:

| Metric | Result | 95% CI |
| --- | ---: | ---: |
| Agreement | 10/24 (41.7%) | 24.5%-61.2% |
| High-tier recall | 1/9 (11.1%) | 2.0%-43.5% |
| Over-routing | 6/24 (25.0%) | 12.0%-44.9% |

The stronger warning is the one the headline metrics hide: eight of the nine
majority-`high` prompts were routed to `standard`. The sample and its intervals
are too small to be an accuracy estimate, and a separate pre-registered study
found no benefit from routing up on the work it measured. See
[the fresh blind check](docs/policy4-fresh-blind-evaluation.md) and
[the paired token study](docs/paired-token-study.md).

## Measured results for tier routing

The absence of a saving is now a measured statement, not only a cautious one. A
pre-registered study ([`docs/paired-token-study.md`](docs/paired-token-study.md)) built a
synthetic fixture, 36 blind-rated tasks, and a paired harness, then ran a pilot
and a difficulty calibration against real vendors. **Wherever it observed the
same task at two tiers, the tier never changed whether the task got done.** In
the pilot a pinned mid effort came out ahead of routing on both vendors by point
estimate — 5.5% on Codex, 5.1% on Claude — though only on Claude did the fixed
arm's interval exclude zero; Codex's result is a wide null. In calibration, 0 of
18 candidates were tier-sensitive, and the two tasks routed to `high` also passed
one tier down. Only five of the eighteen were run at two tiers at all — two that
passed at both and three that failed at both — while the rest passed at the
routed tier and stopped, so no comparison exists for them. Of those five, two of
the three fail-at-both cases turned out to be defects in the study's own
acceptance tests, which rejected correct work for choosing a different interface.
The pilot adds two more clean two-effort observations — the tasks it routed to
`high` also ran at `medium` as the fixed arm, and completed at both on both
vendors. So the headline rests on a handful of observations, not on the full 36.

On work of that shape — small, well-specified maintenance tasks — effort moved
cost and nothing else, so routing up had no quality risk to justify its price.
The study stopped there because it had pre-registered the condition: a floor of
nine tier-sensitive tasks, and a written instruction to report a shortfall as
the finding rather than lower the bar. That floor is stated over all 36 tasks
while 18 were calibrated; the doc does not claim the untested tail is proven
empty, only that finding nine tier-sensitive tasks in a mostly `low`-rated
remainder, after none surfaced among the hardest eighteen, was not worth another
calibration round to rule out.

Read that as a bound on the evidence, not a proof about all work: one fixture,
one vendor (Codex) for calibration, and small, fully specified maintenance tasks.
The study itself was lopsided: calibration recovers by escalating *upward*, so
what it ruled out is routing up paying for itself.

The cheap direction was measured separately afterwards, and it is the more
useful result. The 15 tasks that had passed at their routed tier — 13 routed
`standard`, 2 routed `high`, so every one of them above `low` — were re-run
pinned at `low`. **All 15 passed.** None hit a critical failure either, meaning
none deleted a source file, wrote a secret, or left the fixture's own test suite
red. One task the router had sent to `high` for 77,170 tokens passed at `low` on
31,727. For all 15 of these, then, the tier the classifier picked was higher
than the acceptance test required — which is the strongest form the claim can
take, since that test is the only definition of "enough" this measurement has.

Token savings point the same way without being established. Across the same 15
tasks `low` used 506,529 tokens against the routed tiers' 632,983, or 20.0%
fewer. That aggregate is dominated by the largest tasks, though; the number
that generalizes is the per-task mean saving of 14.1%, whose **95% interval,
[−1.0%, +29.1%], includes zero.** Four of the 15 cost *more* at `low`.

Do not read that as "route everything cheaply." Those 15 were selected for
having already passed, and a pass/fail acceptance test cannot detect work that
meets the contract while being worse. What it does show is where the slack is:
in how the tier is chosen, not in the routing mechanism.

It does not show effort never matters. It does show that nobody, including this
router, should assert a saving without measuring it on their own workload —
which is why every savings surface here abstains by default.

Two proposals for what to do with the one lever that did survive — model grade,
−69.02% cost among the 88 runs without a critical failure, but rejected for two
mechanically detectable critical failures in 90 — are written up but **not implemented**:
[`docs/archive/speculative-cheap-route-design.md`](docs/archive/speculative-cheap-route-design.md)
runs the cheap route and escalates when a verify command fails, and
[`docs/archive/advisor-arm-design.md`](docs/archive/advisor-arm-design.md) measures
Anthropic's Advisor tool as a rival mechanism that buys expensive *guidance*
instead of expensive *output*. Both require a number nobody has yet:
[`docs/archive/measuring-p-at-work.md`](docs/archive/measuring-p-at-work.md) is how to
get it. The sealed, task-free campaign contract those proposals would have
collected evidence under shipped with the separate companion that was removed in
0.32.0; its design notes are archived under
[`docs/archive/`](docs/archive/README.md).

Raw tokens and estimated provider cost must be evaluated separately. The
offline evaluation tools can score externally normalized aggregate evidence,
but they never fetch prices or claim to reproduce a subscription bill.
An optional local usage store can count completed schema-3 runs and compare
user-supplied relative cost weights without reading provider usage or prices.

By default, a request stays with its explicit source vendor. Cross-vendor
routing is available only through a reviewed policy opt-in. weightclass never
reads credentials and never makes a provider network request itself.

## Commands and exit codes

`wclass --help` lists the daily commands in this order: `discover`, `usage`,
`classify`, `example-policy`, `review-preset`, `route`, and `run`. One epilog
line names the two advanced policy generators, `profile` and `select`; both
still parse, run, and answer their own `--help`, they are only absent from the
listing. `classify`, `route`, and `run` read the task from standard input; every other
command is task-free, and only `run` ever starts a vendor. `select` reads
choices and confirmations from the controlling terminal and writes only the
confirmed canonical policy to standard output.

`route` and `run` require exactly one of `--tier` and `--suggest-tier`; neither
infers a tier from an absent flag, and the refusal happens before the task is
read. A schema-3 policy additionally requires an explicit `--tier`; the
classifier suggestion is not accepted there. Every malformed invocation —
unknown subcommand, missing argument, bad policy — exits `2` with
`{"error": "invalid_input"}` on standard error and no command list; `usage`
failures add a `reason_code`, and when stdout is a terminal the same error is
rendered as one human line plus a `Next:` hint. Flag names are never abbreviated:
`--confirm-endpoint-transition` cannot be shortened.

Exit codes are weightclass's own; a selected command's status never overwrites
them:

| Code | Meaning |
| --- | --- |
| `0` | Success. For `run`, the selected command exited `0`. |
| `1` | `execution_cancelled` — you declined at the terminal review prompt, or `select` was cancelled or reached terminal EOF before policy emission. Anything else exiting `1` is an unhandled interpreter exception and a bug worth reporting. |
| `2` | `invalid_task` or `invalid_input`. |
| `3` | `unsupported_route` — no policy route matched, or a built-in/bound custom route's executable is missing or rejected during review. |
| `4` | `executor_unavailable` — the command could not be started or a bound custom executable could not be admitted for a run. |
| `5` | `confirmation_required` — a required endpoint-transition confirmation is absent. |
| `6` | `route_fingerprint_mismatch` — the reviewed route changed. |
| `7` | `executor_failed` — the command started and exited non-zero. |
| `9` | `usage_unavailable` — enabled schema-3 accounting could not be validated or updated. |

Code `7` carries the real status as
`{"error": "executor_failed", "executor_exit_code": N}`, or `"executor_signal":
N` for a signal. The child inherits standard error, so that diagnostic is the
**last line** of the stream — parse that line, not the whole stream. A vendor
CLI that reports success while declining to do the work still exits `0`;
weightclass cannot detect that and does not claim to.

## Policy schemas

Three schemas are accepted, and every profile, account, recipient, billing,
subscription, entitlement, model, effort, permission, and ownership label in
all three is an opaque caller declaration: weightclass validates shape and
explicit grants, never the truth of a label.

- **Schema 1** — the built-in routes and simple custom routes: a list of
  `{id, vendor, tier, command}` objects matched in declaration order by the
  first route whose `tier` matches. `--policy`, `--preset`, and the policy
  `wclass profile` generates all use it.
- **Schema 2** — a native policy adding explicit source/account profiles,
  closed model-and-effort builders, directional profile/vendor authorization,
  and fingerprint-bound review. Select a profile with `--source-profile`.
- **Schema 3** — adds observation-bound review, endpoint-transition
  confirmation, and the opt-in aggregate usage store on top of that.

A native `run` needs the exact `route_fingerprint` from the reviewed route as
`--ack-route-fingerprint`, and a missing acknowledgement stops before task
access. A route listing `endpoint_transition` needs
`--confirm-endpoint-transition` and otherwise exits `5`; one still asking for
the `native_delegation` confirmation exits `3`, because the nested surface that
offered that consent was removed in 0.32.0 and the route is refused rather than
run without the consent it names. See
[Native schema 3](docs/native-schema-3.md) and
[Native integrations](docs/integrations.md).

## The built-in routes

Every built-in route covers `low`, `standard`, and `high` and is intentionally
conservative. Codex uses an ephemeral `exec` session in a workspace-write
sandbox with `model_reasoning_effort` passed as a `-c` override for that one
invocation, because Codex has no effort flag. Claude uses print mode with no
session persistence, the matching `--effort`, and permissions `acceptEdits`:
print mode is non-interactive, so a permission mode that asks a human has
nobody to ask and every edit would be refused while `claude` still exits `0`.
`acceptEdits` auto-accepts file edits only — unlike Codex's `workspace-write`
it runs no commands. `agy` uses `--print` with `--mode accept-edits` for the
same reason, and `grok` uses `-p` with `--reasoning-effort` and
`--permission-mode acceptEdits`, leaving `--sandbox` at its own default because
that vocabulary was never enumerated in `--help`. `agy` and `grok` take their
prompt only in argv, so those routes declare `{{task}}` and receive empty
standard input instead. No built-in route pins a model.

`--source-vendor` is required when weightclass is called from an agent
integration: each of `codex`, `claude`, `agy`, and `grok` then selects only
that vendor's routes, and weightclass never tries to infer its parent
application. When it is omitted, every tier is still pinned to one vendor — the
vendor of the first route declared in the policy. A tier is never silently
served by a second vendor; that requires `"allow_mixed_vendors": true`. The
`vendor` field is always present in `wclass route` output.

## Discover installed agents and generate a policy

`discover` checks only for the four package-supported executable names in
absolute directories from the current `PATH`; `profile` turns an agent, model,
effort, and tier selection into a complete schema-1 policy, so you do not have
to assemble vendor argv by hand. [Native integrations](docs/integrations.md)
documents both in full.

```sh
wclass discover --agent grok
wclass profile --agent codex --tier low --model default --effort low \
  > worker-policy.json
printf '%s' 'Fix a spelling typo.' | \
  wclass route --policy worker-policy.json --source-vendor codex --tier low
```

Discovery means executable presence only, and neither command executes the
selected agent. `executable_detected` means a regular executable file passed
local admission checks; subscription, pricing, and quota stay `unknown`, and
the model catalog holds only `default` with `availability_verified: false`. A
package-managed final-component symlink is resolved, and discovery emits — and
policy generation binds — its canonical regular-file target rather than the
mutable link name. `network_used: false` means weightclass opens no network
client and `network_probe_performed: false` states the narrower discovery
guarantee; neither claims that a caller-supplied remote or automounted `PATH`
entry performs no external I/O of its own.

A generated policy holds the detected absolute executable path and exactly one
tier route, and nothing is written unless you redirect the output. `agy`
accepts only `--model default`. `--allow-cross-vendor` emits the schema-1
`allow_mixed_vendors: true` opt-in, deliberately not a directional grant, so
use such a policy only at the reviewed integration boundary; generated `agy`
and Grok policies keep `task_delivery: argv` and its process-inspection
exposure.

Built-in route review resolves `PATH` to one admitted absolute executable. A
reviewed custom policy keeps its existing compatibility behavior unless you
pass `--bind-executable-identity` to both `route` and `run`, which resolves the
final symlink, records the admitted executable observation in the review, binds
it into the fingerprint, and reobserves it before the child starts, so a
replacement or mismatch fails closed. That is how to use an agent weightclass
ships no built-in for: write an exact reviewed schema-1 policy whose `command`
begins with the absolute resolved executable, then bind it. It is incremental
observation hardening, not verified-object execution — the reviewed target is
still started by path, so an attacker able to replace the executable after the
final observation can still affect path-based spawn resolution.

## Classification

Classification is local, deterministic, and offline. Security, authentication,
authorization, data, migration, concurrency, performance, production, and
architecture signals route to `high`, as do narrowly defined high-impact
outcomes such as duplicate charges, duplicate work, and balances becoming
negative — each requiring its full context, so a request merely to display a
negative balance is not escalated. An explicit root-cause investigation reaches
`high.uncertain_diagnostic` only when the task also describes an intermittent
or nondeterministic symptom. Short typo, spelling, formatting, and rename tasks
route to `low`; other valid tasks route to `standard`. Unknown or oversized
input fails closed. Signals match on whole words, so `reproduction` does not
count as `production`, but Korean has no word boundaries and its signals match
by containment. `high` beats `low` when both are present, and length never
raises a tier: 1,200 characters or more only costs eligibility for `low` and
reports `standard.length_floor`.

```sh
printf '%s' 'Fix a spelling typo.' | wclass classify --explain
# {"tier": "low", "reason_code": "low.mechanical", "policy_version": "4"}
```

`--explain` adds policy metadata only: never task text, task hashes, matched
fragments, credentials, or provider output, and a reason-only change never
enters a route fingerprint. The same flag on `route` adds the reason code,
policy version, and a coarse `confidence_class` to the reviewed receipt without
adding task text or changing the fingerprint. Native schema-2/3 descriptors are
explicit-selector contracts and do not use it.

**Keyword matching has a measured ceiling.** Before explicit high-impact
outcome patterns were added, the local classifier agreed with 15 of 40 tasks on
a benchmark rated independently by three raters (unanimous on 39 of 40); a
rerun after that refinement yields 17 of 40. The corpus is now public, so
neither figure is valid evidence of general accuracy for later changes, and the
remaining failures are not vocabulary gaps more words would close: people
describe hard problems in ordinary language with no technical term to match.
`PYTHONPATH=src python3 tests/eval/score.py` re-derives the public regression
result, 21/40 under classification policy 4, with 22.5% over-routing and 5/15
high-tier recall — a direction check, not an accuracy claim, because a visible
fixture measures the tuner as much as the classifier. Build and blind-rate a
fresh corpus before making a new accuracy claim; any future semantic classifier
stays an opt-in experiment until it beats the pre-registered baseline on one.
See [`tests/eval/README.md`](tests/eval/README.md).

`route` and `run` never contact a vendor to choose a tier: `--tier` takes the
tier you supply and `--suggest-tier` uses the same local, offline classifier.
`--tier` skips classification but not validation — empty and oversized input
still fail closed — so a tier you already obtained can simply be passed on:

```sh
tier="$(printf '%s' "$task" | wclass classify \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')" || exit
printf '%s' "$task" | wclass run --source-vendor claude --tier "$tier"
```

The `|| exit` matters: a rejected task exits `2` and prints nothing on standard
output, and without it the pipeline would continue with an empty tier.

## Override the routes

Review a local policy with `wclass route --policy policy.json`, then run what
that review selected with
`wclass run --policy policy.json --tier <tier> --ack-route-fingerprint <fp>`.
Routes are considered in listed order, so the first matching `tier` wins. Add
`--source-vendor <vendor>` matching the route's vendor label.

```json
{
  "allow_mixed_vendors": false,
  "posture": "balanced",
  "routes": [
    { "id": "codex-low", "vendor": "codex", "tier": "low",
      "command": ["codex", "exec", "--model", "your-low-model-label", "-"] },
    { "id": "claude-high", "vendor": "claude", "tier": "high",
      "command": ["claude", "--print", "--model", "your-high-model", "--effort", "high"] }
  ]
}
```

`posture` is optional and defaults to `balanced`. An explicitly reviewed
`"posture": "cautious"` raises only an otherwise `standard` decision to `high`;
it does not change `low` or already-`high` decisions, override `--tier`, switch
vendors, inspect model labels, or infer subscription availability, and
`wclass route` then renders both `posture` and a static `reason_code`. Any
other value or shape fails closed with the redacted `invalid_input` diagnostic.
Because `cautious` can select a higher effort route it can raise token use; it
is a safety preference, not an efficiency setting.

A policy must be a regular file no larger than 262,144 raw bytes. Parsing is
strict UTF-8, duplicate object keys are rejected at every nesting depth, special
files such as FIFOs fail promptly, and the file is validated before weightclass
reads transient task input. A symlink is accepted only when the object opened for that invocation is
a regular file, which does not make a path stable between a separate review and
run. The `command` tokens are opaque: weightclass validates their shape but
asserts nothing about vendor CLI semantics or subscription access.

A route's `vendor` is a containment label you choose, not a list of tools
weightclass knows: any printable identifier without whitespace, up to 64 bytes,
matched and fingerprinted as a string. So an agent weightclass ships no
built-in command for is still usable by whoever has it installed —
`{"id": "qwen-low", "vendor": "qwen", "tier": "low", "command": ["qwen", "-p",
"{{task}}"]}`. Because the label is open, `--source-vendor` can no longer
reject a typo: `--source-vendor codx` is well-formed, matches no route, and
exits `3`, while a malformed label — empty, whitespace-bearing, over 64 bytes,
or non-printable — still exits `2`.

A command may contain the reserved token `{{task}}` once, as a whole argument,
and never first: `argv[0]` is the fixed reviewed executable, never task data.
That route receives the task at that argv position and empty standard input
instead, for agents that read a prompt only from their command line — `agy
--print ""` and `grok -p ""` both refuse an empty prompt and never read the
pipe. `wclass route` prints the command with `{{task}}` still in it and adds
`"task_delivery": "argv"`, so a review never contains task text and the
fingerprint does not change from task to task. **Command lines are readable by
every user on the machine**, so such a route exposes the task to anyone who can
run `ps` for as long as the child runs, and nothing weightclass can do removes
that. A token may contain ASCII spaces but not a character a reviewer would not
see: every Unicode `C` category is rejected, as is any other whitespace and any
leading or trailing whitespace. The same rule applies to a schema-2 or
schema-3 policy's `model` and `effort` labels, which additionally cannot begin
with `-` because they occupy reviewed option-value positions.

### Bind a model to a tier

Model grade is usually the larger of the two levers, so tier-specific labels
attach to the built-in routes directly, without materializing a preset policy:

```sh
printf '%s' "$task" | wclass route --source-vendor codex --tier low \
  --low-model your-reviewed-cheap-model \
  --high-model your-reviewed-capable-model
```

`--source-vendor` is required: without it, which vendor's routes receive the
label would depend on declaration order. Only the named tiers change. The label
is opaque — weightclass never checks that the model exists, is available to the
account, or costs less — and because the command changes, the fingerprint
changes with it, so a `run` acknowledged with the unlabelled fingerprint
refuses to start. An unsupported combination fails closed rather than being
silently dropped: `agy` has no model flag and the `grok` effort override is not
measured. `configuration_status` reports `unqualified_custom` for any bound
label.

### Packaged presets

The packaged presets are named `<vendor>-model-override` because that is what
they do: bind opaque model and effort labels onto the built-in route shapes.
They used to be called `<vendor>-cost-focused`, a name that claimed a saving
nothing here has measured. The old names are still accepted everywhere a preset
is selected, and the policy files, command bytes, and fingerprints are
unchanged; only the `preset` field in `review-preset` receipts changed, and it
now carries the current name.

```sh
wclass example-policy claude-model-override > policy.json
wclass review-preset codex-model-override --low-model your-codex-low-model

printf '%s' 'Add a focused unit test.' |
  wclass route --preset codex-model-override --tier standard \
    --standard-model your-reviewed-codex-model
```

`example-policy` materializes a preset as a policy file; `review-preset` prints
every route in it task-free — exact command, fingerprint, tier, vendor, and
`stdin`/`argv` delivery boundary — and reads no task. It labels the unchanged
Claude preset `measured_low_route_only` and the others
`unqualified_experiment`: only the Claude low route ever passed a predeclared
estimated-cost gate, and it used *more* raw tokens while reporting a lower
estimated provider cost, which is a model-price difference rather than a token
saving. The other three pin no model and keep every tier aligned with the
corresponding built-in, so an unmodified one is a reviewable scaffold whose
commands are identical to the routes it would replace. All four keep
`allow_mixed_vendors` false. The baseline, the evaluated candidate, and the
token and estimated-cost gates are
[`tests/eval/claude_cost_baseline_policy.json`](tests/eval/claude_cost_baseline_policy.json),
[`src/weightclass/examples/claude_cost_focused_policy.json`](src/weightclass/examples/claude_cost_focused_policy.json),
and [`tests/eval/README.md`](tests/eval/README.md).

`--preset` selects a packaged policy in memory, carries its source vendor in
the reviewed name, and cannot be combined with `--source-vendor`,
`--cost-focused`, `--policy`, or `--source-profile`; the older
`--cost-focused --source-vendor <vendor>` form remains supported, and invalid
combinations fail before task input is read. Claude and Codex presets accept
per-tier model and effort labels (`--low-model`, `--low-effort`, and the
`standard`/`high` equivalents); Grok accepts model labels but rejects effort
overrides, `agy` rejects all tier overrides, and the older Codex `--model`
shorthand applies one label to low and standard together and cannot be combined
with a tier-specific flag. Each label must be one printable, non-whitespace,
non-option argv token of at most 240 UTF-8 bytes, and weightclass infers no
availability, effort vocabulary, subscription access, quality, or price from
it. Any override is `unqualified_custom` and changes the reviewed fingerprint.
Nothing is persisted; dropping the selector restores the built-in routes.

## Local aggregate usage accounting

Accounting is disabled until you create a private local store:

```sh
wclass usage enable
wclass usage weight --agent grok --effort medium --relative-cost 1.0
wclass usage weight --agent grok --effort low --relative-cost 0.25
wclass usage report
```

On macOS the default store is
`~/Library/Application Support/weightclass/usage-v1.json`; elsewhere it is
under `$XDG_STATE_HOME/weightclass`, or `~/.local/state` when `XDG_STATE_HOME`
is absent or relative. `--store /absolute/path` selects a different private
store for any `usage` command, and schema-3 `run` accepts the same path as
`--usage-store`. Once the default store exists, installed schema-3 executions
record automatically after the selected child has completed; attempts that fail
before a child status is obtained are not counted.

**The store is aggregate-only.** It holds cumulative agent/model/effort/tier
buckets, success/failure and exit-status counts, optional self-reported
rework/escalation counts, and one cumulative baseline total — no task content
or hash, per-run event, timestamp, policy/profile/account, executable path, or
route fingerprint. The store and its lock are regular files private to you,
updates are locked and atomic, and an unsafe or malformed enabled store fails
closed before task access.

Relative cost is a caller assertion: `0.25` means one run of that
agent/model/effort counts as one quarter of one unit. Unconfigured buckets stay
`unweighted`; weightclass never fills them from a price list and claims no
monetary, token, subscription, or quota saving. All configured weights in one
store must use the same caller-defined relative unit, and the report states
both that requirement and that weightclass cannot verify it. Omitting `--model`
configures the native default, while `--model default` configures an opaque
model literally named `default`. **Weights apply prospectively:** configure
them before the runs being compared, because changing a weight later does not
rewrite already aggregated units.

Savings are measured against a counterfactual — the same tasks on the fixed
`medium` route, which is the built-in standard route and pins no model — so the
baseline weight is looked up without a model even when the run used a model
override. Running the baseline route itself reports `0.000000`, not a saving,
and a retry costs extra without enlarging the baseline. `savings_reason_code`
explains every abstention: the report declines a ratio when there are no tasks
(`no_tasks`), when any run has no configured weight (`unweighted_runs`), or
when any task has no `medium` baseline weight (`missing_baseline_weight`).
Partial evidence always flatters the router, so it is refused rather than
shown.

**Rework and escalation are self-declared.** Pass `--usage-rework` when
re-running work that was already counted and `--usage-escalation` on the run
that should increment that counter; weightclass cannot infer either without
storing task identity. After a failed run against an enabled store it prints
`{"usage_hint": "record_retry_with_usage_rework"}` to standard error as a
reminder, because omitting it on a retry inflates both the run count and the
baseline — exactly how a failed cheap route comes to look like a saving.

`usage enable` and `usage report` also emit task-free `onboarding` guidance
whose closed `next_action` names the next step, and a task-free `capacity`
object for the 4,096-bucket and 256 KiB limits; `status: near_limit` begins at
90% of either bound and never prunes, merges, or rewrites evidence. A store
from an earlier build is promoted on read and abstains from savings until new
evidence accumulates. Code `9` before execution emits
`{"error": "usage_unavailable"}` and starts no child; if the child completed
but the atomic update failed it additionally emits `"child_completed": true`
and a bounded numeric `child_returncode`, and callers must not automatically
retry that task. Store JSON uses the same duplicate-key rejection as policy
inputs, and bounded integer-conversion or recursion failures are normalized to
this value-free diagnostic rather than escaping as a traceback.

## Bind a run to the selection you reviewed

`wclass route` prints a `route_fingerprint` over the selected route id, vendor,
command, tier, the policy's `allow_mixed_vendors` setting, and an explicitly
declared posture. `wclass run --policy` requires it whenever the run is not
reviewed on a terminal — a scripted policy run without one exits `6` before
the task is read. Pass it back to bind the run to that selection:

```sh
task='Review this authorization change.'
reviewed="$(printf '%s' "$task" | wclass route --policy policy.json --suggest-tier)"
tier="$(printf '%s' "$reviewed" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')"
fingerprint="$(printf '%s' "$reviewed" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["route_fingerprint"])')"
printf '%s' "$task" | wclass run --policy policy.json --tier "$tier" \
  --ack-route-fingerprint "$fingerprint"
```

If the policy, the selected route, or the task's tier changed since the review,
the run stops with exit `6` and `{"error": "route_fingerprint_mismatch"}` rather
than executing an unreviewed command.

Two limits are worth stating plainly:

- **The task is not bound, only its tier.** A fingerprint reviewed for one
  `low` task will run any other `low` task that selects the same route. Binding
  the task would mean retaining a hash of it, and weightclass does not hash task
  content.
- **The argv is bound, not the program.** If the command names a path whose
  contents are replaced between review and run, the fingerprint still matches.
  It binds the policy's selection, not the identity of the executable. Use
  `--bind-executable-identity` when you want the reviewed executable's `lstat`
  identity bound too.

A route has no separate `model` field, and a policy that declares one is
rejected. Only `command` is ever executed, and weightclass cannot verify that a
label matches the model a command actually selects without asserting vendor CLI
semantics it deliberately does not assert. A label it cannot verify would let a
reviewed descriptor advertise one model while another runs, so the model is
declared once, inside `command`, where `wclass route` prints it in full.

Set `"allow_mixed_vendors": true` only when you intentionally want a Codex
request to select a Claude route, or the reverse. When it is `false` or absent,
the vendor filter is applied before tier selection — including when
`--source-vendor` is omitted, in which case the vendor of the first declared
tier route is used.

## Security boundary and non-goals

- Core `wclass` has no persistence: it writes no router artifacts or vendor
  configuration.
- Task text is read only from standard input, held in memory to classify and
  pass to the selected child process, then discarded. `route` never reads it;
  `run` reads it only after its static execution gates.
  weightclass never logs, stores, echoes, hashes, or places it in diagnostics.
- Core `wclass` never reads credentials, subscription balances, pricing, cookies,
  or vendor configuration. It does not capture or process vendor output, and it
  issues no provider HTTP request of its own.
- Every execution path requires the main thread and a native `SIGCHLD`
  disposition that preserves its direct child's exit status before consuming
  task input. An unsafe context fails closed as `executor_unavailable`; a
  concurrent native change after the check remains a documented residual.
- Schema-3 native route descriptors bind an `lstat` observation
  and recheck it immediately before spawn. That narrows but cannot eliminate
  executable replacement after the final check because execution is still by
  path. Admission rejects other-writable executable files, group-writable
  files not owned by root or the current user, and
  non-sticky world-writable containing directories in both the lexical and
  resolved target chains. Root/current-user-owned group-writable files, sticky
  directories, and user-owned group-writable ancestors remain compatible. This
  is incremental admission hardening, not verified-object execution.
- Route selection is deterministic. Unsupported, malformed, or unsafe input
  fails closed with a redacted JSON diagnostic.
- weightclass does not infer source vendor, model availability, subscription
  tier, or remaining usage. Supply the source vendor explicitly and put model
  arguments in a reviewed policy's `command` when model routing is required.
- `wclass run` starts exactly one configured command in the foreground without
  a shell, retry, backgrounding, recovery, or process supervision.
- weightclass is not an API proxy, credential manager, cloud service,
  subscription checker, bundled provider runtime, or unattended multi-agent
  supervisor.
- Policies must be reviewed before use. Do not place secrets in a policy.
- Every policy file you pass on the command line must be owned by you or by
  root, and must not be world-writable. Either violation is rejected with
  `{"error": "invalid_input"}` before the file is parsed, because whoever can
  rewrite it can choose both the argv and the vendor boundary between the
  `route` you reviewed and the `run` you start. `chmod o-w <file>` if you hit
  this. The check reads the already-opened file, so no swap between check and
  read is possible, and it does not apply to package-owned resources.
- Group-writable files are **not** rejected, and that residual is yours to
  manage. Under the user private group convention the group holds only you, so
  group write is harmless; under a shared primary group such as macOS `staff`
  it is equivalent to world-writable. `stat` cannot tell the two apart, and
  rejecting it would fail every file created under the common `umask 002`. If
  your policy lives in a shared group, keep it at `0o644`.
- `wclass run --policy` requires either a terminal review or the fingerprint
  that `wclass route` printed. On a terminal, `run` reviews by default; without
  a terminal, or with `--no-review`, running a policy is always two steps and
  there is no unreviewed shortcut. A missing acknowledgement then exits `6`
  before the task is read. This is the boundary
  that actually closes the gap between review and execution, because the
  fingerprint covers the selected command itself: if the policy changes, the
  fingerprint changes and the run refuses. File permissions cannot close that
  gap — anyone who can write the containing directory can replace the file
  regardless of its mode. See
  [Bind a run to the selection you reviewed](#bind-a-run-to-the-selection-you-reviewed)
  for what the binding does and does not cover.
- Built-in route syntax remains executable without an acknowledgement when no
  terminal is present or `--no-review` is given, but its admitted absolute
  executable comes from the current `PATH`. Pass the fingerprint from `route`
  to `run` when the reviewed absolute path must be binding; without it, `run`
  resolves and admits `PATH` again.
  Treat a policy file the way you treat a shell script.
- weightclass ships built-in commands only for vendors whose CLI invocation was
  measured: `claude`, `codex`, `agy`, and `grok`. It will not guess another
  program's flags. Any other agent is reachable by writing its exact argv in a
  policy, which is also why no CLI has to be installed here for weightclass to
  support it.
- The built-in `agy` and `grok` routes deliver the task on the command line, so
  the `ps` exposure above applies to them. `claude` and `codex` routes deliver
  it on standard input and do not.
- Argv delivery puts the task in a value position among flags, so a task
  beginning with `-` reaches the child's own argument parser. This is not an
  adversarial case — an ordinary task written as a markdown bullet list starts
  with `-` routinely. Measured directly: the built-in `grok` route fails
  closed on such a task with an argument-parser error from `grok` itself
  (`error: a value is required for '--single <PROMPT>' but none was
  supplied`); the built-in `agy` route is unaffected and accepts it. Neither
  `--` nor any other change to the command helps — for `grok` it produces the
  same error, and `agy` does not need it. weightclass does not validate,
  escape, or reject a task for this; it delivers exactly the bytes you gave
  it.
- A selected command receives the task on standard input — or, for a route that
  declares `{{task}}`, at that argv position with empty standard input instead
  — and inherits standard output and error. Whatever it does with the task —
  including writing it somewhere — is outside weightclass's control, and its
  exit status is its own.

## Development verification

weightclass has no runtime dependencies. These development tools are not
required to use it, only to reproduce what CI checks. The distribution gate
accepts only a directory containing exactly one regular, nonsymlink wheel and
one regular, nonsymlink sdist. Each distribution artifact is capped at 72 MiB
before hashing or archive parsing. The gate fingerprints that exact inventory
and checks it again after running the extracted sdist tests. Archives are
rejected before content inspection or extraction when they exceed 4,096
physical members or 64 MiB total declared payload. Wheel members and supported
local PAX records are capped at 256 KiB, ordinary sdist records at 8 MiB, and
archive directory entries must report zero size. The physical tar scan rejects
GNU, global-PAX, sparse, or offset-changing extensions, malformed headers,
missing terminators, and nonzero trailing data before `tarfile` processes them.
The source registry and both distributions are read through bounded no-follow
descriptors, and archive parsers consume private snapshots matching the initial
fingerprints. The classic-ZIP preflight rejects ZIP64, multidisk, encrypted,
data-descriptor, gapped, overlapping, or inconsistent layouts before `ZipFile`;
it also requires exact stored/raw-deflate input consumption, output size, and
CRC for every wheel member.

### Offline/preprovisioned release verification

The following release-candidate commands are offline only after Python 3.13,
`build`, and the project test dependencies have already been provisioned. CI's
separate dependency-install steps are networked. The candidate download remains
an exact wheel, sdist, and `SHA256SUMS`; the isolation verifier still receives a
private directory containing exactly the two manifest-named distributions.

```sh
python3.13 -m build --outdir artifact-download/build-output
python3.13 tests/verify_release_candidate.py \
  --create-manifest-from artifact-download/build-output \
  --artifact-download artifact-download/candidate
python3.13 tests/verify_release_candidate.py \
  --artifact-download artifact-download/candidate \
  --create-staging dist-under-test
python3.10 tests/compare_release_candidates.py \
  --artifact-download artifact-download/candidate
python3.14 tests/compare_release_candidates.py \
  --artifact-download artifact-download/candidate
```

```sh
set -eu
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall -q src

python3 -m pip install ruff mypy build twine
ruff check src tests
ruff format --check src tests
mypy
weightclass_dist_dir=$(mktemp -d "${TMPDIR:-/tmp}/weightclass-dist.XXXXXX")
python3 -m build --outdir "$weightclass_dist_dir"
twine check --strict "$weightclass_dist_dir"/*.whl "$weightclass_dist_dir"/*.tar.gz
python3 tests/verify_distribution_isolation.py \
  --source . --dist-dir "$weightclass_dist_dir" \
  --run-sdist-tests
```

## License

[MIT](LICENSE)
