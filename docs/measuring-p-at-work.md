# Measuring `p` on a real repository with API keys

The design in [`speculative-cheap-route-design.md`](speculative-cheap-route-design.md)
turns on one number: how often the cheap route fails verification. This is how
to get that number from real work, and what changes when the vendors are billed
by API key rather than by subscription.

## What API-key billing changes

Three things, all of them improvements.

**The saving is money.** Under a flat subscription a token saved changes no
invoice. Under an API key it comes straight off the bill.

**Cost becomes measurable.** The earlier study had to model cost because
subscription CLIs report none. Claude with `--output-format json` puts
`total_cost_usd` at the root of its output — verified against the CLI, and real
dollars under API-key billing.

**Vendor comparison becomes possible, on one condition.** Token counts are not
comparable across vendors: Claude sums cache reads into its usage, Codex reports
an opaque total. Dollars are closer to a common unit — but only when both sides
are counted the same way. Claude's `total_cost_usd` is a bill that includes cache
reads; Codex has no dollars at all, so its cost can only come from a table you
supply. Dividing one by the other is not a cost ratio, and the report refuses to
do it: it measures `c` only when every cost in the log comes from one source.

To compare two vendors, put both arms on the same basis with `--prefer-prices`,
which prices *every* arm from `--prices` even where the vendor reported its own
cost. You then own the accuracy of the table for both sides, which is the honest
price of the comparison.

It fails closed. `--prefer-prices` without `--prices` is rejected outright, and
an arm the table cannot price records **no cost at all** rather than falling back
to the vendor's number — because a silent fallback would put that one arm on a
different basis while the flag promised otherwise. A missing cost drops the task
from `c`; a wrong basis would corrupt it.

## What each vendor will and will not tell you

Probed directly against the installed CLIs:

| | Claude Code | Codex |
| --- | --- | --- |
| USD cost | **`total_cost_usd`** at the root | **none, anywhere** |
| tokens | `usage.*`, one JSON object | `turn.completed.usage.*`, JSONL |
| flag | `--output-format json` | `--json` |
| model id in output | dynamic keys under `modelUsage` | **absent** |

Two traps found while probing:

- **Codex's `--json` empties stderr.** The cumulative `tokens used` line the
  older harness scraped is simply not there once the flag is on. `capture.py`
  and `speculative_run.py` now read the JSONL event instead, and fall back to the
  scrape only for runs made without the flag.
- **Claude's `modelUsage` keys contain brackets** — the observed key was
  literally `claude-opus-5[1m]`. Any dotted-path or `jq` access breaks on it.
  Read the root `total_cost_usd` instead; it is the same number.

Because Codex reports no cost, its dollars have to be computed from token counts
and rates you supply. That is what `--prices` is for.

## Setting it up

### 1. Choose the pair

The 90-pair study that produced the −69% figure compared `gpt-5.6-terra` against
`gpt-5.6-luna`. **That is not your baseline.** The Codex config on this machine
runs `gpt-5.6-sol`, so the ratio you care about is `sol` → something cheaper,
and nobody has measured it. Expect to find your own number rather than to
confirm that one.

For Claude the aliases are `opus`, `sonnet`, `fable`; pick the pair you would
actually consider switching between.

### 2. Write `verify.sh`

**Keep it short enough to audit by reading.** A generated 24 KB script that
auto-detects every stack was tried for this document and an adversarial audit
found fifteen distinct ways it wrongly returned "accept". A gate that never
fires is worse than no gate, because it is believed. For one known repository
the honest version is a dozen lines:

