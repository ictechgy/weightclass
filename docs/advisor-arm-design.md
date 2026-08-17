# Design: measuring the Advisor tool as a third arm

**Status: proposal.** Nothing here is implemented. It needs no change to the V1
boundary, because it adds no capability to `weightclass` — it adds one more
*route command* for the measurement harness that already exists.

## What the Advisor tool is

A beta feature of the Messages API (`anthropic-beta: advisor-tool-2026-03-01`).
A cheap **executor** model runs the task; when it decides it needs a plan, it
emits a `server_tool_use` block and Anthropic runs a separate inference pass on
an expensive **advisor** model, server-side, inside the same `/v1/messages`
request. The advisor reads the executor's full transcript and returns strategic
guidance; the executor keeps going.

```
tools = [{"type": "advisor_20260301", "name": "advisor",
          "model": "claude-opus-5", "max_tokens": 2048}]
```

Parameters: `model` (required — the advisor), `max_uses` (per **request**, not
per conversation), `max_tokens` (per call, min 1024, caps thinking + text),
`caching` (`{"type": "ephemeral", "ttl": "5m" | "1h"}`, off by default, breaks
even at roughly three advisor calls).

The advisor runs with no tools and no context management, and its thinking blocks
are dropped before the result reaches the executor.

## Why this is worth measuring here specifically

Every lever this project has measured is dead except one.

| lever | result |
| --- | --- |
| effort-tier routing | 0/18 tier-sensitive against a pre-registered floor of 9 |
| pinned `medium` vs routing | pinned `medium` won on both vendors |
| routing down to `low` | 15/15 passed, but shipped real input-validation defects |
| vendor comparison | invalid — the CLIs report incomparable numbers |
| **model grade** | **−69.02% cost, CI [60.57%, 77.47%], equal quality — rejected for 2/90 new critical failures** |

The model-grade saving is the only real one, and it was blocked by failures that
are *mechanically detectable*. That produced the speculative-cheap-route design:
run cheap, verify, escalate on failure.

The Advisor is a **rival mechanism for the same money**. It also puts most token
generation on the cheap model, but instead of catching the cheap model's mistakes
afterwards it tries to prevent them by paying for expensive *guidance* rather
than expensive *output*. Both target the same 69%. Only one of them needs a
verify script, a throwaway clone, and a boundary change.

That makes it a genuinely useful third arm, and it is cheap to add: the harness
already runs an arbitrary command per route.

## The prediction worth writing down before measuring

Stated now so it cannot be retrofitted afterwards.

**The Advisor is aimed at a different failure mode than the one that killed the
model-grade lever.** The two critical failures in the 90-pair study, and the
defects `QUALITY-RESULT.md` records from the `low` arm, were not planning
failures. They were: accepting `True` as a schema version, admitting a
whitespace-padded identifier into a ledger, and returning a mutable internal
cache list. Those are input-validation and API-surface defects in code the model
had already decided how to structure.

Advice like "use a channel-based coordination pattern" does not prevent any of
them. So the honest prior is: **the Advisor arm should improve plan quality and
leave the defect class that blocked model-grade routing untouched.** If it does,
it is a cost lever with the same unresolved quality risk, not a fix for it.

Anthropic's own framing is modest and matches this — the docs say results depend
on the task and tell you to evaluate on your own workload, and the coding claim
is Sonnet-executor-plus-Opus-advisor reaching *Sonnet-at-default-effort* quality
more cheaply, not reaching Opus.

## The arms

Three arms, same tasks, same `verify.sh`, same throwaway-clone harness:

| arm | route command | what it tests |
| --- | --- | --- |
| 1. baseline | expensive model alone | the quality bar and the cost ceiling |
| 2. speculative | cheap → verify → escalate | the design in `speculative-cheap-route-design.md` |
| 3. advisor | Sonnet executor + Opus advisor | guidance instead of a verify gate |

Arms 1 and 2 already run under `tools/speculative_run.py`. Arm 3 needs one new
piece.

## The piece that does not exist

**The CLI cannot express this.** `claude` accepts `--betas`, but there is no way
to put a tool *definition* into the request it builds, and the advisor tool is a
tool definition. So arm 3 has to speak the Messages API directly.

That does **not** mean changing the runner. The runner treats a route as an
opaque reviewed command that runs in a workspace and edits files. A small script
that speaks the Messages API and exposes a file-editing tool is exactly that:

```sh
tools/advisor_route.py \
  --executor claude-sonnet-5 --advisor claude-opus-5 --advisor-max-tokens 2048
```

passed to the existing harness as `--cheap` or `--expensive`. The runner does not
learn anything new; it still clones, runs one command, rebuilds, verifies.

What that script must do:

1. Read the task from stdin, exactly as the vendor CLIs do.
2. Run an agentic loop over a minimal tool set — read file, write file, run
   command — in the current working directory, which the runner has already made
   a throwaway clone.
3. Include the advisor tool in `tools` on every request.
4. **Emit its own cost as a single JSON object on stdout**, in the shape
   `_claude_usage` already reads: `total_cost_usd` at the root plus a `usage`
   object. The harness then needs no vendor-specific code for this arm.

Point 4 is what makes this fit the existing machinery instead of extending it.

## The cost trap, and why it must be handled inside that script

This is the part most likely to produce a wrong number quietly.

