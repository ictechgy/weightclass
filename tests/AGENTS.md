# Tests — `tests/`

Scope: the test suite and the tools that audit it. The root
[`AGENTS.md`](../AGENTS.md) still applies.

## Which runner is the gate

**The gate is `./.weightclass/verify`. `pytest -q` is not the gate.**

`pytest` is CI's runner. Running it alone is how a whole test module once
reached `main` in a form the release workflow could not execute, and how `mypy`
errors in `tests/` went unseen for a full review cycle.

Before reporting completion, run all of:

```sh
./.weightclass/verify                      # unittest discovery + compileall
uvx --offline ruff==0.16.2 check .
uvx --offline ruff==0.16.2 format --check .
uvx --offline mypy==2.3.0 --strict src tests
git diff --check
```

`mypy --strict` covers `tests` as well as `src`. A test helper needs real type
arguments; `dict` without parameters fails the gate.

`unittest` discovery runs with `-W error::ResourceWarning`, so a leaked file or
subprocess handle in a test is a failure, not a warning.

## What the runner does not give you

Two failures reached CI green-on-macOS, red-on-Linux because a test assumed
something about the machine it ran on. Both are cheap to avoid.

- **Never assume a vendor CLI is installed.** `route --source-vendor codex`
  needs the real executable to pass admission, so it works on a developer
  machine and exits `3` `unsupported_route` on a runner. Supply the route from a
  temp policy file with a fake command, the way the rest of the suite does.
- **Do not spawn interpreters you do not need.** The suite contains timing
  assertions that share the runner, and
  `test_redaction_is_fast_on_hostile_input` went 16.55 s against its 15 s bound
  because a new module spawned thirteen subprocesses. Prefer in-process
  `cli.main()` with redirected streams. Raising someone else's limit to make
  room for your load is the wrong fix.

## What a new test has to prove

- Add focused tests with each behavior change. A change with no test that fails
  without it is not finished.
- Write the test RED first, then make it GREEN. If the test passed before the
  change, it is not testing the change.
- Prove a new guard is not vacuous: restore the defect and watch the guard fail.
  A guard that has never failed has never been shown to guard anything.
- Pin the contract, not the current output. Assert the property the code
  promises so a legitimate refactor does not have to edit the assertion.
- Write a deliberate limit into a named test rather than tuning a knob that is
  wrong in both directions.

## Changing an existing assertion

An existing test that fails after your change is a question, not an obstacle.

Answer it explicitly before editing:

- If the test pins the behavior you deliberately changed, rewrite it to pin the
  **new** contract and say so in the commit body. Do not flip an assertion
  silently.
- If a test pinned a defect as if it were a contract, replace it with a test of
  the contract that remains, and add one for the behavior that now exists.
- If you cannot state which contract the test was protecting, stop and find out.

## The vacuity audit

`tools/check_test_vacuity.py` exists because a test here once passed while
checking nothing — its probe string was not in the input — and hid a real key
leak for five rounds. It reruns the suite against a copy of the runner whose
redaction functions are replaced by identity, and lists what still passes.

Passing under identity redaction is not automatically a defect: preservation
tests are supposed to pass. The list is for a human to read.

Do not let a tool that audits the suite use a different runner than the gate.
This tool once counted pytest node IDs, which do not exist for `subTest` cases,
so it silently lost per-parameter resolution and reported leak-direction tests
as passing.

## Protected files

`tests/test_advisory_hardening_batch.py` is protected acceptance and is checked
by `./.weightclass/verify` itself. See
[`../.weightclass/AGENTS.md`](../.weightclass/AGENTS.md) before touching it or
anything in that directory.

## Distribution tests

`verify_distribution_isolation.py` and `test_distribution_isolation.py` check
that the built sdist and wheel work outside this working tree. They are part of
the release gate in [`../packaging/AGENTS.md`](../packaging/AGENTS.md); a change
to packaging metadata is not verified until they pass against a freshly built
distribution.
