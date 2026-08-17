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

**Vendor comparison becomes meaningful.** Token counts are not comparable across
vendors: Claude sums cache reads into its usage, Codex reports an opaque total.
Dollars are the same unit everywhere. The vendor question that could not be
answered on subscriptions can be answered here.

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
for path in pathlib.Path(".").rglob("*"):
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

A field the table prices but a particular run does not report counts as zero:
a run that touched no cache genuinely has no cached tokens. It is only when
*none* of the priced names appear anywhere in the run's breakdown that the whole
table is treated as a typo and produces no cost at all — better than a partial
number that looks authoritative. Top-level keys other than `cheap` and
`expensive` are rejected outright, because a misspelled arm would otherwise
leave that side unpriced with no complaint.

Claude needs none of this — it reports its own cost, and a vendor-reported
number always wins over the table, which can go stale.

### 4. Run it

Commit your work first; uncommitted changes are not in the clone and the runner
refuses to start rather than silently measure something else.

```sh
# Codex, needs the price table and --json for structured usage
tools/speculative_run.py \
  --repo ~/work/service --task-file task.txt \
  --cheap    'codex exec --sandbox workspace-write --json -c model=<cheap> -' \
  --expensive 'codex exec --sandbox workspace-write --json -c model=<expensive> -' \
  --verify ./verify.sh --prices prices.json --out-dir ~/spec-runs

# Claude, reports its own cost
tools/speculative_run.py \
  --repo ~/work/service --task-file task.txt \
  --cheap    'claude --print --output-format json --model sonnet --permission-mode acceptEdits' \
  --expensive 'claude --print --output-format json --model opus   --permission-mode acceptEdits' \
  --verify ./verify.sh --out-dir ~/spec-runs
```

One task per invocation. Around twenty real tasks gives a usable interval.

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

That narrows variables, not the filesystem. The CLI finds its own credentials
under `HOME`, so `~/.aws/credentials` stays readable however short the variable
list is.

`--child-home-stage .codex` closes most of that gap without any setup: the run
builds a throwaway `HOME`, copies only the names you list into it, and points the
child there. The CLI still finds its own auth; `~/.aws` and `~/.ssh` are simply
not present. Copies, not symlinks — a link would let the child's writes flow back
into your real home. Add `--child-home-stage .gitconfig` if the agent needs it.

This is not a sandbox and the runner does not claim to be one. Nothing stops
code from opening `/Users/you/.ssh/id_rsa` by absolute path. Against genuinely
untrusted output, run the whole thing in a container.

### 5. Read the answer

```sh
tools/speculative_report.py --log ~/spec-runs/runs.jsonl
```

When both arms recorded a cost, `c` is **measured from your own tasks** rather
than assumed: on every escalated task the same task ran through both models, so
the ratio is paired and task difficulty cancels out. The report says so
explicitly, and warns when it had to fall back to the assumed value.

The decision:

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