> **The top-level `usage` reflects executor tokens only.** Advisor tokens are
> billed at the advisor model's rates and are **not** in the top-level totals.
> They appear only in `usage.iterations[]` entries with `type:
> "advisor_message"`, each carrying its own `model`.

And separately:

> Every top-level `usage` field is a **sum across all executor iterations**.
> Because each iteration resends the growing conversation, later iterations
> include earlier output, so the summed `input_tokens` exceeds the size of any
> single prompt.

Two independent ways to get this wrong, both of which flatter the Advisor arm or
distort it:

- Read the top-level `usage` and you have **omitted the advisor entirely** — the
  expensive half of the arm — and the arm looks nearly free.
- Read the top-level `input_tokens` as a prompt size and you have
  **double-counted** the executor across iterations.

So the script must price from `usage.iterations[]`, per entry, using the entry's
own `model`, and never touch the top-level object. That is the single most
important line of code in the arm, and it deserves a test that feeds it a
recorded response with two executor iterations and one advisor iteration and
asserts the total is the sum of three separately-priced entries.

This is the same class of error the existing report already guards against with
its mixed-provenance check: a number that is real, arrives in the right field,
and answers a different question than the one being asked.

## What the primary endpoint should be, and what it should not be

**Primary: cost per *passing* task.** Total USD across the arm divided by the
number of tasks whose `verify.sh` exited zero. It is measurable, it needs no
rater, and it is the number the decision actually turns on.

**Not a blind quality rating.** The quality instrument in
`~/weightclass-token-study/` has a documented ceiling: 5/5 unanimity on planted
controls, 7/14 on `low` vs routed tier, 3/7 on Codex vs Claude — decisive only on
defects planted to be findable, and progressively less decisive on real
differences. `VENDOR-RESULT.md` names the two live explanations it cannot
separate. Pointing that instrument at a third arm at n≈20 would produce another
inconclusive result at real token cost.

**Secondary, cheap, and worth collecting anyway:** the count of advisor calls per
task (`usage.iterations[]` filtered by type), the advisor's `output_tokens` per
call against the `max_tokens` cap, and any `stop_reason: "max_tokens"` in the
results. Those say whether the arm was configured sensibly, independent of
whether it won.

**The defect check that actually matters** is not a rating either. It is a
targeted re-run of the specific defect classes from `QUALITY-RESULT.md` —
schema-version validation, identifier normalization, mutable-return — as tasks
with tests that catch them. That is a small, falsifiable check of the prediction
above, and it costs far less than a rating round.

## Pre-registration, before any tokens are spent

This project's rule is that thresholds and stopping conditions are fixed before
collection and shortfalls are reported rather than reinterpreted. That rule has
already forced two negative results to stand (`CALIBRATION-REPORT.md`,
`VENDOR-RESULT.md`), and it applies here.

To fix before the first run:

- **n**, and the rule for stopping early.
- **The decision threshold on cost per passing task.** Arm 3 has to beat arm 1 by
  enough to matter; name the margin now.
- **What counts as a failure of the prediction above** — i.e. how many of the
  targeted defect tasks arm 3 must pass before "the Advisor does not address that
  defect class" is falsified.
- **The rule for an inconclusive result**, so the answer is not chosen after
  seeing the numbers.

## Cost controls to set from the start

- `max_tokens: 2048` on the tool. Anthropic's own benchmark (n=40 per config)
  reports this cut mean advisor output ~7× with ~0% truncation; the 1024 minimum
  cut ~10× but truncated ~10% of calls. Leaving it unset gave 4,200–5,900 tokens
  per call on hard reasoning tasks — which would dominate the arm's cost and make
  the comparison a measurement of one default rather than of the mechanism.
- `caching` **off** unless a task actually calls the advisor three or more times.
  Below that the cache writes cost more than the reads save.
- `max_uses` set to something finite, so one runaway task cannot skew the arm.
  Note it is per **request**, not per conversation — an agentic loop makes many
  requests, so a conversation-level budget has to be counted client-side, in the
  script, by removing the tool from `tools` once the cap is hit.

## Limits worth knowing before planning around this

- **Beta.** The header pins a dated version; it can change.
- **Not on Bedrock, Vertex, or Foundry.** Claude API and Claude Platform on AWS
  only. If the work environment routes Claude through Bedrock, this arm cannot
  run there at all — which is also why `speculative_run.py` now warns when
  `CLAUDE_CODE_USE_BEDROCK` is set and the AWS credential family was narrowed
  out.
- **Pairing constraint.** The advisor must be at least as capable as the
  executor, and at least Sonnet 4.6 class. `claude-sonnet-5` executor accepts
  `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-mythos-5`,
  `claude-fable-5`, or `claude-sonnet-5` as advisor.
- **Priority Tier does not carry over** from executor to advisor.
- **Not suitable for** single-turn Q&A or workloads where every turn needs the
  advisor's full capability — by Anthropic's own account.

## Honest cost of building it

The script in "the piece that does not exist" is a small agentic loop, but it is
a small agentic loop **that edits files from untrusted model output** — the same
thing every other route in this harness does, with the same throwaway-clone and
verify-gate protections around it. It is perhaps 200 lines. The comparison it
makes possible is the first one in this project that pits two live cost
mechanisms against each other rather than measuring one against a guess.

The argument against building it is the same one that applies to everything else
here: five levers have been measured and four were dead, and the fifth was
blocked by a defect class this one probably does not touch. Writing the
prediction down first is what makes the answer worth having either way.
