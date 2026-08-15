# weightclass

**weightclass** is a local, policy-driven router for agent CLI workflows.
Built-in support covers Codex, Claude Code, Antigravity (`agy`), and Grok; any
other vendor is reachable by writing its exact command in a policy. It
classifies a task in memory as `low`, `standard`, or `high`, chooses a
deterministic model-and-effort route, and can start one selected vendor
process in the foreground.

This is effort routing, not a token-saving claim. weightclass does not read
provider usage data, infer pricing, or know whether an effort label reduces
the total tokens needed to finish a task. Retries and rework happen outside the
router and can outweigh a cheaper first attempt.

Raw tokens and estimated provider cost must be evaluated separately. The
offline evaluation tools can score externally normalized aggregate evidence,
but they never fetch prices or claim to reproduce a subscription bill.
An optional local usage store can count completed schema-3 runs and compare
user-supplied relative cost weights without reading provider usage or prices.

By default, a request stays with its explicit source vendor. Cross-vendor
routing is available only through a reviewed policy opt-in. An optional V2
route can start a separately installed API runtime after explicit review and
egress acknowledgement; weightclass never reads API credentials or makes
provider network requests itself.

The `delegate` surface can also compile a Claude- or Codex-native
planner/worker/reviewer policy into one offline review descriptor. P0.5 may
start one explicitly trusted user-supplied orchestration runtime after review;
its manifest remains a declaration, not proof that it enforces delegation.

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

All three install the `wclass` command. weightclass bundles no vendor CLI
itself: whichever built-in route you use — `codex`, `claude`, `agy`, or
`grok` — that vendor's own CLI must already be installed and authenticated on
the machine that runs it. weightclass never reads or changes their
authentication or subscription state.

Releases are cut by pushing a tag; see [RELEASING.md](RELEASING.md).

For reviewable native Codex and Claude Code invocation examples, see
[Native integrations](docs/integrations.md).

Native schema 2 and delegation protocol 2 add explicit source/account profiles,
closed model-and-effort builders, directional profile/vendor authorization, and
fingerprint-bound review before one foreground execution. All profile, account,
recipient, billing, subscription, entitlement, model, effort, permission, and
ownership labels remain opaque caller declarations. See the
[protocol 2 security boundary](docs/protocol-v2-security.md) and
[migration guide](docs/protocol-v2-migration.md).

Native schema 3 adds observation-bound native review and an additive
`wclass delegate native route|run` surface for exactly one bounded subtask and
one foreground child. It is direct native execution, not an orchestration
runtime. See [Native schema 3](docs/native-schema-3.md).

## Run locally

`wclass --help` lists the whole surface:

```text
wclass [-h] [--version] {discover,profile,select,usage,classify,example-policy,review-preset,review-cost-profile,recommend,route,run,render,delegate,v2} ...
```