```sh
#!/bin/sh
set -e

# 시크릿 스캔을 **먼저** 돌린다. 테스트는 에이전트가 쓴 코드를 실행하는
# 행위이고, 그 코드는 conftest.py 든 무엇이든 스캔이 보기 전에 흔적을
# 지울 수 있다. 유출을 확인하는 검사가 유출한 쪽의 코드를 먼저 실행하면
# 안 된다.
python3 - <<'SCAN'
import os, pathlib, re, sys

PATTERNS = re.compile(
    rb"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    # AKIA 는 영구 키, ASIA 는 임시 자격증명(STS). 둘 다 잡는다.
    rb"|A[KS]IA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|BEGIN [A-Z ]*PRIVATE KEY"
)
# rglob 은 읽을 수 없는 디렉터리를 조용히 건너뛴다. 못 본 곳을 "깨끗함" 으로
# 넘기지 않도록 os.walk 로 순회하며 오류를 직접 받는다.
def refuse(error):
    print(f"unreadable directory, refusing to pass: {type(error).__name__}", file=sys.stderr)
    sys.exit(1)

paths = []
for parent, dirs, files in os.walk(".", onerror=refuse):
    if ".git" in pathlib.Path(parent).parts:
        continue
    paths.append(pathlib.Path(parent))
    # os.walk 는 심링크 디렉터리로 내려가지 않으므로 dirs 를 버리면 그 링크는
    # 한 번도 검사되지 않는다. 링크 자체는 경로명과 타깃을 봐야 한다.
    paths.extend(pathlib.Path(parent, d) for d in dirs)
    paths.extend(pathlib.Path(parent, f) for f in files)

for path in paths:
    if ".git" in path.parts:
        continue
    blobs = [os.fsencode(path)]          # 파일 이름 자체가 시크릿일 수 있다
    if path.is_symlink():
        blobs.append(os.fsencode(os.readlink(path)))
    elif path.is_file():
        try:
            blobs.append(path.read_bytes())
        except OSError as error:
            # 읽지 못한 것은 "깨끗함" 이 아니라 "검사하지 못함" 이다.
            where = "".join(c for c in str(path.parent) if c.isprintable())[:120]
            print(f"unreadable entry under {where}/: {type(error).__name__}", file=sys.stderr)
            sys.exit(1)
    if any(PATTERNS.search(b) for b in blobs):
        # 경로도 시크릿을 담을 수 있으므로 디렉터리까지만 알린다.
        where = "".join(c for c in str(path.parent) if c.isprintable())[:120]
        print(f"credential-like string under {where}/", file=sys.stderr)
        sys.exit(1)
SCAN

# 스캔을 통과한 뒤에야 이 저장소의 실제 테스트 명령을 돌린다. 자동
# 감지하지 않는다 — 감지에 실패하면 아무것도 안 돌리고 통과해 버리는
# 것이 가장 나쁜 결과다.
python3 -m pytest -q
```

Replace the test line with whatever your repo actually runs. Do not use `git`
inside it: the tree is assembled from untrusted agent output.

**A passing verify is not proof the work is good, and the agent can forge the
pass.** The tree the tests run on is the tree the agent wrote, so a `conftest.py`
hook, a `pytest.ini` with the right `addopts`, or a `Makefile` target can make the
command exit zero without the tests meaning anything. Nothing in this harness can
prevent that — running the agent's code is the entire point of the step. It means
the `p` you measure is a **lower bound** on how often the cheap route actually
fails, and therefore the saving is an upper bound. Read a few accepted patches by
hand before you trust the aggregate.

### 3. Write the price table, if you are measuring Codex

Rates are **USD per million tokens**, keyed by the token field they price. The
table drives the sum: a field the vendor reports but you do not name is skipped.

**Name only fields that do not overlap.** Codex reports `input_tokens`,
`cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, and
`reasoning_output_tokens`, and the probe could not establish whether the cached
and reasoning figures are separate line items or breakouts of the two totals.
Pricing all five would double-count. The safe starting point is the two totals:

```json
{
  "cheap":     {"input_tokens": 0.25, "output_tokens": 2.0},
  "expensive": {"input_tokens": 1.25, "output_tokens": 10.0}
}
```

Then **check the result against one real invoice line** before trusting it. If
your provider bills cached input at a discount, add `cached_input_tokens` and
lower the `input_tokens` rate accordingly — but only once you have confirmed
which figure contains which.

**An omission here is not symmetric between the arms.** If
`reasoning_output_tokens` turns out to be a separate line item rather than a
breakout of `output_tokens`, the two-field table understates whichever model
reasons more — normally the expensive one. That shrinks `c` and inflates the
reported saving. The direction is the one that flatters the idea, so resolve it
against an invoice rather than leaving it.

A field the table prices but a particular run does not report counts as zero:
a run that touched no cache genuinely has no cached tokens. It is only when
*none* of the priced names appear anywhere in the run's breakdown that the whole
table is treated as a typo and produces no cost at all — better than a partial
number that looks authoritative. Top-level keys other than `cheap` and
`expensive` are rejected outright, because a misspelled arm would otherwise
leave that side unpriced with no complaint.

That leaves a middle case: `input_tokens` matches and `output_tokens` does not,
because the CLI renamed it. A missing cache field really is zero; a missing
output field is half a bill wearing a whole bill's face. The runner cannot tell
them apart, so it records which priced fields were absent and the report says how
many runs were computed that way. **If that count is not zero, check the field
names against the CLI's output before believing the cost.**

Claude needs none of this for a Claude-vs-Claude run — it reports its own cost,
and a vendor-reported number wins over the table, which can go stale. For a
*cross-vendor* run you need a Claude table too, plus `--prefer-prices` to make it
apply; otherwise the two arms end up on different bases and `c` is not
measured.

### 4. Run it

Commit your work first; uncommitted changes are not in the clone and the runner
refuses to start rather than silently measure something else.

```sh
# Codex, needs the price table and --json for structured usage
python3 -m weightclass.advisory.speculative_run \
  --repo ~/work/service --task-file task.txt \
  --cheap    'codex exec --sandbox workspace-write --json -c model=<cheap> -' \
  --expensive 'codex exec --sandbox workspace-write --json -c model=<expensive> -' \
  --verify ./verify.sh --prices prices.json --out-dir ~/spec-runs

