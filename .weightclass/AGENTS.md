# Prospective verifiers — `.weightclass/`

Scope: the executable verifiers in this directory. The root
[`AGENTS.md`](../AGENTS.md) still applies.

## What these files are

These are **pre-registered prospective verifiers**. Each one was committed
*before* the work it gates, so that the acceptance criteria could not be shaped
by whatever a candidate happened to produce. Their value comes entirely from
having been written first.

- `verify` — the repository gate. It refuses to run when it is itself staged as
  changed, then runs the full suite and `compileall`.

`verify` protects exactly `.weightclass/verify`. Do not stage a change to it
while using it as a gate.

## The rule

**Never edit a verifier to make a candidate pass.** If a result fails, the
result is what changes. Relaxing a seed, widening an accepted enum, or lowering
a required count destroys the evidence the file exists to produce, and it does
so silently — the gate still exits 0.

Editing one after its campaign is complete is possible but is a deliberate,
disclosed act. Say what changed and why in the commit body.