`classify`, `recommend`, `route`, and `run` read the task from standard input. `discover`,
`profile`, `select`, and `usage` are task-free local commands. `select` reads
numeric choices and confirmations from the controlling terminal and writes only
the confirmed canonical policy to standard output. `render`
prints the command of a policy route named by a workflow descriptor and never
reads a task. `example-policy` emits packaged policy JSON; `review-preset`
prints every command and fingerprint in one packaged policy.
`review-cost-profile` validates and fingerprints task-free cost input. None of
those three review commands reads a task or invokes a vendor. `recommend`
emits evidence-bound advice or an explicit abstention and never invokes a
vendor. `v2` selects a declarative API route; see
[V2 API routing](#v2-api-routing-through-an-external-runtime).
`delegate route` reads only its policy and manifest and does not consume task
standard input or inspect the supplied runtime path. `delegate run` reads the
task only after confirmation, fingerprint, and runtime-availability gates.

Every malformed invocation — an unknown subcommand, a missing argument, a bad
policy — exits `2` with `{"error": "invalid_input"}` on standard error and
nothing else, so a caller can parse the failure without scraping usage text.
Flag names are never abbreviated: `--confirm-api-egress` cannot be shortened.

Exit codes are weightclass's own; a selected command's status never overwrites
them:

| Code | Meaning |
| --- | --- |
| `0` | Success. For `run` and `v2 run`, the selected command exited `0`. |
| `1` | `select` was cancelled or reached terminal EOF before policy emission. |
| `2` | `invalid_task` or `invalid_input`. |
| `3` | `unsupported_route` — no policy route matched. |
| `4` | `executor_unavailable` — the command could not be started. |
| `5` | A required API, runtime, endpoint-transition, or native-delegation confirmation is absent. |
| `6` | `route_fingerprint_mismatch` — the reviewed route changed. |
| `7` | `executor_failed` — the command started and exited non-zero. |
| `8` | `triage_unavailable` — `--ask-vendor` could not obtain a tier. |
| `9` | `usage_unavailable` — enabled schema-3 accounting could not be validated or updated. |

Outside the documented `select` cancellation path, code `1` indicates an
unhandled interpreter exception and is a bug worth reporting.

## Escalating after a failed run

Routing a task to a cheap tier is only sensible if a failure is cheap to
recover. `run --suggest-escalation` makes that recovery a step you can take
rather than one you have to reconstruct:

```sh
# $fingerprint comes from the matching `wclass route --tier low` call.
printf '%s' "$task" | wclass run --source-vendor codex --tier low \
  --ack-route-fingerprint "$fingerprint" --suggest-escalation
# {"error": "executor_failed", "executor_exit_code": 1}
# {"escalation": {"from_tier": "low", "to_tier": "standard", "route": "codex-standard",
#                 "vendor": "codex", "command": [...],
#                 "route_fingerprint": "sha256:...",
#                 "record_as_rework": true, "failure_cause_diagnosed": false}}
```

The fingerprint is the one `wclass route --tier standard` renders, so it can be
passed straight to the next `run` without re-reviewing by hand.

**Nothing is retried, started, or supervised.** V1 runs exactly one foreground
child and this does not change that; the router names a route and exits. Running
it is your decision.

Two fields exist to stop the output being read as more than it is:

- `failure_cause_diagnosed` is always `false`. The router does not read the
  child's output and cannot tell a task that needed more effort from one that was
  impossible, misconfigured, or broken for unrelated reasons. A non-zero exit is
  not evidence that the tier was wrong.
- `record_as_rework` is `true` because the escalated run is a second attempt at a
  task already counted. Pass `--usage-rework` to it. Omitting that inflates both
  the run count and the counterfactual baseline, which is exactly how a failed
  cheap route comes to look like a saving.

Nothing is suggested when the router itself refused — invalid input, an
unsupported route, a fingerprint mismatch, or an executor that never started.
Those failures have nothing to do with the tier, and pointing at a more
expensive route would only spend money on them. `high` reports
`{"escalation": null, "reason": "already_highest_tier"}`.

## Local aggregate usage accounting

Accounting is disabled until the user creates a private local store:

```sh
wclass usage enable
wclass usage weight --agent grok --effort medium --relative-cost 1.0
wclass usage weight --agent grok --effort low --relative-cost 0.25
wclass usage report
```

The `medium` weight is not optional bookkeeping. It states what the same task
would have cost on the fixed route it would have taken without routing, and the
report refuses to compute a saving without it.

On macOS the default store is
`~/Library/Application Support/weightclass/usage-v1.json`. On other supported
systems it is under `$XDG_STATE_HOME/weightclass`, or `~/.local/state` when
`XDG_STATE_HOME` is absent or relative. `--store /absolute/path` selects a
different private store for any `usage` command; schema-3 `run` and
`delegate native run` accept the same path as `--usage-store`.

Once the default store exists, normal installed `wclass` schema-3 executions
record automatically after the selected direct child has completed. Attempts
that fail before a child status is obtained are not counted. The store contains
only cumulative agent/model/effort/tier buckets, success/failure and exit-status
counts, optional self-reported rework/escalation counts, and one cumulative
baseline total. It contains no task content or hash, per-run event, timestamp,
policy/profile/account, executable path, or route fingerprint. The store and lock are regular files private to the
current user, updates are locked and atomic, and an unsafe or malformed enabled
store fails closed before task access.

Relative cost is a caller assertion: `0.25` means one run of that
agent/model/effort counts as one quarter of one unit. Unconfigured buckets
remain `unweighted`; weightclass never fills them from a price list and does not
claim monetary, token, subscription, or quota savings.

Savings are reported against a counterfactual, not against a per-run constant.
The baseline is *the same tasks on the fixed `medium` route* — the built-in
standard route, which pins no model. The baseline weight is therefore looked up
without a model (`--agent <agent> --effort medium`, no `--model`) even when the
run itself used a model override, because the route a task would have taken
without routing is the vendor's own default model. Pricing a model-routed task
against that same routed model would compare it to a counterfactual that never
existed, and would cancel out exactly the saving model routing was meant to
produce. One limitation follows from the aggregate-only contract: the store does
not record a source vendor, so if a reviewed cross-vendor opt-in changed the
agent, the baseline is that *destination* agent's default route, not the
vendor you would otherwise have used.

Given that baseline:

- running the baseline route itself reports `0.000000`, not a saving;
- a retry costs extra without also enlarging the baseline, because a retry is
  not a new task. Ten tasks routed to a cheap effort that fail and are reworked
  on an expensive one report the resulting **overrun**, not a saving;
- `savings_reason_code` explains every abstention. The report declines to
  compute a ratio when there are no tasks (`no_tasks`), when any run has no
  configured weight (`unweighted_runs`), or when any task has no `medium`
  baseline weight (`missing_baseline_weight`). Partial evidence always flatters
  the router, so it is refused rather than shown.

Distinguishing a task from a retry is the caller's declaration: pass
`--usage-rework` when re-running work that was already counted. After a failed
run against an enabled store, `wclass` prints
`{"usage_hint": "record_retry_with_usage_rework"}` to standard error as a
reminder. Omitting it on a retry inflates both the run count and the baseline,
which is exactly how a failed cheap route comes to look like a saving. There is
no per-run identifier to reconstruct this from, by design.

Stores created by an earlier build are promoted on read. Schema 1 recorded no
counterfactual, so a promoted store keeps its counts, recovers its task count as
`runs - reworks`, and abstains from savings until new evidence accumulates.
Omit `--model` to configure the native default; passing `--model default`
configures an opaque model literally named `default`. Weights apply
prospectively, so configure them before the runs being compared;
changing a weight does not rewrite already aggregated units. The report lists
the current configured weights alongside the cumulative metrics.
Use `--usage-rework` or `--usage-escalation` on the schema-3 run that should
increment those counters; weightclass cannot infer either without storing task
identity, so both are explicitly self-reported.

If validation fails before execution, code `9` emits
`{"error": "usage_unavailable"}` and starts no child. If the child completed but
the atomic aggregate update failed, code `9` additionally emits
`"child_completed": true`; callers must not automatically retry that task.

## Discover installed agents and generate a policy

`discover` checks only for the four package-supported executable names in
absolute directories from the current `PATH`. Discovery means executable
presence only; it does not establish a usable profile, authenticated account,
model entitlement, price, or remaining quota. weightclass does not
intentionally start a provider or network request during discovery, but a
caller-supplied `PATH` can name a remote or automounted filesystem whose normal
metadata lookup has external I/O. Discovery does not start a vendor process,
read vendor configuration or authentication files, or read task standard
input:

```sh
wclass discover
wclass discover --agent grok
```

The JSON result distinguishes an executable detected on the local path from a
usable subscription or model. `executable_detected` means only that a regular
executable file was found. A package-managed final-component symlink is resolved,
and discovery emits and later policy generation binds its canonical regular-file
target rather than the mutable link name. Subscription, pricing, and quota remain
`unknown`. The schema-1 `network_used: false` field is retained for compatibility
and means that weightclass opens no network client; `network_probe_performed:
false` states the narrower discovery guarantee. Neither field claims that a
caller-supplied remote filesystem performs no external I/O.
The package-owned effort catalog describes the command shapes weightclass can
build; it is not a probe of the installed CLI version. The model catalog
contains only `default`, meaning that no model override is emitted, and reports
`availability_verified: false`. Cloud model entitlement is not a locally
installed property that weightclass can safely infer.

`profile` turns an agent, model, effort, and tier selection into a complete
schema-1 policy, so the user does not have to assemble vendor argv manually:

```sh
wclass profile \
  --agent codex \
  --tier low \
  --model default \
  --effort low > worker-policy.json
```

Codex, Claude, and Grok accept an opaque `--model` selection through their
closed package builders. `agy` currently accepts only `--model default`
because weightclass has no reviewed model-override shape for it. Every
non-default model label remains caller-supplied opaque configuration;
weightclass does not verify that the account can use it. The generated policy
contains the detected absolute executable path and exactly one tier route.
The command writes nothing unless the caller explicitly redirects its output
to a chosen file.

For an intentional cross-vendor worker, add `--allow-cross-vendor`. This emits
the existing schema-1 `allow_mixed_vendors: true` opt-in; it is deliberately
not a directional grant, so use the generated single-worker policy only at the
reviewed integration boundary:

```sh
wclass profile \
  --agent grok \
  --tier low \
  --model default \
  --effort low \
  --allow-cross-vendor > worker-policy.json

printf '%s' 'Fix a spelling typo.' | \
  wclass route --policy worker-policy.json --source-vendor codex --tier low
```

Review the emitted route and pass its fingerprint to the ordinary `run`
command. Discovery and profile generation never execute the selected agent;
`run` still starts exactly one foreground child with no retry or fallback.
Generated `agy` and Grok policies retain `task_delivery: argv` and its local
process-inspection exposure. Schema 1 binds the lexical executable path in the
route fingerprint but does not provide schema-2 executable reobservation.

Code `7` carries the real status in its diagnostic, as
`{"error": "executor_failed", "executor_exit_code": N}` or, for a command killed
by a signal, `{"error": "executor_failed", "executor_signal": N}`. A selected
command inherits standard error, so this diagnostic is always written on a fresh
line and is the **last line** of standard error — parse that line, not the whole
stream, which also holds whatever the command itself printed.

A vendor CLI that reports success while declining to do the work still exits
`0`; weightclass cannot detect that and does not claim to.

Inspect a route before running it:

```sh
printf '%s' 'Fix a spelling typo in the README.' | wclass route --source-vendor codex
printf '%s' 'Fix a spelling typo in the README.' | wclass run --source-vendor codex
```

For schema-2 `run`, pass the exact `route_fingerprint` from the reviewed route
as `--ack-route-fingerprint`; a missing acknowledgement stops before task
access. Cross-profile and cross-vendor changes must be explicitly and
directionally granted by the reviewed policy. weightclass observes only the
one direct child's exit, never task or orchestration success.

The built-in routes are intentionally conservative:

- Codex: `low`, `standard`, and `high` use an ephemeral `exec` session in a
  workspace-write sandbox with `model_reasoning_effort` set to `low`, `medium`,
  and `high`. Codex has no dedicated effort flag, so the effort is passed as a
  `-c` configuration override for that one invocation.
- Claude: `low`, `standard`, and `high` use print mode, no session persistence,
  and efforts `low`, `medium`, and `high`. Permissions are `acceptEdits`,
  because print mode is non-interactive: a permission mode that asks a human
  has nobody to ask, so every edit is refused while `claude` still exits `0` —
  the router would report success having changed nothing. `acceptEdits`
  auto-accepts file edits only, which lets the Claude route change files as the
  Codex route already could. It does not make the two identical: Codex's
  `workspace-write` also runs commands, while under `acceptEdits` a non-edit
  tool still goes to a prompt that print mode cannot answer.
- `agy`: `low`, `standard`, and `high` use `--print` with efforts `low`,
  `medium`, and `high`, and `--mode accept-edits` for the same non-interactive
  reason as Claude's `acceptEdits`. `agy` takes its prompt only in argv, so
  these routes declare `{{task}}` and receive empty stdin instead.
- `grok`: `low`, `standard`, and `high` use `-p` with `--reasoning-effort`
  `low`, `medium`, and `high`, and `--permission-mode acceptEdits`. `--sandbox`
  is left at `grok`'s own default because its profile vocabulary was never
  enumerated in `--help`, and an unmeasured value is not shipped. `grok` also
  takes its prompt only in argv, so these routes declare `{{task}}` and receive
  empty stdin instead.

Neither default route pins a model. Model selection stays your reviewed
policy's decision, expressed inside that policy's `command`; see
[Override the routes](#override-the-routes).

`--source-vendor` is required when weightclass is called from an agent
integration. With the default policy, `--source-vendor codex` selects only
Codex routes, `--source-vendor claude` selects only Claude routes,
`--source-vendor agy` selects only Antigravity routes, and `--source-vendor
grok` selects only Grok routes. weightclass is a standalone process, so it
does not try to infer its parent application.

When `--source-vendor` is omitted, weightclass still pins every tier to a
single vendor: the vendor of the first route declared in the policy (`codex`
for the built-in routes). A tier is never silently served by a second vendor —
that requires `"allow_mixed_vendors": true`. The `vendor` field is always
present in `wclass route` output.

## Classification

By default, classification is local, deterministic, and offline: security,
authentication, authorization, data, migration, concurrency, performance,
production, and architecture signals route to `high`, as do narrowly defined
high-impact outcomes such as duplicate charges, duplicate work, and balances
becoming negative. Short typo, spelling, formatting, and rename tasks route to
`low`; other valid tasks route to `standard`. Outcome patterns require their
full context, so a request merely to display a negative balance or deliberately
repeat a test job is not escalated. Unknown or oversized task input fails
closed.

Add `--explain` to a local classification to include its versioned static
reason code:

```sh
printf '%s' 'Fix a spelling typo.' | wclass classify --explain
# {"tier": "low", "reason_code": "low.mechanical", "policy_version": "3"}
```

The explanation contains policy metadata only: it never includes task text,
task hashes, matched fragments, credentials, or provider output. It is not
available with `--ask-vendor` or `--show-triage-command`, because those are not
local tier decisions. Without `--explain`, the existing JSON output is
unchanged. Narrow security-failure phrases use `high.risk_floor`; broader
complexity vocabulary uses `high.complexity_signal`. Reason-only changes do not
enter route fingerprints, while a corrected tier can select a different route
and therefore a different fingerprint.

**Keyword matching has a measured ceiling.** Before explicit high-impact
outcome patterns were added, the local classifier agreed with 15 of 40 tasks on
a benchmark rated independently by three raters (unanimous on 39 of 40). A
rerun after that narrow refinement yields 17 of 40, but the corpus is now
public, so neither figure is valid evidence of general accuracy for later
changes. The remaining failures are not vocabulary gaps that more words would
close: people describe hard problems in ordinary language with no technical
term to match. Build and blind-rate a fresh corpus before making a new accuracy
claim.

`--ask-vendor` puts the question to a CLI you already have installed:

```sh
task='Bump the copyright year in LICENSE and the footer component.'

printf '%s' "$task" | wclass classify
# {"tier": "standard"}

printf '%s' "$task" | wclass classify --source-vendor claude --ask-vendor
# The provider-owned result may be low, standard, or high.
```

In the historical measurement made before the public fixture was refined, the
local classifier scored 15/40 and the recorded vendor tiers scored 33/40,
without over-rating. The vendor result still under-rated 7 of the 15 genuinely
hard tasks, so it was better, not solved. Models change, and those recorded
tiers are not a current provider claim. The current offline command
`PYTHONPATH=src python3 tests/eval/score.py` re-derives only the local public
regression result, now 22/40 under classification policy 3. Read that number
as a direction check, not an accuracy claim: the fixture is visible, so a score
against it measures the tuner as much as the classifier. What policy 3 changed
is documented below and in `src/weightclass/classification.py`; its stated aim
was to stop over-routing mechanical work, and on this fixture over-routing fell
from 32.5% to 17.5% while high-tier recall stayed at 5/15. A supported vendor
comparison requires a fresh
evaluator-supplied corpus and `--compare-triage`, as documented in
[`tests/eval/README.md`](tests/eval/README.md).

This does not make weightclass an API client. It runs one vendor CLI in the
foreground; that CLI owns its credentials and network. The triage call is a
separate opt-in disclosure and quota/billing event before any later `wclass
run`. There is no new key stored by weightclass, but there can be an additional
vendor invocation.

It is not a token-saving path. If you classify with `--ask-vendor` and then
run the task, the full task reaches the external vendor once for triage and
again for execution. Count both invocations, plus any rework, when comparing
net token use.

Where the task goes is your choice, and weightclass does not tie the two steps
together: nothing stops you from asking Claude for a tier and then running the
task on Codex. If you want the task to reach only one vendor, pass the same
`--source-vendor` to both commands.

The flag is opt-in and `--source-vendor` is required, so weightclass never picks
a vendor to bill on your behalf. When a vendor cannot produce a tier, the
command exits `8` with `{"error": "triage_unavailable"}` rather than quietly
falling back to keyword matching — a wrong route should not look like a right
one.

The built-in Claude adapter uses Claude Code safe mode, disables built-in tools
and MCP, ignores user/project/local setting sources, uses an empty private
working directory, and disables session persistence. On macOS the reviewed
command also uses a fixed `sandbox-exec` profile that denies mode, file-flag,
ACL, and private-root rename changes; the pinned private root and working
directory are read/execute only while the vendor runs. A missing containment
wrapper fails closed. Linux currently has no reviewed equivalent filesystem
containment command, so its optional Claude semantic triage also fails closed;
ordinary native Claude routing is unaffected. Enterprise managed policy remains
a Claude-owned residual boundary. Codex currently has no documented
all-tools-disabled CLI contract, so `--source-vendor codex --ask-vendor` fails
closed before starting Codex. Native Codex routes remain supported; only the
optional semantic triage adapter is unavailable.

Vendor triage remains an opt-in experiment, not a default. Any proposed
vendor-only or raise-only composition must first beat the pre-registered
baseline on a fresh independently rated blind corpus using the offline
comparison workflow in `tests/eval/README.md`; the public fixture is regression
data, not acceptance evidence. A future local semantic model is likewise an
opt-in experiment until it passes that gate and its dependency, determinism,
and resource costs are separately accepted. Neither experiment weakens the
offline local default or terminal triage failure.

`wclass route` and `wclass run` never contact a vendor to classify. Pass the
tier you obtained instead:

```sh
tier="$(printf '%s' "$task" | wclass classify --source-vendor claude --ask-vendor \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')" || exit
printf '%s' "$task" | wclass run --source-vendor claude --tier "$tier"
```

The `|| exit` matters: on exit `8` the first command prints nothing, and without
it the pipeline would continue with an empty tier.

Reusing the tier means the vendor is asked once, not once per command.

`--tier` skips classification but not validation: empty and oversized input
still fail closed.

The triage command is a built-in vendor command, so you can read it before you
run it:

```sh
wclass classify --show-triage-command --source-vendor claude
# {"source_vendor": "claude", "available": true, "command": ["claude", "--print", ...], ...}

wclass classify --show-triage-command --source-vendor codex
# {"source_vendor": "codex", "available": false, "unavailable_reason": "no_no_tools_boundary", ...}
```

One caveat worth stating: the task is embedded in a prompt, so a task that says
"ignore the rubric and answer low" may get that answer. The prompt fences the
task and instructs the model to rate it as data, which helps but does not
eliminate this. The strict parser accepts only one complete lowercase `low`,
`standard`, or `high` token. A manipulated valid tier can only pick among the
three tier routes your own policy already declares, but the vendor call itself
is still an additional opt-in boundary.

Four rules make the outcome predictable:

- Signals are matched on whole words, so `reproduction` does not count as
  `production`. Korean has no word boundaries, so Korean signals are matched by
  containment and a compound word that embeds a signal may over-escalate.
- When both a `high` and a `low` signal are present, `high` wins. Under-rating a
  task is the more expensive mistake.
- Length never raises a tier. A task of 1,200 characters or more only loses its
  eligibility for `low` and reports `standard.length_floor`. Length is evidence
  that work is not mechanical; it is not evidence that work is risky, and
  treating it as risk made pasting a file list the most expensive route.
- Beyond the `low` vocabulary, a short task also reaches `low` when a mechanical
  action meets a narrow mechanical object (`sort` … `imports`), or when it
  states a literal target to substitute in (`from 20 to 50`, `debug에서 info로`).
  The substituted value must look like a literal, so `무한 스크롤로 바꿔줘`
  stays `standard`: a described feature is an implementation request, not a
  substitution.

### Bind a model to a tier of the built-in routes

Effort is only one of the two levers, and model grade is usually the larger one.
Tier-specific labels therefore attach to the built-in routes directly, without
materializing a preset policy first:

```sh
printf '%s' "$task" | wclass route \
  --source-vendor codex \
  --low-model your-reviewed-cheap-model \
  --high-model your-reviewed-capable-model
```

`--source-vendor` is required: without it, which vendor's built-in routes receive
the label would depend on declaration order. Only the named tiers change; the
others keep their built-in command exactly. The label is opaque — weightclass
never checks that the model exists, is available to the account, or costs less.
Because the command changes, the route fingerprint changes too, so a `run`
acknowledged with the unlabelled fingerprint still refuses to start.

Vendors differ in what they can accept, and an unsupported combination fails
closed rather than being silently dropped: `agy` has no model flag, and the
`grok` effort override is not yet measured. `configuration_status` reports
`unqualified_custom` for any bound label.

## Override the routes

Use `wclass route --policy policy.json` to review a local policy, then
`wclass run --policy policy.json --ack-route-fingerprint <fingerprint>` to run
what that review selected. `run` refuses a policy without the acknowledgement;
see [Bind a run to the selection you reviewed](#bind-a-run-to-the-selection-you-reviewed).
Routes are considered in listed order, so the first matching `tier` is selected. Add
`--source-vendor <vendor>` matching the route's vendor label when invoking it
from that vendor — `codex` or `claude` for the example policy below, but any
label a route declares works the same way. Configure model labels and
vendor-specific effort arguments only with labels you know are available to
you.

```json
{
  "allow_mixed_vendors": false,
  "posture": "balanced",
  "routes": [
    {
      "id": "codex-low",
      "vendor": "codex",
      "tier": "low",
      "command": ["codex", "exec", "--model", "your-low-model-label", "-"]
    },
    {
      "id": "claude-high",
      "vendor": "claude",
      "tier": "high",
      "command": ["claude", "--print", "--model", "your-high-model-label", "--effort", "high"]
    }
  ]
}
```

`posture` is optional and defaults to `balanced`, preserving the documented
local classification. An explicitly reviewed `"posture": "cautious"` raises
only an otherwise `standard` local decision to `high`; it does not change
`low` or already-`high` decisions, override `--tier`, switch vendors, inspect
model labels, or infer subscription availability. When posture is explicit,
`wclass route` renders both `posture` and a static `reason_code`. Any other
posture value or shape fails closed with the redacted `invalid_input`
diagnostic. Because `cautious` can select a higher effort route, it can increase
token use; it is a safety preference, not an efficiency setting.

Native policies, workflow descriptors, and V2 policies must each be a regular
file no larger than 262,144 raw bytes. Parsing is strict UTF-8, duplicate object
keys are rejected at every nesting depth, and special files such as FIFOs fail
promptly. Argument-addressed policy and descriptor files are validated before
weightclass reads transient task input. Symlinks remain accepted only when the
object opened for that invocation is a regular file; this does not make a path
stable between a separate review and run.

The `command` tokens are opaque policy values. weightclass validates their shape
but does not assert vendor CLI semantics or subscription access. Always run
`wclass route` with a representative non-sensitive task to inspect a policy
before using `wclass run`.

### Experimental effort-inheritance policy

An evaluator can test a narrower schema-1 policy without changing built-ins or
adding an `efficient` posture. In this example, only the `standard` route omits
Claude's effort override and therefore inherits whatever default the installed
CLI and its configuration choose:

```json
{
  "allow_mixed_vendors": false,
  "posture": "balanced",
  "routes": [
    {
      "id": "experimental-efficient-v1-claude-low",
      "vendor": "claude",
      "tier": "low",
      "command": ["claude", "--print", "--no-session-persistence", "--permission-mode", "acceptEdits", "--effort", "low"]
    },
    {
      "id": "experimental-efficient-v1-claude-standard",
      "vendor": "claude",
      "tier": "standard",
      "command": ["claude", "--print", "--no-session-persistence", "--permission-mode", "acceptEdits"]
    },
    {
      "id": "experimental-efficient-v1-claude-high",
      "vendor": "claude",
      "tier": "high",
      "command": ["claude", "--print", "--no-session-persistence", "--permission-mode", "acceptEdits", "--effort", "high"]
    }
  ]
}
```

This is an experiment, not a built-in recommendation. weightclass cannot prove
what provider default is selected, whether that default remains stable, or
whether omitting the flag saves tokens. Keep the source vendor fixed, review
the exact route fingerprint, freeze the CLI/model/configuration outside the
router, and compare total provider-reported usage—including every authorized
invocation and rework attempt—with the offline paired gate in
[`tests/eval/README.md`](tests/eval/README.md). Until independent evidence
passes that gate, the built-in `standard=medium` commands and the accepted
`balanced`/`cautious` posture vocabulary remain unchanged. Native schema 2 also
continues to require an explicit reviewed model/effort pair.

Exploratory measurements also found that explicit Haiku/low could use more raw
tokens while reporting a much lower estimated provider cost. That is not a
contradiction: model prices differ. The diagnostic covered only one public
low-risk task across disposable layouts and used JSON output for usage
collection. The exact evaluation baseline remains in
[`tests/eval/claude_cost_baseline_policy.json`](tests/eval/claude_cost_baseline_policy.json)
and the exactly evaluated candidate is available as the explicit opt-in
[`src/weightclass/examples/claude_cost_focused_policy.json`](src/weightclass/examples/claude_cost_focused_policy.json).
Only its low route changes model/effort; standard and high remain identical to
the baseline. The candidate passed the predeclared low-target estimated-cost
gate, but used more raw tokens. It deliberately exposes JSON as user output for
measurement and is neither a built-in nor a general-use default. Review its
exact `wclass route` command and fingerprint before `run`. Wheel installs can
materialize the same reviewed policy with
`wclass example-policy claude-cost-focused > policy.json`. See the separate
token and estimated-cost gates in
[`tests/eval/README.md`](tests/eval/README.md); the result authorizes only this
cost-focused low-route opt-in and does not change any built-in.

The same installable command surface also exposes explicit cost experiments
for every other built-in vendor:

```sh
wclass example-policy codex-cost-focused > codex-policy.json
wclass example-policy agy-cost-focused > agy-policy.json
wclass example-policy grok-cost-focused > grok-policy.json
```

Codex additionally accepts an opaque model label without weightclass trying to
validate availability or price. Prefer the tier-specific low-only form for a
cost experiment so the failed standard-low candidate stays removed:

```sh
wclass example-policy codex-cost-focused \
  --low-model your-reviewed-codex-low-model > codex-policy.json
```

The generated command carries `--model your-reviewed-codex-low-model` only on
the low route. Standard remains on medium effort with the installed Codex
default model, and high remains unchanged. The older `--model` shorthand still
changes low and standard together for compatibility, but that custom shape is
unqualified and is not the recommended cost-evaluation candidate.
The label must be one printable non-whitespace argv token of at most 240 UTF-8
bytes and must not begin with `-`. Review the generated route fingerprint
before execution; changing the model changes that fingerprint.

These three policies are intentionally narrower claims than the evaluated
Claude policy. Their static forms pin no model and now keep low, standard, and
high effort aligned with the corresponding built-ins; in particular, standard
remains medium after the standard-low Codex canary used more tokens. Therefore
an unmodified Codex, `agy`, or Grok preset is only a reviewable experiment
scaffold, not an economic candidate. `wclass recommend` abstains when candidate
and baseline commands are identical. Optional tier-specific Codex or Grok
model overrides change the exact reviewed command and require separate
evidence. No provider usage, pricing, or quality evidence has qualified those
custom configurations, so their names describe an optimization hypothesis—not
measured token or billing savings.
Keep them opt-in, review the exact route and fingerprint, and evaluate each
vendor independently before broader use.

All four examples keep `allow_mixed_vendors` false. Supply the matching
`--source-vendor` when routing or running a materialized policy file; the
in-memory preset shorthand below already carries that vendor. Codex and Claude
receive the task through stdin; `agy` and Grok retain their documented
`{{task}}` argv delivery and local process-inspection exposure.

For a task-free review of all three routes, use the packaged preset name:

```sh
wclass review-preset claude-cost-focused
wclass review-preset codex-cost-focused
wclass review-preset grok-cost-focused
```

The JSON output includes every exact command, route fingerprint, tier, vendor,
and `stdin`/`argv` task-delivery boundary. It also labels the unchanged Claude
preset `measured_low_route_only`; the other packaged presets are
`unqualified_experiment`. This command neither reads task stdin nor invokes a
vendor.

You do not have to materialize those JSON files or repeat the vendor name.
Native schema-1 `route` and `run` can select a packaged policy in memory with
`--preset`:

```sh
printf '%s' 'Add a focused unit test.' |
  wclass route --preset codex-cost-focused \
    --model your-reviewed-codex-model
```

`--preset` carries its source vendor explicitly in the reviewed name; it cannot
be combined with `--source-vendor`, `--cost-focused`, `--policy`, or
`--source-profile`. The older `--cost-focused --source-vendor <vendor>` form
remains supported. Invalid combinations fail before task input is read.

Claude and Codex presets accept independent model and effort labels for each
tier. Grok accepts the same tier-specific model labels while retaining the
packaged effort command:

```sh
wclass review-preset claude-cost-focused \
  --low-model your-claude-low-model --low-effort low \
  --standard-model your-claude-standard-model --standard-effort medium \
  --high-model your-claude-high-model --high-effort high

wclass review-preset codex-cost-focused \
  --low-model your-codex-low-model --low-effort low \
  --standard-model your-codex-standard-model --standard-effort medium \
  --high-model your-codex-high-model --high-effort high

wclass review-preset grok-cost-focused \
  --low-model your-grok-low-model \
  --standard-model your-grok-standard-model \
  --high-model your-grok-high-model
```

The same vendor-supported tier flags work on `route` and `run` with either
`--preset` or the older `--cost-focused` selector. Labels are opaque:
weightclass checks only that each is one printable, non-whitespace, non-option
argv token of at most 240 UTF-8 bytes. It does not infer model availability,
effort vocabulary, subscription access, quality, or price.
`agy` rejects all tier overrides. Grok accepts model overrides through its
reviewed `--model` shape but rejects effort overrides; the packaged
`--reasoning-effort` values remain unchanged. The older Codex `--model`
shorthand still applies one model to low and standard; it cannot be combined
with any tier-specific model flag.

Any model or effort override is labeled `unqualified_custom`; whenever it
changes the reviewed command, the fingerprint changes with it. Even if a label
happens to reproduce an existing command byte-for-byte, the explicit custom
selection remains outside the packaged Claude low-route claim. Evaluate custom
configurations independently before claiming token or cost savings.

Either selector chooses a policy; it does not waive review. Copy the exact
`route_fingerprint` from `route` into the otherwise identical `run` command:

```sh
printf '%s' 'Add a focused unit test.' |
  wclass run --preset codex-cost-focused \
    --standard-model your-codex-standard-model \
    --standard-effort medium \
    --ack-route-fingerprint 'sha256:REVIEWED_FINGERPRINT'
```

No preference is persisted and no router configuration file is written.
Removing `--preset` or `--cost-focused` immediately restores the built-in route
selection.

### Evidence-gated cost recommendation

`wclass recommend` is a non-executing, same-vendor advisory layer over the
packaged presets. It consumes a user-reviewed opaque cost profile and a strict
qualification card, then returns either `recommend` or `abstain`. It does not
infer provider pricing, inspect billing, start a child, retry, fall back, or
change built-ins. A later `run` still requires the ordinary exact route review
and acknowledgement.

See [Cost-aware recommendations](docs/cost-recommendation.md) for the input
schemas, fixed quality and uncertainty gates, canonical fingerprints, provider
capability differences, and end-to-end workflow.

For sanitized provider-export measurements, use the separate offline
`tests/eval/provider_usage_benchmark.py` adapter. It distinguishes metered cost
from fixed-price subscription quota. A passing quota result is capacity-only
and is never eligible for a cost recommendation or a monthly-bill reduction
claim. Never give the scorer a raw billing export; normalize it outside the
repository after removing task data and account identifiers.

A route's `vendor` is a containment label you choose, not a list of tools
weightclass knows. Any printable identifier without whitespace, up to 64 bytes,
is valid. Routing compares it as a string and the fingerprint hashes it as a
string; nothing in weightclass holds vendor-specific knowledge about it.

That means an agent weightclass ships no built-in command for is still usable
by whoever has it installed:

```json
{
  "routes": [
    { "id": "qwen-low", "vendor": "qwen", "tier": "low",
      "command": ["qwen", "-p", "{{task}}"] }
  ]
}
```

The label still does its job. Routes of different vendors do not mix without
`"allow_mixed_vendors": true`, and a fingerprint reviewed for one vendor never
matches another.

Because the label is open, `--source-vendor` can no longer reject a typo.
`--source-vendor codx` is well-formed, so it is not an argument error; it simply
matches no route and exits `3` with `{"error": "unsupported_route"}`. A
malformed label — empty, containing whitespace, over 64 bytes, or carrying
non-printable characters — still exits `2` with `{"error": "invalid_input"}`.

A command may contain the reserved token `{{task}}` once, as a whole argument.
That route receives the task at that argv position and receives empty standard
input, instead of the default of the task on standard input. This exists for
agents that read a prompt only from their command line: `agy --print ""` and
`grok -p ""` both refuse an empty prompt and never read the pipe.

`wclass route` prints the command with `{{task}}` still in it and adds
`"task_delivery": "argv"`, so a review never contains task text and the
fingerprint does not change from one task to the next.

**Command lines are readable by every user on the machine.** A route that uses
`{{task}}` exposes the task to anyone who can run `ps` for as long as the child
runs. On a single-user machine this is inconsequential; on a shared host it is
not. Nothing weightclass can do removes this — it follows from how these agents
accept a prompt — so it is your decision each time you write `{{task}}` or
select an `agy` or `grok` built-in route.

A token is passed to the selected program as one `argv` entry, without a shell,
so a token may contain spaces — an install path such as
`/Users/me/My Tools/claude`, or a multi-word flag value.

A token may not contain a character that a reviewer would not see, since review
is the whole point of rendering the command. Rejected are every Unicode `C`
category — control characters, format characters such as zero-width space and
the bidirectional overrides, surrogates, private-use and unassigned code points
— along with any whitespace other than the ASCII space, and leading or trailing
whitespace. The same rule applies to V2's `model` and `effort` labels.

## Bind a run to the selection you reviewed

`wclass route` prints a `route_fingerprint` over the selected route id, vendor,
command, tier, the policy's `allow_mixed_vendors` setting, and an explicitly
declared posture. `wclass run --policy` requires it — running a policy without
one exits `6` before the task is read. Pass it back to bind the run to that
selection:

```sh
task='Review this authorization change.'
fingerprint="$(printf '%s' "$task" | wclass route --policy policy.json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["route_fingerprint"])')"
printf '%s' "$task" | wclass run --policy policy.json \
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
  It binds the policy's selection, not the identity of the executable — the same
  limit V2 has for `--api-runtime`.

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

## One-child native delegation (schema 3)

Schema 3 can review and run exactly one bounded subtask through one of the four
closed native builders. First produce a task-free review descriptor:

```sh
wclass delegate native route \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low
```

The canonical descriptor has `purpose: "native_delegation"`, includes the
selected executable's `lstat` identity, lists its required confirmations, and
binds all of that into `route_fingerprint`. It reads no subtask. An ordinary
schema-3 `wclass route` descriptor has `purpose: "native_route"`; its
fingerprint cannot authorize this delegation command.

Run only after reviewing that exact output:

```sh
printf '%s' 'Implement the one reviewed subtask.' | \
  wclass delegate native run \
  --policy native-policy-v3.json \
  --source-vendor codex \
  --source-profile work \
  --tier low \
  --confirm-native-delegation \
  --confirm-endpoint-transition \
  --ack-route-fingerprint 'sha256:copied-from-delegate-native-route'
```

`--confirm-native-delegation` is always required. Add
`--confirm-endpoint-transition` only when the reviewed artifact lists
`endpoint_transition`; a route whose source and destination are the same
profile/vendor does not require it. Review produces information, while these
run flags provide execution consent; neither one substitutes for the other.

After confirmations and an exact acknowledgement, run checks safe direct-child
status ownership, observes and binds the executable, compares the fingerprint,
reads stdin exactly once, observes the executable again, and starts one
foreground child with inherited output. Codex and Claude receive the exact
validated UTF-8 task bytes on stdin. The built-in `agy` and Grok command shapes
replace one reviewed `{{task}}` argv slot and use empty child stdin, so the task
is visible to local process inspection while the child runs. Argv delivery
rejects NUL, more than 32,768 UTF-8 bytes, and a Grok task beginning with `-`.

This command does not decompose the subtask, start a planner or reviewer,
capture or interpret child output, persist task artifacts, synthesize results,
retry, fall back, supervise descendants, or read provider usage. When the
optional local aggregate store is enabled, it records only the selected
schema-3 dimensions and direct-child status described above. Profile, account,
model, entitlement, pricing, subscription, and quota labels are opaque caller
assertions. The executable observations detect ordinary replacement
between review and run and immediately before spawn, but path-based execution
still has a residual replacement race after the final observation. See
[Native schema 3](docs/native-schema-3.md) for the exact boundary and exit
mapping.

## Reviewed role delegation

P0 adds a compatibility-isolated review command:

```sh
wclass delegate route \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor codex \
  --tier standard
```

It selects exactly one workflow, fully inlines its orchestrator, worker, and
reviewer profiles plus the matching adapter, and emits a canonical descriptor
whose fingerprint can be reproduced from the output alone. Claude and Codex
use the same role/action/stage contract, while protocol 1 requires every role
to match `--source-vendor` and use the native transport. Model and effort
labels remain opaque policy values.

The runtime path may be nonexistent during review: route compilation validates it
lexically but never resolves, stats, opens, hashes, or executes it. The output
therefore says `declared_enforcement`; it does not say the runtime exists, that
it delegated work, or that any named model authored an artifact.

To run, copy the exact fingerprint and provide both execution gates:

```sh
printf '%s' 'Apply the reviewed change.' | \
  wclass delegate run \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor codex \
  --tier standard \
  --confirm-trusted-delegation-runtime \
  --ack-route-fingerprint 'sha256:copied-from-route'
```

`delegate run` recompiles without printing, checks confirmation and the exact
fingerprint, verifies that the reviewed path is currently a regular executable,
then reads and validates task stdin. It constructs the complete bounded WCD1
frame before spawning exactly one foreground process:

```text
/absolute/reviewed/runtime --weightclass-delegation-protocol 1
```

The canonical review descriptor and UTF-8 task are sent on the child's standard
input within the fingerprinted `direct_child_cleanup.grace_seconds` deadline.
Its stdout/stderr and environment are inherited. weightclass does not capture,
parse, redact, limit, or retain runtime output. Runtime nonzero and post-spawn
framing failure map to exit `7`; framing failure triggers the fingerprinted
direct-child `close -> wait -> terminate -> wait -> kill -> reap` sequence.
weightclass does not enumerate descendants.

Before task input is read, run rejects a non-main-thread launch or a
Python-visible non-default `SIGCHLD` disposition. Platform flags hidden from
Python can only be detected after spawn; weightclass owns the direct
`waitpid`, never converts unavailable child status to exit zero, and maps that
condition to the same redacted exit `7` failure.

P0.5 includes no bundled Claude/Codex orchestrator. The user-supplied runtime
owns vendor authentication, network and billing behavior, role processes,
permission enforcement, review, integration, its deadline, descendants, and
output. A dishonest runtime can exit zero without doing those things, so the
descriptor remains `declared_enforcement`.

P1's local qualification foundation is opt-in. Add
`--require-qualified-runtime` to both `delegate route` and `delegate run` to
require a package-owned record matching the manifest build ID, host platform,
protocol, adapter, and source vendor. The qualified route fingerprint also
binds the recorded executable SHA-256 and size, conformance-suite revision,
and evidence digest. Run reopens the absolute path and checks the exact bytes
before reading task stdin. Qualified mode rejects a final symlink and retains a
documented hash-to-spawn path-replacement race because the child is still
started by path.

The shipped registry is intentionally empty, so qualified route/run currently
fail closed with `unsupported_route`: no real Claude or Codex adapter has been
independently qualified. There is no CLI, environment variable, or user path
that overrides the production registry.

Package maintainers can normalize a task-free conformance report into an
untrusted review candidate without changing that registry:

```sh
wclass delegate qualification-candidate \
  --evidence /absolute/conformance-evidence.json \
  --delegation-runtime /absolute/runtime
```

Candidate input must contain all 54 role/category/action/mode observations and
all required lifecycle, attribution, review, integrity, integration, deadline,
cleanup, leakage, and output-channel scenarios, with every result passing.
This command validates shape and hashes the local executable; it does not prove
that the evidence is independent and does not qualify the runtime. Review and a
source change to the package registry are still required.

Repository maintainers can produce that evidence through the bounded external
driver contract:

```sh
PYTHONPATH=src python3 -m weightclass.delegation_conformance \
  --driver /absolute/reviewed/adapter-conformance-driver \
  --runtime /absolute/runtime \
  --runtime-build-id 'opaque runtime build' \
  --adapter-id claude-native-v1 \
  --vendor-family claude
```

The runner creates a new private workspace for each of the 67 predeclared
cases, never reads task stdin, and starts the driver with exactly:

```text
/absolute/reviewed/adapter-conformance-driver \
  --weightclass-conformance-driver 1
```

Each case has a fixed 60-second deadline and a 4,096-byte stdout limit; driver
stderr is discarded. The driver process starts in a new session. A nonzero
exit, malformed or mismatched response, timeout, oversized output, or a live
same-process-group descendant records that case as failed, then the runner
cleans the group. An interrupt also cleans the active group and returns exit
`130` with a redacted diagnostic. Driver and runtime environment variables are
inherited, so a real driver may still cause vendor authentication, network,
quota, and billing effects; invoke it only after reviewing both artifacts and
the exact command.

The runner hashes the executable before and after all cases and evidence schema
2 carries that exact size and SHA-256. Candidate construction rechecks the
current executable against those observed bytes, so a post-suite replacement
cannot inherit the earlier passing matrix.

No real Claude-family or Codex-family conformance driver is shipped. The test
fixture merely pressure-tests the runner and can trivially claim success
without using the runtime. Evidence from an arbitrary `--driver` is therefore
untrusted, and escaped sessions or process groups remain a driver-side
conformance concern. The package registry stays empty until source-reviewed,
adapter-specific drivers independently establish every required observation.

The exact schema, permission modes, retention rules, byte representations,
process-lifecycle boundary, and P0.5/P1/P2 gates are documented in the
[Claude and Codex delegation roadmap](docs/delegation-roadmap.md).

## V2 API routing through an external runtime

V2 adds declarative API-route selection without turning weightclass into an API
client. weightclass does not read API keys, inspect authentication, or make
network requests. Instead, you provide an already-installed, trusted runtime
at an absolute, executable path. That runtime is responsible for provider
credentials, HTTP, billing, and any provider output.

Use a V2 policy only for API routes; unlike the V1 legacy policy, it cannot
contain arbitrary command arrays. A route is eligible only for its declared
source vendors. `codex` maps to the OpenAI provider family and `claude` maps to
the Anthropic provider family; `allow_cross_provider` must be `true` before a
route can cross those families.

```json
{
  "schema_version": 2,
  "allow_cross_provider": false,
  "allow_api": true,
  "routes": [
    {
      "id": "openai-high-api",
      "tier": "high",
      "eligible_source_vendors": ["codex"],
      "provider": "openai",
      "transport": "api",
      "model": "your-openai-model-label",
      "effort": "high",
      "intended_recipient": "OpenAI API",
      "intended_billing_boundary": "your OpenAI API account"
    }
  ]
}
```

First review the selected destination and copy the returned fingerprint.
weightclass reports the intended recipient and billing boundary from the
reviewed policy; it does not verify either claim.

```sh
printf '%s' 'Review this authorization change.' | \
  wclass v2 route \
  --policy api-policy.json --source-vendor codex \
  --api-runtime /absolute/path/to/weightclass-runtime
```

Starting an API route requires both an explicit egress confirmation and the
exact fingerprint from that review. weightclass recomputes the route before
spawning the runtime, so a change to the selected model, effort, source,
destination, resolved runtime path, runtime identity, or API/cross-provider
permission invalidates the acknowledgement. API `run` rejects a missing egress
confirmation or a missing route fingerprint before checking process context,
inspecting the runtime, or consuming task standard input.

```sh
printf '%s' 'Review this authorization change.' | \
  wclass v2 run \
  --policy api-policy.json --source-vendor codex \
  --api-runtime /absolute/path/to/weightclass-runtime \
  --confirm-api-egress --ack-route-fingerprint 'sha256:copied-from-route'
```

For a selected V2 route, weightclass invokes exactly this fixed protocol,
without a shell, and passes the task only on standard input:

```text
/absolute/path/to/weightclass-runtime --provider PROVIDER --model MODEL --effort EFFORT
```

Do not put API keys, tokens, task text, or personal information in the policy,
route metadata, or command line. V2 does not provide retries, failover,
credential management, background execution, or a bundled provider runtime.

## Security boundary and non-goals

- No persistence: weightclass writes no router artifacts or vendor
  configuration.
- Task text is read only from standard input, held in memory to classify and
  pass to the selected child process, then discarded. `delegate route` does not
  read it; `delegate run` and `delegate native run` read it only after their
  static execution gates.
  weightclass never logs, stores, echoes, hashes, or places it in diagnostics.
- weightclass never reads credentials, subscription balances, pricing, cookies,
  or vendor configuration. It does not capture or process vendor output. V2
  does not issue provider HTTP requests; a separately installed runtime may do
  so only after the explicit acknowledgement described above.
- Every execution path requires the main thread and a native `SIGCHLD`
  disposition that preserves its direct child's exit status before consuming
  task input. An unsafe context fails closed as `executor_unavailable`; a
  concurrent native change after the check remains a documented residual.
- For V2 API routes, weightclass resolves the supplied runtime path before
  review and executes that resolved regular executable path. Its device, inode,
  mode, size, and timestamps are bound into the review fingerprint and checked
  again immediately before spawn. This detects ordinary replacement between
  review and run, but cannot eliminate a replacement after the final check;
  execution remains path-based rather than inode-bound.
- Schema-3 native route and delegation descriptors bind an `lstat` observation
  and recheck it immediately before spawn. That narrows but cannot eliminate
  executable replacement after the final check because execution is still by
  path.
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
- Every policy, runtime manifest, workflow descriptor, and evidence file you
  pass on the command line must be owned by you or by root, and must not be
  world-writable. Either violation is rejected with
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
- `wclass run --policy` requires the fingerprint that `wclass route` printed.
  Running a policy is always two steps; there is no unreviewed shortcut. A
  missing acknowledgement exits `6` before the task is read. This is the boundary
  that actually closes the gap between review and execution, because the
  fingerprint covers the selected command itself: if the policy changes, the
  fingerprint changes and the run refuses. File permissions cannot close that
  gap — anyone who can write the containing directory can replace the file
  regardless of its mode. See
  [Bind a run to the selection you reviewed](#bind-a-run-to-the-selection-you-reviewed)
  for what the binding does and does not cover.
- The built-in routes need no acknowledgement. They live in code, cannot be
  swapped, and there is nothing to bind them to. Treat a policy file the way you
  treat a shell script.
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