# Claude, reports its own cost
python3 -m weightclass.advisory.speculative_run \
  --repo ~/work/service --task-file task.txt \
  --cheap    'claude --print --output-format json --model sonnet --permission-mode acceptEdits' \
  --expensive 'claude --print --output-format json --model opus   --permission-mode acceptEdits' \
  --verify ./verify.sh --out-dir ~/spec-runs
```

One task per invocation. Around twenty real tasks gives a usable interval.

**Both `--output-format json` (Claude) and `--json` (Codex) are load-bearing, not
cosmetic.** Without them the CLI prints the model's prose to stdout, and the
runner will not read a cost from prose — a JSON-looking line there was written by
the *model*, not the vendor, and treating it as a bill would let the cheap route
name its own price. The runner checks the command for those flags and skips the
structured parsers when they are absent.

**Keep one `--out-dir` per configuration.** The report reads every record in the
log as one sample; it flags mixed route fingerprints, but it cannot tell that you
changed the price table or edited `verify.sh` halfway through. Change either and
start a new directory, or the two halves merge into a single `p` and `c` that
describe neither.

**The child gets a narrowed environment by default.** It keeps PATH, HOME,
locale, proxy and CA settings, plus **its own vendor's** namespace: a `codex`
executable sees `OPENAI_*` and `CODEX_*` but not `ANTHROPIC_*`, and vice versa.
An executable whose name matches neither gets both — breaking an unknown CLI's
authentication is worse than the exposure, and the run says so when it happens.
Everything else is dropped, so an AWS secret or a database URL sitting in your
shell does not reach an agent processing an untrusted task. The run prints how
many names it kept and dropped.

If a CLI suddenly cannot authenticate, that count is the first place to look:
add the missing name with `--child-env NAME`, or fall back to `--child-env-all`
to pass everything as older versions did.

**`--child-env` hands the name to both arms.** When only one of them needs a
credential family — an expensive Claude route reaching Bedrock needs `AWS_*`, and
a cheap Codex route has no business seeing it — use `--cheap-env` or
`--expensive-env` instead, or the per-vendor narrowing you just relied on is
undone by hand.

That narrows variables, not the filesystem. The CLI finds its own credentials
under `HOME`, so `~/.aws/credentials` stays readable however short the variable
list is.

`--cheap-home <dir>` and `--expensive-home <dir>` point each arm's child at a
HOME you prepared yourself — copy the vendor's auth directory in once, and the
CLI authenticates while `~/.aws` and `~/.ssh` are simply not there.

**They are two flags on purpose.** A single shared HOME is writable by the child,
and what a child writes there is not only configuration: a `.bashrc`, a CLI hook,
a `settings.json` that names a command to run. The cheap route is the one you
expect to misbehave, and the expensive route is what grades it. One directory for
both lets the first plant code inside the second. You can still pass the same
path twice — but then it is visible in the command line rather than implied by a
default, and the run says so.

Whatever the child leaves in either directory also survives into the next task.
Twenty tasks into one HOME is twenty tasks that can contaminate each other; hand
out a fresh directory per run if that matters for your workload.

The runner deliberately does **not** build that directory for you. An earlier
version did, and the copying logic produced a new security defect in each of
five consecutive review rounds: credential copies left on disk, a staged HOME
shared between the cheap and expensive routes so the first could poison the
second, `..` traversal in the entry names, partial copies leaking when a copy
failed midway, and cleanup that followed symlinks and chmod'd files outside the
tree. Convenience code around secrets earns its complexity back in defects. You
can stage a directory correctly once by hand; a tool doing it on every run has
five ways to get it wrong.

None of this is a sandbox, and two specific holes are worth naming rather than
leaving to the word "sandbox".

**A detached child outlives the run.** The vendor CLI starts in its own process
group and a timeout kills that group, but a grandchild that calls `setsid` leaves
the group and survives. The runner then builds the "untouched" clone and runs the
verify command on the same host, so a surviving process can watch for those
directories and edit them between the copy and the check. Nothing in a
single-host harness closes that.

**The verify command runs the agent's code with your privileges.** That is the
step's whole purpose, so it cannot be avoided. Clearing the environment and
pointing `HOME` at a scratch directory does not stop code from finding your real
home through the password database or opening `/Users/you/.ssh/id_rsa` by
absolute path.

Against genuinely untrusted output, run the whole thing in a container. The
runner deliberately does not try to build that jail, because a half-built sandbox
invites more trust than it earns.

### 5. Read the answer

```sh
python3 -m weightclass.advisory.speculative_report --log ~/spec-runs/runs.jsonl
```

That legacy form is descriptive. Evidence intended to move the product boundary
must first seal routes, verifier bytes, pricing basis, advisory shape, and sample
bounds with [`advisory-campaign.md`](advisory-campaign.md), pass the manifest to
each runner invocation, and report with the same manifest. Campaign mode treats
a damaged or mixed log as invalid instead of skipping rows.

When both arms recorded a cost, `c` is **measured from your own tasks** rather
than assumed. It is the average cheap cost per task divided by the average
expensive cost per escalated task — not a paired per-task ratio. An earlier
version of this report did use a paired ratio, and it was wrong for the formula
it fed: `c + p` treats `c` as a cost ratio over *all* tasks, and the escalated
tasks are the subset where the cheap route failed.

Two things about that number are easy to over-read, and the report now says both
out loud rather than leaving them to the reader:

- **Its numerator is a full census; its denominator is not.** `c + p` needs
  cheap-cost-over-all-tasks divided by expensive-cost-over-all-tasks. The cheap
  route ran on every task, so the numerator is measured outright. The expensive
  route ran only where the cheap one failed, so the denominator is the escalated
  tasks' average standing in for every task. **That imputation is the assumption
  that remains,** and the report says so on its own line rather than burying it.
  The paired escalated-only ratio is still printed as a secondary figure — task
  difficulty cancels in it — but it is not the quantity the formula asks for, and
  early versions of this report used it as if it were.
- **The denominator comes from very few tasks.** Twenty tasks at a 20% failure
  rate is four escalations. The report recomputes `c` with each **task** left out
  in turn — whole tasks, because an escalated task contributes to both sides and
  deleting only its expensive cost would throw away the numerator's variability —
  and reports a proper jackknife interval. That is a standard error, not the
  spread of the leave-one-out values; the spread shrinks as the sample grows and
  would make you *more* confident with less evidence. The t multiplier is chosen
  from the **effective** sample, meaning the count of priced escalations rather
  than the task count, so twenty tasks with three escalations still gets the
  three-observation multiplier. The savings interval then varies `c` and `p`
  together. Expect it to be wide; on four escalations it usually crosses
  break-even, and "not yet" is the correct answer there.

`c` is only measured when **every cost in the log comes from one source.** A
vendor-reported bill and a price-table conversion count different things, so an
average mixing them is an average of nothing. If the log mixes them — one arm on
Claude and the other on Codex, or a `--prices` table that covers only one arm —
the report says so, names the sources it saw, and falls back to the assumed
value rather than dividing them.

The decision:

**A timed-out run suspends the verdict.** Its cost is not merely unknown, it is
unbounded upward — that run burned the whole budget, so it is probably the most
expensive one in the sample, and leaving it out can reverse the conclusion by
itself. The report prints the numbers and then declines to call it. Rerun the
task with a longer `CHILD_TIMEOUT`, or drop it and collect more.

| result | reading |
| --- | --- |
| interval entirely below break-even | cheap-first pays on this workload |
| interval crosses break-even | not enough tasks yet |
| interval entirely above | the idea is dead for this workload |

Break-even is `1 - c`. At `c = 0.31` the cheap route can fail two times in three
and still not lose money, which is why this is worth measuring rather than
guessing.

## What this still does not buy

Verification catches catastrophes: a broken test suite, a deleted source file, a
credential in the tree. It does not catch a cheaper model accepting `True` as a
schema version, admitting a malformed identifier, or handing back a mutable
internal list — all of which happened in the tier comparison and all of which
passed their tests. See `QUALITY-RESULT.md` in the study repository.

So this mode buys **safety**, not **quality**. On a codebase where a subtly
worse implementation is expensive, that trade may not be the one you want, and
no value of `p` changes that.
