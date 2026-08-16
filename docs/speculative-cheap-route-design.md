# Design: speculative cheap routing with a verification gate

**Status: proposal.** Nothing here is implemented. It requires moving the V1
boundary, which is a product decision, not a refactor.

## The number this exists to capture

A 90-pair qualification compared two Codex models at the same effort. Estimated
API cost fell from $3.4776 to $1.0774 — **−69.02%, 95% interval [60.57%,
77.47%]** — while tokens fell only 4.89%, because the lever is price per token,
not token count. Both arms passed 85 of 90 quality checks.

It was rejected. The cheaper model **introduced two new critical failures**, and
one new critical failure fails the gate. The machine decision was `no-go` and
that decision was correct.

So the situation is: a large, well-measured saving, blocked by a 2-in-90 failure
rate. The proposal is not to accept those failures. It is to catch them.

## Why this is not the routing that already failed

Everything measured so far tried to **predict** the right tier before running.
That has now failed three ways: effort routing never changed an outcome, a
pinned `medium` beat routing on both vendors, and lowering the tier bought
tokens at the price of input-validation defects.

This proposal does not predict. It runs the cheap route, **checks the result
mechanically, and pays for the expensive route only when the check fails.**
Prediction accuracy stops mattering; only the failure rate and the cost of
detection do.

## The economics, and how much slack there is

Let the cheap route cost `c` relative to the expensive one, and let `p` be the
probability it fails verification. A failure costs both attempts.

```
expected cost = c + p
```

With `c = 0.31` from the measurement above:

| p (cheap route fails) | expected cost | saving |
| ---: | ---: | ---: |
| 0% | 0.31 | 69% |
| 5% | 0.36 | 64% |
| 20% | 0.51 | 49% |
| 45% | 0.76 | 24% |
| **69%** | **1.00** | **break-even** |

The cheap route can fail **two times in three** and this still does not lose
money. Against an observed critical-failure rate of 2.2%, and roughly 5.6% of
runs failing quality checks in either arm, a realistic `p` under 10% keeps most
of the 69%.

Verification itself costs no tokens — it is a test run — but it costs wall-clock
on every task, including the ones that would have passed anyway.

## The design

### Speculative execution, not a retry loop

Framing it as "retry on failure" makes it sound like supervision, which is what
V1 forbids and for good reasons. The accurate framing is different:

> Run the cheap route **in a disposable workspace**. Verify. Promote the result
> only if it passes; otherwise discard the workspace entirely and run the
> escalation route in a fresh one.

The workspace is what makes this tractable. It converts "roll back the user's
repository" — a destructive operation on real work, and a thing weightclass has
never done — into "delete a scratch directory". It also means a cheap route that
deletes sources or writes a credential **never touches the real tree at all**.
The blast radius of the failure mode being tolerated is the same directory that
gets thrown away.

### Flow

```
1. clone the repo at HEAD into a temp workspace       (no vendor, no tokens)
2. run the cheap route's reviewed command there        (child 1)
3. run the reviewed verify command there               (child 2, no tokens)
   pass -> emit the diff + base commit, keep workspace, stop
   fail -> delete the workspace entirely
4. clone again into a fresh workspace
5. run the escalation route the router names           (child 3)
6. run the same verify command                         (child 4)
7. emit the diff + base commit + both route fingerprints + the verdicts
```

weightclass **never writes to the user's repository.** Step 3 and step 7 emit a
patch and the commit it applies to; applying it stays a human action, exactly as
reviewing the route is today. That preserves the property that makes the tool
auditable.

### The verify contract

One reviewed command in the policy, alongside the routes. Its **exit code is the
verdict** — zero passes, anything else fails. Nothing is parsed from its output.

The user composes what it does. The study's own gate is a reasonable default to
document:

```sh
python -m pytest -q \
  && git diff --cached --name-only --diff-filter=D | grep -q . && exit 1
  && ! git diff | grep -qE 'sk-[A-Za-z0-9]{16,}|BEGIN [A-Z ]*PRIVATE KEY'
```

