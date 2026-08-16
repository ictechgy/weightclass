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
1. clone the repo at HEAD into a temp workspace        (no vendor, no tokens)
2. run the cheap route's reviewed command there         (child 1)
3. rebuild that work in a second clone the child never touched:
   copy its files, leave its .git behind, keep symlinks as links
4. stage, drop whatever .gitignore kept out of the patch, then emit it
5. run the reviewed verify command on that tree         (child 2, no tokens)
   pass -> hand over the patch + base commit, stop
   fail -> delete both trees
6. repeat 1-5 with the escalation route the router names (children 3, 4)
7. emit the patch, base commit, both route fingerprints, and the verdicts
```

Steps 3 and 4 are the load-bearing ones, and each closes a hole the obvious
version leaves open. **Verifying the child's own workspace** runs the verify
script over a `.git` the child could write, where a planted
`filter.<name>.clean` executes on the host. **Verifying before dropping ignored
files** blesses a tree the patch cannot rebuild, so a cheap route could pass the
tests using a file that never ships. Rebuilding, then pruning to exactly what the
patch carries, means the thing verified and the thing handed over are the same
thing.

weightclass **never writes to the user's repository.** It emits a patch and the
commit it applies to; applying it stays a human action, exactly as reviewing the
route is today. That preserves the property that makes the tool auditable.

### The verify contract

One reviewed command in the policy, alongside the routes. Its **exit code is the
verdict** — zero passes, anything else fails. Nothing is parsed from its output.

The user composes what it does. A reasonable default, in the same spirit as the
study's own gate:

```sh
#!/bin/sh
set -e
python3 -m pytest -q

# 자격증명이 작업 트리에 들어왔는지. 바이트 단위로 훑는다.
python3 - <<'SCAN'
import os, pathlib, re, sys

# 이 목록은 출발점이지 완결이 아니다. 쓰는 곳의 자격증명 형식을 넣어라.
PATTERNS = re.compile(
    rb"sk-[A-Za-z0-9_-]{16,}"           # OpenAI, 프로젝트 키의 -/_ 포함
    rb"|gh[pousr]_[A-Za-z0-9]{20,}"     # GitHub 클래식 토큰 전 종류
    rb"|github_pat_[A-Za-z0-9_]{20,}"   # GitHub fine-grained
    rb"|AKIA[0-9A-Z]{16}"               # AWS 액세스 키
    rb"|xox[baprs]-[A-Za-z0-9-]{10,}"   # Slack
    rb"|BEGIN [A-Z ]*PRIVATE KEY"
)
for path in pathlib.Path(".").rglob("*"):
    if ".git" in path.parts:
        continue
    # 경로명 자체도 검사한다. 패치는 파일 이름과 심링크 대상을 그대로
    # 실어 나르므로, 자격증명이 내용이 아니라 이름에 들어올 수 있다.
    haystacks = [os.fsencode(path)]
    if path.is_symlink():
        haystacks.append(os.fsencode(os.readlink(path)))
    elif path.is_file():
        haystacks.append(path.read_bytes())
    if any(PATTERNS.search(blob) for blob in haystacks):
        print(f"credential-like string at {path}", file=sys.stderr)
        sys.exit(1)
SCAN
```

**This deliberately does not use `grep`, and that was measured rather than
assumed.** The runner emits binary additions (`git diff --binary`), so a
credential can arrive inside a file that is not valid text. Against a file
holding `\xff\xfe\x00sk-…\xff`, `grep -a` misses it on BSD/macOS, `strings |
grep` misses it, and `tr -d '\0'` dies with "Illegal byte sequence". Only a
byte-level scan finds it. A secret gate that silently never fires is worse than
no gate, because it is believed.

Two more traps worth naming. Writing the check as `! grep …` inverts grep's
*error* status too — grep exits 1 when it finds nothing, 0 when it finds
something, and **2 on failure**, so an unreadable path turns into "clean". And
`grep -I` skips binary files by design, which is exactly the case that matters
here.

**Verification runs on the handover tree, not on the child's workspace.** The
runner rebuilds the child's work inside a clone the child never had a handle on,
takes the patch there, deletes anything `.gitignore` kept out of that patch, and
verifies what is left. Two things follow. The `.git` under the verify script is
ours, so a `filter.<name>.clean` the child planted has no config to name it. And
what passes verification is exactly what the patch reconstructs — a cheap route
cannot satisfy the tests using a file that will not ship.

Reading files rather than the repository is still the better habit in a verify
script, since the tree it runs on is assembled from untrusted output either way.

**The secret scan is not optional.** A cheap run that writes a credential and
then passes the test suite would otherwise have its diff promoted with the
credential inside it. Verification is the only thing standing between the
tolerated failure mode and the user's tree.

### Verification executes untrusted code, and cannot not

The verify command runs the agent's output — that is the whole point — so a
`conftest.py`, a `Makefile` target, a `setup.py`, or a `.pth` file the child
wrote will execute with the verifier's privileges. There is no version of "run
the tests on what the agent wrote" that avoids this.

What the design does bound is the blast radius: it happens in a clone under a
temp directory, against a `.git` the child cannot use to reach the real
repository, and the tree is deleted unless it passes. What it does not bound is
the host.

**Accepted patches from earlier tasks are not protected from it.** The intended
use is twenty tasks into one output directory, and a verify script from task
fifteen runs with enough privilege to rewrite task three's patch. The runner
raises the bar — workspaces live under `<out-dir>/.work` so the verifier's
parent directory is not the artifact directory, and an accepted patch is left
read-only — but neither is a fence. Code running as the user reaches whatever
the user reaches.

The runner does what it can cheaply: the verify command gets a scrubbed
environment with no provider tokens, and `HOME` and `TMPDIR` point at an empty
scratch directory, so `~/.ssh`, `~/.aws/credentials`, and shell rc files are not
where the child's code will look for them. Code that opens `/Users/you/.ssh` by
absolute path walks straight past all of it.

Anyone running this against genuinely untrusted output should put the verify
command itself in a container or `sandbox-exec` jail. The runner deliberately
does not try to build that jail, because a half-built sandbox invites more trust
than it earns; an honest warning is worth more.

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
