# Advisory product roadmap v2

Status: implementation contract for the post-0.18.0 follow-up.

This increment improves the separately invoked `wclass-advisory` companion. It
does not change core classification, select advisory automatically, add a retry
to `wclass run`, inspect credentials, or infer provider quality, entitlement,
pricing, recipient, or billing state.

## 1. Filtered managed status

`wclass-advisory status` accepts the same task-free `--vendor` and `--workflow`
selectors as `doctor` and `cleanup`. Defaults remain `all`. Filtering changes
only which independently validated populations are rendered; it never merges
or rewrites records.

## 2. Provenance-bound campaign gate

Unbound `wclass-advisory experiment sequential --records ...` is descriptive.
It may report `signal_above_target`, but always returns
`promotion_eligible:false`, `policy_decision_allowed:false`, and
`evidence_origin:caller_jsonl`.

The new managed `campaign-gate` command selects exactly one configured vendor,
workflow, and metric. It validates the sealed manifest and every anonymous lane
through the existing binding loader, derives boolean outcomes transiently, and
applies the same simultaneous confidence sequence. Its three metrics are:

- `cheap_acceptance`: whether the cheap attempt was accepted;
- `advised_rescue`: retry acceptance among records that reached failure advice;
- `final_acceptance`: acceptance by cheap, retry, or expensive.

The command emits no manifest path, result path, task identifier, timestamp, or
fingerprint. `eligible_for_human_review` requires both the sealed campaign's
60-task/12-advised-failure gate and an above-target statistical signal. Even
then, `policy_decision_allowed` and `core_routing_changed` remain false.

## 3. Closed consult failure contract

The internal consult runner emits one fixed `advisory_consult_failed` receipt
when it cannot return an accepted result. Stages and reason codes are closed
enums. The managed parent parses and reconstructs that receipt and never replays
arbitrary internal stderr. Attempt, provider, verifier, exception, task, command,
profile, and pathname text are unavailable at this boundary.

## 4. Consult deadline and completion-order output

Campaign jobs retain the existing eight-hour outer ceiling. One-shot consults
default to 5,400 seconds and accept an explicit value from 1 through 28,800
seconds. Each selected vendor receives the same reviewed ceiling before task
access.

The parallel coordinator keeps its input-ordered return value for compatibility,
but adds a completion callback. Managed consult uses it to validate and emit
each tagged result immediately when that vendor finishes. Output is therefore
NDJSON in completion order. A peer is not cancelled automatically: cancellation
could discard paid work or corrupt campaign semantics.

## 5. Custom-provider dispatch conformance

Built-in dispatch behavior is unchanged. A schema-2 custom vendor dispatch
requires `--confirm-provider-egress` in addition to task-egress confirmation and
runs the existing three-role, task-free provider check before task-file metadata
is inspected. Failure records no sample and prevents task access.

Route review labels custom containment honestly: exact argv is reviewed and
repository changes are rejected after execution, but host filesystem isolation
is false. The conformance check establishes CLI/provider result compatibility;
it does not verify recipient, billing, entitlement, or containment.

## Deferred boundaries

- Host-wide admission control remains measurement-gated. First collect a
  10-invocation benchmark of active children and allocator p50/p95/p99.
- Descriptor-bound executable launch remains deferred until native binaries,
  scripts, process status, signal handling, and cleanup pass on every supported
  platform.
- Decomposing the redaction-heavy speculative runner is not part of this
  increment because its vacuity anchors must not move without a dedicated audit.
