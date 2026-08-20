# Advisory productization campaign

_Status: measurement gate implemented; advisory execution remains repository-only._

The advisory runner already measures Shape A and Shape B, but an ordinary JSONL
log cannot prove that its verifier, prices, routes, stopping rule, or advisory
context stayed fixed. A product decision must use a sealed campaign manifest.
The manifest is task-free: it contains no task text, task hash, repository path,
timestamp, account/profile, credential, or full command.

## Fixed decision contract

- Primary endpoint: cost per passing task.
- Shape B rule: the complete rescue-rate interval must exceed the complete
  `a_B + q*r` interval.
- Shape A+B rule: paired baseline and advised campaigns are required; no
  unpaired or mixed-configuration extrapolation is accepted.
- At least 12 advised failures are required. This threshold existed before the
  current `0/2` result and must not be tuned after seeing more data.
- Both the planned task count and failure minimum must be met before a decision.
- Collection stops only after a decisive interval once both minimums are met,
  or at the sealed maximum. Reaching the maximum without both minimums is not a
  promotion result.
- Any timeout, incomplete pricing, mixed cost origin, duplicate sample ordinal,
  damaged campaign log, or contract mismatch blocks the verdict.

## Seal the campaign before reading a task

Use one manifest and output directory per arm. The example labels below are
opaque caller configuration; weightclass does not assert availability or price.

```sh
python3 tools/advisory_campaign.py \
  --arm shape_b \
  --planned-tasks 60 \
  --max-tasks 150 \
  --cost-basis price_table \
  --cheap 'codex exec --model REVIEWED_CHEAP_MODEL -' \
  --expensive 'codex exec --model REVIEWED_STRONG_MODEL -' \
  --advisor 'codex exec --model REVIEWED_ADVISOR_MODEL -' \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./prices.json \
  --output ./shape-b-campaign.json
```

`price_table` requires exactly `cheap`, `expensive`, and `advisor` tables. The
file is bounded, duplicate-key-safe, finite, and non-negative. Its raw bytes,
the verifier bytes, and all three complete argv arrays are SHA-256-bound into
the manifest. Only executable basenames and digests are retained; commands and
rates are not copied into the manifest.

Use `--cost-basis vendor` without `--prices` only when every arm reports a
complete cost from the same vendor-defined origin. Do not mix a vendor bill
with price-table conversion.

## Record one representative task

Campaign mode rejects `--label`; the only persisted task handle is an opaque
ordinal within the sealed maximum. It is not a task name or content hash.

```sh
python3 tools/speculative_run.py \
  --repo /path/to/clean/repo \
  --task-file /private/path/current-task.txt \
  --cheap 'codex exec --model REVIEWED_CHEAP_MODEL -' \
  --expensive 'codex exec --model REVIEWED_STRONG_MODEL -' \
  --advisor 'codex exec --model REVIEWED_ADVISOR_MODEL -' \
  --advise-on-failure \
  --advisor-context prompt \
  --verify ./verify.sh \
  --prices ./prices.json --prefer-prices \
  --campaign ./shape-b-campaign.json \
  --sample-ordinal 1 \
  --out-dir /private/path/shape-b-results
```

The runner obtains a nonblocking campaign lock, pins one canonical copy of the
manifest, requires the next contiguous ordinal, and validates every task-free
input before reading `--task-file`. An unbound legacy invocation cannot append
to that output directory. Every campaign input is opened once with no-follow,
validated as a regular file through `fstat`, and read through that descriptor.
Descriptor-bound verifier and price-table bytes are staged into the owner-only
output directory; verification and pricing use those private copies rather than
reopening the caller paths. The JSONL record contains the campaign fingerprint,
arm, and ordinal but never task/advice/output text. Run only one task at a time.

## Report and promotion gate

```sh
python3 tools/speculative_report.py \
  --log /private/path/shape-b-results/runs.jsonl \
  --campaign ./shape-b-campaign.json
```

Legacy logs may skip a damaged trailing line for descriptive recovery. A sealed
campaign cannot: malformed rows, duplicate JSON keys, missing bindings, repeated
ordinals, mixed fingerprints, or out-of-range ordinals fail closed. Even a
statistically decisive point estimate is withheld until the sealed task and
failure minimums are both met.

The campaign receipt is necessary but not sufficient for productization. A
promotion also requires independent blind quality review, zero new critical
failures, zero task/credential persistence or workspace escapes, complete
macOS/Linux compatibility, and a clean security review.

## Product boundary after a passing campaign

Do not add retries to `wclass run`. A passing Shape B campaign authorizes only
a separate explicit surface such as `wclass advisory review|run|prune`, with:

- exact cheap/advisor/escalation/verifier fingerprints;
- explicit task-egress confirmation;
- one bounded advised retry;
- disposable clones and patch-only handoff;
- no automatic patch application;
- aggregate-only router state and no task-level product log;
- advisory disabled unless the caller selects it.

Shape A remains a separate decision. Shape B can pass without proving that an
up-front plan improves subtle quality, and an A+B campaign cannot borrow the
unprimed costs or rates from a Shape-B-only log.
