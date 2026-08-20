# Claude and Codex advisory campaign profiles

_Status: repository-only measurement configuration. These profiles do not enable advisory in the
installed `wclass` command._

The Shape-B path is the same for both vendors:

```text
cheap executor -> verify -> advisor -> fresh cheap retry -> verify -> expensive fallback
```

Keep Claude and Codex in separate sealed campaigns. Their token accounting, authentication,
billing origin, and output formats differ, so combining their rows would make the cost endpoint
uninterpretable.

## Task-free route profiles

A route profile contains only the exact model and effort labels selected by the operator. The
labels are opaque configuration: weightclass does not infer availability, entitlement, price, or
relative quality. The compiler supplies a reviewed CLI shape and sends every task through standard
input.

Claude example:

```json
{
  "schema_version": 1,
  "vendor": "claude",
  "models": {
    "cheap": "claude-sonnet-5",
    "advisor": "claude-opus-5",
    "expensive": "claude-opus-5"
  },
  "efforts": {
    "cheap": "high",
    "advisor": "high",
    "expensive": "high"
  }
}
```

Codex example:

```json
{
  "schema_version": 1,
  "vendor": "codex",
  "models": {
    "cheap": "gpt-5.6-luna",
    "advisor": "gpt-5.6-sol",
    "expensive": "gpt-5.6-sol"
  },
  "efforts": {
    "cheap": "high",
    "advisor": "high",
    "expensive": "high"
  }
}
```

Use full model names rather than moving aliases. Keep effort equal in the first campaign so the
measured treatment is model grade; test a lower cheap effort only in a new sealed campaign.

The profile reader is bounded, duplicate-key-safe, nonblocking, and rejects symlinks, special
files, option-like labels, invisible characters, and non-ASCII whitespace. Review the exact argv
before sealing:

```sh
python3 tools/advisory_routes.py review --profile ./claude-profile.json
python3 tools/advisory_routes.py review --profile ./codex-profile.json
```

Claude executors run with safe mode, no session persistence, JSON output, the edit-capable tool
subset, and `acceptEdits`; its advisor runs in `plan` mode with only Read, Glob, and Grep. Codex
routes are ephemeral, ignore user configuration and exec-policy rules, emit JSONL, and use a
workspace-write sandbox for executors and a read-only sandbox for the advisor. Authentication
remains owned by each CLI. The runner itself does not read or copy credential files, but a child
can still read files exposed by its HOME and sandbox. Use separate `--cheap-home`,
`--advisor-home`, and `--expensive-home` directories when the operator has staged the minimum
vendor authentication there; never point one arm at another arm's HOME.

## Seal one campaign per vendor

Use owner-only directories outside a repository for profiles, prices, task files, and results.
The same profile must be supplied when sealing and running; the manifest binds the generated argv
digests.

```sh
python3 tools/advisory_campaign.py \
  --arm shape_b \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --route-profile ./claude-profile.json \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./claude-prices.json \
  --output ./claude-shape-b.json

python3 tools/advisory_campaign.py \
  --arm shape_b \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --route-profile ./codex-profile.json \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./codex-prices.json \
  --output ./codex-shape-b.json
```

Use one current, reviewed pricing origin within an entire campaign. Do not mix API rates, vendor
reported dollars, subscription allowances, or credits. Claude API-key runs can instead use
`--cost-basis vendor` without a prices file when every arm reports a complete cost from that same
origin.

For Codex, `input_tokens` includes its cached-input breakout. A price table can use the derived
`uncached_input_tokens` field together with `cached_input_tokens` and `output_tokens`; the runner
calculates the disjoint uncached count and refuses an impossible negative partition. Do not price
both `input_tokens` and those two components. The CLI also reports `cache_write_input_tokens`, but
its overlap relationship was not established by the observed zero-write probe. Price that field
only when the chosen billing origin establishes that it is a separate line item.
If the selected provider has short/long-context or service-tier rates that the CLI usage cannot
distinguish, seal a conservative table and describe that limitation; do not call the result an
invoice-equivalent cost.

Campaign sealing rejects known overlapping pairs (`input_tokens` with either derived/cached input,
and `output_tokens` with reasoning output) instead of relying on this prose at decision time.

## Run and report

The explicit confirmation acknowledges that task text, a failed diff, and a redacted verification
excerpt can leave the machine for the selected provider. It is required for route profiles and is
checked before the task file is read.

```sh
python3 tools/speculative_run.py \
  --repo /path/to/clean/repo \
  --task-file /private/path/task.txt \
  --route-profile ./claude-profile.json \
  --confirm-task-egress \
  --advise-on-failure \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./claude-prices.json --prefer-prices \
  --campaign ./claude-shape-b.json \
  --sample-ordinal 1 \
  --out-dir /private/path/claude-results
```

Use the equivalent Codex profile, manifest, price table, ordinal sequence, and a different output
directory for the Codex arm. Run one task at a time. The log contains aggregate attempt outcomes,
duration, and usage but never task text, advice, model output, repository path, or task hash.

```sh
python3 tools/speculative_report.py \
  --log /private/path/claude-results/runs.jsonl \
  --campaign ./claude-shape-b.json
```

The gate remains the one in [the advisory campaign contract](advisory-campaign.md): at least 60
usable tasks and 12 advised failures, then a decisive complete interval or the sealed maximum of
150 tasks. A separate blind patch-quality review, clean security review, zero new critical
failures, and macOS/Linux compatibility are also required. Until those conditions pass,
`wclass run` remains single-child and advisory remains outside the distributed package.
