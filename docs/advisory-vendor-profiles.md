# Advisory campaign vendor profiles

Read-only `review`, `research`, `diagnosis`, and `design` workflows compile the same
profile with read-only executor authority; see
[Read-only advisory evidence workflows](advisory-evidence-workflows.md).

_Status: installed experimental measurement configuration. These profiles do not enable advisory
in the core `wclass` command._

The Shape-B path is the same for every configured vendor:

```text
cheap executor -> verify -> advisor -> fresh cheap retry -> verify -> expensive fallback
```

Keep every vendor in a separate sealed campaign. Token accounting, authentication, billing origin,
and output formats differ, so combining rows would make the cost endpoint uninterpretable.

## Task-free route profiles

A schema-1 route profile contains only the exact model and effort labels selected by the operator;
schema 2 contains the operator's exact command matrices. Labels are opaque configuration:
weightclass does not infer availability, entitlement, price, or relative quality. The compiler
supplies or validates a reviewed CLI shape and sends every task through standard input unless the
reviewed command contains the exact argv or private-file placeholder described below.

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
wclass-advisory review --profile ./claude-profile.json
wclass-advisory review --profile ./codex-profile.json
```

Claude implementation executors run with safe mode, no session persistence, JSON output, the
edit-capable tool subset, and `acceptEdits`; its advisor runs in `plan` mode with only Read, Glob,
and Grep. Claude evidence executors use `dontAsk`, the same read-only tool subset, and a task-free
JSON Schema so the final envelope contains structured output rather than a plan artifact. Codex
routes are ephemeral, ignore user configuration and exec-policy rules, emit JSONL, and use a
workspace-write sandbox for executors and a read-only sandbox for the advisor. Authentication
remains owned by each CLI. The runner itself does not read or copy credential files, but a child
can still read files exposed by its HOME and sandbox. Use separate `--cheap-home`,
`--advisor-home`, and `--expensive-home` directories when the operator has staged the minimum
vendor authentication there; never point one arm at another arm's HOME.

### Built-in agy and Grok profiles

Schema 1 also accepts `agy` and `grok`. Their model and effort labels remain opaque, user-selected
configuration; this repository does not infer model quality, availability, entitlement, price, or
subscription usage, and it does not create active measurement profiles by guessing those values.

The reviewed agy route places the exact `{{task}}` slot in argv. Its review output reports
`"task_delivery": "argv"` and `"task_process_exposure": true`; local process inspection can see
the task while the child runs. The executor uses `--mode accept-edits`, while advisor and evidence
executors use `--mode plan`. Agy 1.1.21 rejects the advertised `--effort` flag for configured
models and disables plan mode when slash expansion is disabled, so read-only routes omit both
`--effort` and `--disable-slash-commands`. Cheap and expensive evidence executors use the selected
workflow JSON Schema; the prose advisor remains schema-free.

The reviewed Grok route uses `--prompt-file {{task_file}}`. The runner creates an owner-only,
transient file outside the Git workspace immediately before spawn and deletes it after success,
failure, timeout, or a child start error. Keeping it outside the clone prevents a child that runs
`git add -A` from staging task bytes before cleanup. Grok review therefore reports
`"task_delivery": "file"` and no argv exposure. The route also disables subagents, web search,
and implicit prompt rewriting so the reviewed command does not silently widen the measurement
surface.
Cheap and expensive Grok evidence executors use the selected workflow JSON Schema; the prose
advisor remains schema-free.

agy JSON usage exposes `input_tokens`, `output_tokens`, `thinking_tokens`, and
`cache_read_tokens`. Grok JSON exposes its own vendor-reported `total_cost_usd` plus
`input_tokens`, `output_tokens`, `cache_read_input_tokens`, and
`cache_creation_input_tokens`. The runner accepts those envelopes only when the executable basename
identifies the corresponding vendor. A user price table may name the reported fields; do not infer
rates or translate token fields across vendors. Structured stdout from an unknown executable is
model-controlled text and never becomes usage or cost evidence.

### Schema 2 for arbitrary vendors

Schema 2 is for a vendor whose command shape is supplied and reviewed by the operator. It is
closed and bounded: the top-level keys are exactly `schema_version`, `vendor`, and `commands`,
where `commands` contains exactly `implementation` and `evidence`, and each workflow contains
exactly `cheap`, `advisor`, and `expensive` command arrays.

```json
{
  "schema_version": 2,
  "vendor": "acme-cli",
  "commands": {
    "implementation": {
      "cheap": ["acme", "--prompt-file", "{{task_file}}"],
      "advisor": ["acme", "--prompt-file", "{{task_file}}"],
      "expensive": ["acme", "--prompt-file", "{{task_file}}"]
    },
    "evidence": {
      "cheap": ["acme", "--read-only", "{{task}}"],
      "advisor": ["acme", "--read-only", "{{task}}"],
      "expensive": ["acme", "--read-only", "{{task}}"]
    }
  }
}
```

Each command may contain zero or one exact `{{task}}` or `{{task_file}}` token. A token cannot be
the executable or embedded in another argument; duplicate keys, unknown keys, unbounded commands,
and multiple delivery tokens fail closed. Zero tokens means stdin. `{{task}}` means argv and carries
the documented process-inspection exposure. `{{task_file}}` means the same private transient-file
delivery used by Grok. Review reports one delivery string when all roles agree, or a per-role map
when they do not, plus whether any selected role enters argv.

An executable whose basename is not a built-in vendor receives no vendor-prefixed credentials by
default. Add only the exact required environment variable names with `--cheap-env`,
`--advisor-env`, or `--expensive-env`; do not use `--child-env-all` merely to make an unknown CLI
authenticate, because that hands the entire process environment to every arm.

Keep every vendor and workflow in its own sealed campaign. The manifest binds the exact command
matrices after compilation, while existing schema-1 Claude/Codex campaign bytes and fingerprints
remain compatible.

## Seal one campaign per vendor

Use owner-only directories outside a repository for profiles, prices, task files, and results.
The same profile must be supplied when sealing and running; the manifest binds the generated argv
digests.

```sh
wclass-advisory seal \
  --arm shape_b \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --route-profile ./claude-profile.json \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./claude-prices.json \
  --output ./claude-shape-b.json

wclass-advisory seal \
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
wclass-advisory run \
  --campaign-root /private/path/claude-results \
  --vendor claude \
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
```

Use the equivalent Codex profile, manifest, price table, ordinal sequence, and a different output
directory for the Codex arm. Run one task at a time. The log contains aggregate attempt outcomes,
duration, and usage but never task text, advice, model output, repository path, or task hash.

```sh
wclass-advisory report \
  --log /private/path/claude-results/runs.jsonl \
  --campaign ./claude-shape-b.json
```

The gate remains the one in [the advisory campaign contract](advisory-campaign.md): at least 60
usable tasks and 12 advised failures, then a decisive complete interval or the sealed maximum of
150 tasks. A separate blind patch-quality review, clean security review, zero new critical
failures, and macOS/Linux compatibility are also required. Until those conditions pass,
`wclass run` remains single-child and advisory remains an explicitly selected companion command.
