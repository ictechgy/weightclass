# Design: the advisor pattern, reimplemented locally

**Status: proposal.** Nothing here is implemented in `weightclass` itself. It
requires moving the V1 boundary, which is a product decision. The measurement
support described at the end is the part that gets built first.

## What is being copied, and from where

Anthropic ships an [Advisor
tool](https://platform.claude.com/docs/ko/agents-and-tools/tool-use/advisor-tool)
(beta `advisor-tool-2026-03-01`). A cheap **executor** model runs the task; when
it wants a plan it emits a `server_tool_use` block, Anthropic runs a separate
inference pass on an expensive **advisor** model server-side, and the advice
comes back inside the same request. Most tokens are generated at the executor's
price while the planning comes from a stronger model.

**This document is not about integrating that tool.** It is about reproducing the
*pattern* locally, so it works:

- under a **subscription** as well as an API key, because the router drives CLIs,
  not HTTP;
- across **vendors**, including combinations the original cannot express;
- on **Bedrock**, which does not support the `anthropic-beta` header at all and
  therefore cannot reach the original.

## Why a local imitation is the better shape for this tool

Three reasons, and the third is the strongest.

**It matches what weightclass already is.** The router's whole job is deciding
which model does what, from a local reviewed policy, without making HTTP
requests. "Cheap model does the work, expensive model advises" is a routing
decision. Delegating it to a vendor's server-side feature would move the decision
out of the policy and into an account.

**It removes the billing and platform preconditions.** The original needs a
first-party Claude API key or Claude Platform on AWS. A local implementation
needs neither: it invokes the CLI commands the user already reviewed.

**It can pair models the original cannot.** The Advisor tool requires both sides
to be Claude — the advisor must be Sonnet 4.6 class or better and at least as
capable as the executor. A local implementation can run **Codex as executor with
Claude as advisor**, or the reverse. That combination is not a workaround; it is
the case this project has been routing between all along, and no vendor feature
will ever offer it.

## What survives the translation, and what does not

The original interrupts the executor **mid-generation**. A CLI cannot be
interrupted mid-generation — `claude -p` and `codex exec` run their agentic loop
internally and expose no hook. So a local implementation can only insert advice
**between invocations.**

Three consequences, stated plainly rather than discovered later:

| | original | local |
| --- | --- | --- |
| when advice arrives | inside one request, any number of times | between invocations, at fixed points |
| what the advisor sees | the executor's full transcript, including its reasoning | the executor's **artifacts** — the diff, the verify output |
| output cap | hard `max_tokens` on the advisor, min 1024 | prompt-level request only |

The second is a genuine loss for course-correction: advice grounded in *why* the
executor went wrong is better than advice grounded in *what came out*. It is not
a total loss, because a failing test is a very concrete artifact — arguably more
concrete than a transcript.

The third is the real risk, and it gets its own section below.

## The two shapes

Both are opt-in, independently, by flag. They compose.

### Shape A — advice before the work

```
advisor (expensive, read-only)  reads the task and the repo, returns a plan
      ↓
cheap route runs with that plan prepended to the task
      ↓
verify  ── pass → accept
        └─ fail → escalate to the expensive route
```

Let one full expensive run cost `1` and the un-advised cheap route `c`. **The two
shapes do not call the same advisor.** Shape A's advisor gets the task and reads
the repository; Shape B's gets the task plus a failure excerpt and a diff. The
inputs differ, so the costs differ, and one symbol for both would hide that.
Shape A's call is `a_A`, Shape B's is `a_B`; where the text says `a` without a
subscript it means whichever call that shape makes. Let `p` be the cheap route's
failure rate without advice and `p′` with it. The advised cheap run is **not** `c`: it carries the plan in its prompt,
so give it its own symbol `c_A`, for the same reason Shape B's retry gets `r`.

```
expected cost = a_A + c_A + p′      pays when   a_A + (c_A − c) < p − p′
```

Writing `c` where `c_A` belongs understates the arm by exactly the prompt the
advice adds, and that error runs in the arm's favour.

### Shape B — advice only after a failure

```
cheap route runs
      ↓
verify  ── pass → accept
        └─ fail
             ↓
        advisor (expensive, read-only)  reads the diff and the verify output,
                                        returns what went wrong and what to do
             ↓
        cheap route retries with that advice
             ↓
        verify  ── pass → accept
                └─ fail → escalate to the expensive route
```

With `s` the fraction of **advised failures** that end up rescued — not the
fraction of *attempted retries* that pass; advice can come back empty and then no
retry happens at all, and those failures still count against `s` because they
still escalate — `r` the cost of that retry — **not** `c`, because the retry carries the advice in its prompt and so
costs more than the first attempt — and `q` the fraction of advised failures that
actually produce a retry (advice can come back empty, or the advisor call can
fail, and then nothing is retried and no retry cost is paid):

```
expected cost = c + p·(a + q·r + (1 − s))      pays when   s > a + q·r
```

Charging `r` to every advised failure overstates the arm's cost and can produce
the wrong verdict against it.

**Shape B's condition does not contain `p`.** That matters more than it looks:
the decision can be made from the failed runs alone, which makes the
pre-registration cleaner and the sample requirement smaller. At `c ≈ 0.31` and
`a ≈ 0.1` the break-even is `s > 0.41` when every advised failure retries at the
first attempt's cost, and higher once `r > c`, which it normally is. A `q < 1`
lowers the *threshold* but also caps `s` at `q`, so it does not make the arm
easier to justify **when the retries are not chosen selectively** — `a` is paid
on every advised failure whether or not a retry follows, while only the `q`
fraction can be rescued. Selective retrying breaks that: if the runner skips the
retries least likely to work, `q·r` falls faster than `s` does, and the arm can
pass at a `q < 1` that it would fail at `q = 1`. This measurement retries every
advised failure precisely so that `q` reflects the advisor's own behaviour
rather than a policy layered on top of it — a selective policy is a different
arm and needs its own pre-registration. It replaces a full expensive run with one short
advisory call plus one more cheap run, so the saving per rescued task is large —
but the bar rises fast with `a`, and at `a ≈ c ≈ 0.31` it is already `s > 0.62`.

## The awkward fact about Shape A

Shape A aims at the failure mode that actually blocked model-grade routing — and
this project cannot measure whether it hits it.

The two critical failures in the 90-pair study, and the defects `QUALITY-RESULT.md`
records from the `low` arm, **passed their tests**: accepting `True` as a schema
version, admitting a whitespace-padded identifier into a ledger, returning a
mutable internal cache list. A verify gate never fires on those, so Shape B never
runs for them. Only Shape A could have prevented them, by improving the plan
before the code exists.

But the instrument that would detect that improvement has a documented ceiling:
5/5 unanimity on planted controls, 7/14 on `low` versus routed tier, 3/7 on Codex
versus Claude. `VENDOR-RESULT.md` names the two live explanations it cannot
separate. Pointing it at Shape A at n≈20 would produce another inconclusive
result at real token cost.

So: **Shape B is decidable and Shape A is not, on this workload with this
instrument.** Shape A is still worth building and worth measuring on `p′` — the
failure rate is mechanical and measurable even when quality is not — but a
`p′ ≈ p` result would mean "no measurable effect on failures", not "no effect".
Recording that distinction now is the point of writing it down before collecting.

## The advisor call

One more reviewed command in the policy, alongside the cheap and expensive
routes. What it does is deliberately narrow.

**It runs in a throwaway clone whose workspace is deleted unconditionally.** Not
"deleted unless it passes" — deleted always. The advisor's only output channel is
its stdout. Whatever it writes to disk goes nowhere, so a misbehaving advisor
cannot reach the patch, the verify tree, or the user's repository. This is a
stronger boundary than the executor gets, and it costs nothing because advice is
text.

**It receives the artifacts on stdin.** For Shape A that is the task. For Shape B
it is the task, the diff the cheap route produced, and the verify command's
output. It is invoked as a route command like any other, so the user chooses the
model, the effort, and the flags.

**Its output is capped before use.** Advice is spliced into the executor's task,
so an unbounded blob would blow the executor's context or simply cost money to
carry. Truncation is recorded, not silent.

### The injection channel this opens, and why the blast radius is unchanged

The chain is: untrusted cheap-route diff → advisor → advice text → executor
prompt. A prompt injection planted by the cheap route can therefore try to steer
the advisor into emitting text that steers the executor.

That is a new channel and it should be named rather than waved at. What bounds it
is that **nothing downstream is trusted either**: the retried executor's work is
still rebuilt in a clone it never touched, still reduced to a patch, and still
has to pass the same `verify.sh` — including its secret scan — before anything is
accepted. The advice can change *what the executor tries*; it cannot change
*what passes the gate*. The blast radius is the same directory that already gets
thrown away.

The advice is delimited as untrusted input in the executor's prompt for the same
reason the review target is delimited in this project's review skills: it is
cheap, and it is not the load-bearing control.

## The risk that could kill this: `a` is not small by default

The original's saving depends on the advisor being brief. Anthropic's own numbers:
with no `max_tokens`, advisor output ran 4,200–5,900 tokens on hard reasoning
tasks; at `max_tokens: 2048` it fell to 630–840 with ~0% truncation; at the 1024
minimum, 370–480 with ~10% truncation.

**No CLI exposes that cap.** A local implementation has only the soft lever —
asking for brevity in the prompt — which the vendor's own documentation
distinguishes from a hard cap.

Worse, a CLI advisor that runs in a clone will *read the repository*, so its input
cost is not small either. `a` could plausibly land near `c`, and at `a ≈ c ≈ 0.31`
Shape B needs `s > 0.62` — a much harder bar than 0.4.

Two levers exist, and both should be available:

- **Bound what the advisor sees.** For Shape B the diff plus the verify output may
  be enough; running the advisor in an *empty* directory with that text on stdin
  makes its input bounded and cheap. The cost is that it cannot look anything up.
- **Ask for brevity explicitly**, and record the advice length every time so the
  soft lever's effectiveness is visible rather than assumed.

`a` is therefore a **measured quantity, never an assumed one** — the same rule
this project already applies to `c`. A design that quietly assumes a small `a`
would be assuming away the only thing that decides it.

## Keeping the measurement tractable

Two independent flags suggest four configurations and roughly four times the
tokens. It does not have to cost that.

Measure **two** configurations:

1. **baseline + Shape B** — cheap → verify → (advisor → retry → verify) → escalate.
   Yields `c`, `p`, `a_B`, `q`, `r` and `s` in one pass, because the un-advised
   cheap run and its verify result are the first stage of the same pipeline.
   It does **not** yield `a_A`: Shape A's advisor never runs here.
2. **Shape A + Shape B** — the same pipeline with a plan prepended. Yields `a_A`,
   `c_A`, `p′`, and the primed failure stage `a_B′`, `q′`, `r′`, `s′`.

Configuration 2 **is** A+B, so A+B is measured. Shape A alone is measured too —
its three terms all appear in a configuration-2 log, as the next paragraph
explains. Nobody has to *run* Shape A alone to report it:

```
A alone      = a_A + c_A + p′
A + B        = a_A + c_A + p′·(a_B′ + q′·r′ + (1 − s′))
```

Every term after the up-front advice is primed — including the failure-stage
advisor call itself, whose input is the already-advised task plus the failure
artifacts and so costs `a_B′`, not `a_B`. The escalation is the one exception:
it costs `1` in both shapes because the expensive route gets the original task,
not the advised one. That is why the equations carry `(1 − s′)` unprimed. Each
other term is primed because it is measured on a run whose
prompt already contains the plan: the retry cost, the retry-attempt rate
and the rescue rate are all different quantities from their Shape-B-only
counterparts. Reusing the unprimed symbols would silently assume the plan
changes nothing about what happens after a failure.

`a_A + c_A + p′` for Shape A alone **is measured**, not modelled. An earlier
version of this document called it an extrapolation on the grounds that
configuration 2's `p′` was observed on runs "whose failures were about to be
advised". That reasoning is wrong, and it is worth recording why: Shape B runs
strictly *after* the cheap run's verification, so it cannot change a failure
rate that was already observed at that verification. Nothing about `a_A`, `c_A`
or `p′` depends on what happens next. All three come straight out of a
configuration-2 log.

What the log does **not** give is `c` and `p` — the un-advised baseline. Those
come from configuration 1. So the Shape A verdict needs both logs, but neither
of its terms is modelled. The one assumption left is that the two
configurations are comparable — same task set, same runner, same pricing source.
That is a pre-registration requirement, not a modelling step, and the report
refuses the verdict when it is violated.

## What to fix before spending any tokens

The rule here is that thresholds and stopping conditions are fixed before
collection and shortfalls are reported rather than reinterpreted. It has already
forced two negative results to stand (`CALIBRATION-REPORT.md`,
`VENDOR-RESULT.md`).

To pre-register:

- **n**, and the early-stopping rule.
- **The primary endpoint: cost per passing task.** Not a blind quality rating —
  see the ceiling above.
- **The decision rule for Shape B**: `s > a + q·r` — and note this is the gate
  for adding B to whatever is already running. On an A+B log it answers "is the
  failure-stage advice worth it *given* the plan", not "is A+B worth it"; that
  second question needs configuration 1 as its baseline. Evaluated on the
  *interval*
  of every term, not the point estimates, and abstaining when the interval
  crosses. `r` is the measured cost of the advised retry and is normally above
  `c`, since the retry carries the advice in its prompt; `q` is the fraction of
  advised failures that produced a retry at all.
- **The decision rule for Shape A**: a named margin on `p − p′` **against
  `a_A + (c_A − c)`**, not against `a_A` alone, plus the explicit statement that a
  null result means "no measurable effect on failures" and not "no effect on
  quality".
- **The minimum number of failures** needed before `s` is reported at all. `s`
  comes only from failed runs; twenty tasks at a 20% failure rate is four.
- **What invalidates the run**, e.g. mixed advisor configurations in one log.

## What it costs the V1 boundary

| V1 property | after |
| --- | --- |
| exactly one foreground child | up to seven for A+B (cheap, **two** advisor calls, retry, expensive, plus two verify runs); six for Shape B alone; four for Shape A alone (advisor, cheap, expensive, plus one verify run) |
| does not retry or recover | retries once, on a mechanical signal, with new input |
| never creates or deletes directories | creates and deletes workspaces, including one it always deletes |
| never runs anything but the selected route | runs a verify command and an advisor command |

These are the same expansions the speculative-cheap-route design asks for, plus
one route. The two proposals should be decided together, because **they compose
rather than compete**: the verify gate catches catastrophes, and the advisor
attacks the rate at which the gate fires. An earlier draft of this document
framed them as rivals; that was wrong, and it was wrong because it assumed the
advisor had to be the vendor's server-side feature.

## Where it gets built

In order:

1. **Measurement first**, in `tools/speculative_run.py` and
   `tools/speculative_report.py`, behind `--advisor`, `--advise-first` and
   `--advise-on-failure`. Neither tool ships in the distribution, so this needs no
   boundary change and nothing reaches users who have not asked for it.
2. **Run it on real work** and get `a_A`, `a_B`, `s`, and `p′`.
3. **Only then** decide whether the pattern belongs inside `weightclass`, which
   is where the boundary above actually moves.

Step 3 is a real decision and not a formality: five levers have been measured in
this project and four were dead.

## Appendix: the original's parameters, for reference

Kept because they are the calibration source for the local version's defaults,
not because the local version calls them.

| parameter | value |
| --- | --- |
| beta header | `advisor-tool-2026-03-01` |
| tool type / name | `advisor_20260301` / `advisor` |
| `model` | required; the advisor, billed at its own rate |
| `max_uses` | per **request**, not per conversation |
| `max_tokens` | per call, thinking + text, minimum 1024, recommended 2048 |
| `caching` | `{"type": "ephemeral", "ttl": "5m" \| "1h"}`, off by default, breaks even near three advisor calls |
| usage reporting | `usage.iterations[]` entries typed `advisor_message`; the **top-level `usage` excludes advisor tokens entirely** |
| platform | Claude API and Claude Platform on AWS; **not** Amazon Bedrock, Google Cloud, or Microsoft Foundry |

The usage-reporting row is the one worth remembering even for a local
implementation: it is a reminder that "the cost field you found" and "the cost you
paid" are different questions, which is the same failure the local report guards
against with its single-origin rule.
