# Prospective verifiers — `.weightclass/`

Scope: the executable verifiers in this directory. The root
[`AGENTS.md`](../AGENTS.md) still applies.

## What these files are

These are **pre-registered prospective verifiers**. Each one was committed
*before* the work it gates, so that the acceptance criteria could not be shaped
by whatever a candidate happened to produce. Their value comes entirely from
having been written first.

- `verify` — the repository gate. It refuses to run when the protected
  acceptance files are staged as changed, then runs the full suite and
  `compileall`.
- `verify-review` — the pre-registered factual gate for the advisory
  failure-receipt review.
- `verify-design` — the pre-registered gate for the design workflow.

`verify` protects exactly `.weightclass/verify` and
`tests/test_advisory_hardening_batch.py`. Do not stage a change to either while
using it as a gate.

## The rule

**Never edit a verifier to make a candidate pass.** If a result fails, the
result is what changes. Relaxing a seed, widening an accepted enum, or lowering
a required count destroys the evidence the file exists to produce, and it does
so silently — the gate still exits 0.

Editing one after its campaign is complete is possible but is a deliberate,
disclosed act. Say what changed and why in the commit body.

## The line-anchor gotcha

`verify-review` pins seeds as `file:line` and checks that a named symbol appears
within a **±8 line window** around the cited line.

That means editing the cited source file shifts the anchors. Adding lines above
a seed will break it even though nothing about the reviewed behavior changed.

When that happens:

- Re-anchor the seed to the **same symbol** at its new line, so the accepted and
  rejected sets stay identical.
- Verify the new window actually contains the symbol before committing.
- Say in the commit body which seeds moved, from what to what, and why the
  verifier's meaning is unchanged.
- Never widen the window, drop a seed, or relax a required disposition or
  severity to make the shift go away.

## Exit codes

`verify-review` returns `42` for the pre-registered baseline probe — an empty
findings list with `limitations: ["baseline_probe"]`. That is a contract, not a
placeholder: it lets a caller prove the verifier runs and rejects by default
before any real result exists. A non-zero, non-42 exit is a rejection.

## Managed copies

A managed campaign root receives a copied verifier wrapper pinned to the package
version that installed it. `doctor` reports when that copy is stale. Re-run the
explicit `init` parameters after a package upgrade rather than editing the
copy in place.
