# Delegation qualification oracle inventory

## Status

This document defines the test-only claim inventory that precedes any
qualification-v3 implementation. It does not define qualification evidence,
activate a new suite revision, or make any Claude or Codex runtime eligible.

The machine-readable inventory is
`tests/fixtures/delegation_claim_map_v3.json`. It reconciles the current 54
permission cases and 13 scenarios with both production catalogs while marking
every claim `blocked`. The file is outside the package and is rejected by both
the v2 evidence validator and registry loader.

## Observation classes

A future oracle must classify each decisive fact into one of these boundaries:

- **Runner-direct fact:** the runner selected fixed argv, created a pipe or file
  descriptor, observed a child PID or exit status, enforced a deadline, or
  received bytes on a handle it owns.
- **Runtime-reported telemetry:** a runtime reports a role, stage, action,
  review decision, artifact relationship, or success event. This is never
  qualification evidence by itself, even when typed and bounded.
- **Externally enforced effect:** an OS sandbox or supervisor independently
  allows, denies, attributes, or contains the effect. This requires an
  authoritative boundary that the evaluated runtime cannot forge or escape.
- **Vendor-interface fact:** an exact reviewed native CLI version exposes a
  documented, independently observable property. Lack of such an interface is
  a vendor-specific no-go, not permission to trust narrative output.

Workspace markers alone are not authentic invocation evidence. A same-user
runtime or driver can reproduce ordinary files and event sequences unless a
separate isolation boundary prevents it.

## Claim-map contract

The JSON object has exact top-level fields for its schema version, current v2
suite revision, bounded resource profiles, permission cases, and scenario
cases. Every case names:

- the evaluated subject;
- fixed fixture, setup, and stimulus identifiers;
- an oracle identifier and expected observation;
- a negative control;
- identity and platform requirements;
- resource and cleanup contracts;
- evidence projection, feasibility, and a stable blocking reason.

All values are identifiers or bounded integers rather than free-form runtime
output. Task content, task hashes, prompts, responses, transcripts,
credentials, runtime paths, driver paths, vendor output, and `passed` fields
are forbidden.

The current map intentionally uses `no-independent-oracle-v1` and
`none-claim-blocked-v1` for every row. The permission matrix remains blocked
because a filesystem or command effect does not independently establish the
claimed role, category, and action attribution. Scenario-specific blockers
distinguish runtime telemetry, launch identity, and OS-supervisor gaps.

## Protocol-v2 indistinguishability regression

The test-only v2 driver exercises four fixed modes for one fixed permission
case: invoke the fixed sentinel runtime, skip it, forge the sentinel's constant
marker, or self-attest without invoking it. The outside test observes only
`marker_present` or `marker_absent`; it does not call either marker authentic.

All four modes produce the same complete v2 evidence and qualification
candidate even though the marker observations differ. This demonstrates only
that protocol v2 does not encode an independent runtime-invocation
observation. It does not allege driver or vendor misconduct, and neither the
sentinel nor fake driver may support a package qualification record.

## Eligibility boundary

The claim map is neither evidence nor a registry candidate. It cannot change
`CURRENT_SUITE_REVISION`, route fingerprints, public CLI behavior, or the empty
package registry. A future claim becomes eligible only through a separately
reviewed protocol revision with an implemented independent oracle and negative
control.

The next safe implementation increment is a non-public synthetic probe kernel.
Probe self-tests must use separate IDs rather than weakening any of the 67
qualification claims. Initially they may establish only runner-direct facts.
Runtime telemetry remains untrusted, and path-based execution must be reported
as `TOCTOU-UNRESOLVED` until verified-object or reviewed immutable launch
binding exists.

No vendor CLI, credentials, network access, billing event, production runtime,
qualification record, or delegation advertisement belongs in that increment.