That is a project-specific command, which is why it belongs in the policy and
not in weightclass. The router does not learn how to test anything.

**The secret scan is not optional.** A cheap run that writes a credential and
then passes the test suite would otherwise have its diff promoted with the
credential inside it. Verification is the only thing standing between the
tolerated failure mode and the user's tree.

### Isolation choice, and its sharp edge

`git worktree` is cheap but **shares `.git` with the real repository**. An agent
running loose in a worktree can reach the shared object store and refs. That is
the wrong isolation for a mode whose entire premise is "we expect this one to
misbehave sometimes".

Use `git clone` into the temp directory instead. Note that a local clone
hardlinks objects by default; `--no-hardlinks` removes that at the cost of a
copy. For a repository large enough that this matters, the cost of cloning twice
should be weighed against the saving, and the mode is probably not worth it.

## What this does not fix

**Quality defects pass verification.** The tier comparison found the cheap arm
accepting `True` as a schema version, admitting whitespace-padded identifiers
into a ledger, and handing back a mutable cache list — all while passing the
acceptance tests. A verify command made of tests and greps cannot see any of
those.

So this mode recovers **safety**, not **quality**. It bounds catastrophes; it
does not stop the cheap route shipping work a reviewer would send back. Anyone
enabling it should read `QUALITY-RESULT.md` in the study repository first and
decide whether that trade is acceptable for their codebase.

## What it costs the tool

This is the part to weigh honestly, because it is not small.

| V1 property | after |
| --- | --- |
| exactly one foreground child | up to four (two vendor, two verify) |
| does not retry or recover | recovers once, on a mechanical signal |
| never creates or deletes directories | creates and deletes workspaces |
| never runs anything but the selected route | runs a user-supplied verify command |

The last one is less of a change than it looks: the user already supplies the
exact vendor command, so supplying a verify command is the same class of trust.
The first three are real expansions.

There is also a new failure mode with no V1 analogue: **the workspace outlives
the process.** A crash between step 2 and step 3 leaves a directory holding a
half-finished agent run. Cleanup must be crash-safe — a registry of live
workspaces plus a `wclass workspaces prune`, not a `finally` block.

## The question worth asking before building any of it

**Does this belong in weightclass at all?**

A shell script can clone, run `wclass run`, run the tests, and on failure run
`wclass run` again at the tier the escalation suggestion already prints. That is
perhaps thirty lines, it needs no boundary change, and it is auditable by
reading it.

The arguments for putting it in the tool are real but narrow: the escalation
route must be the one the router names at the fingerprint it prints, the usage
store already has the `rework` and `escalation` counters this mode would
populate, and a script that clones repositories and deletes directories is
exactly the kind of thing that should be reviewed once rather than
copy-pasted per project.

The argument against is that this is orchestration, weightclass's value so far
has been in *refusing* to act, and the measured history of this project is that
every cheap lever cost something the gates were right to catch.

**Recommendation: measure `p` with a script before deciding anything.** That
script now exists as [`tools/speculative_run.py`](../tools/speculative_run.py),
with [`tools/speculative_report.py`](../tools/speculative_report.py) reading `p`
and the modelled saving off its log. Neither ships in the distribution —
`MANIFEST.in` lists what does — because both create and delete directories and
run two vendor children, which is exactly the boundary this document is asking
about.

Run it on real work. If `p` comes in under 20%, the saving is large enough to
justify moving the boundary. If it comes in near 69%, the idea is dead and no
implementation would have saved it.

## What to measure if it is built

The usage store already carries the counters. The mode should record, per task:
which route ran first, whether verification passed, whether escalation ran, and
the token totals for each attempt. That yields `p` directly, and `p` is the only
number that decides whether the mode stays enabled.

Do not report a saving from this mode without it. Every savings surface in this
project abstains without evidence, and this one has more reason to than most.
