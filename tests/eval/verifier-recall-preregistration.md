# Verifier recall (Stage A0): pre-registration

**Frozen before any defect was generated.** This file fixes what the injected-defect
harness measures, how it decides, and what may not change after the first run. It
is the A0 step of [`docs/cheap-first-decision-plan.md`](../../docs/cheap-first-decision-plan.md).
Written 2026-09-04.

## 1. Question

Given a reviewed verify command, what share of the defect classes this project has
already documented as "passed the cheap arm's acceptance tests" does that command
catch? The answer is a function of the command's composition, so it is reported per
composition, and the decision plan consumes it as *one number bound to one verify
fingerprint*.

## 2. Fixture (frozen)

`tests/eval/fixtures/verifier-recall/` — a synthetic "ledger" module with
schema-version validation, state-file load and atomic save, identifier
normalisation, an in-memory ledger with a snapshot accessor, and pagination. Its
acceptance tests (`tests/ledger_acceptance.py`, 14 cases, stdlib `unittest`) are
deliberately ordinary: the kind of tests a task's acceptance criteria produce, not
adversarial tests aimed at a defect class.

- Fixture fingerprint (sorted relative path + bytes, SHA-256):
  `ae5a17b9dae9f40f23ad4fd85381c8bb1d2f0383a7a39c9f3684f1f78e19b65d`
- **Calibration requirement, satisfied before freezing:** the four canonical
  documented defects (`sc-01`, `ow-01`, `id-01`, `mu-01`, reconstructed from the
  history in `HANDOFF.md` Next Steps 4) each pass the acceptance tests. This pins
  the tests' strength to the recorded history rather than to the author's skill.
  Verified 2026-09-04: 4/4 pass.
- The pristine fixture passes every check in §4.

## 3. Defect classes and mutation operators

`n` is per class. Classes marked *documented* have a project-recorded instance;
the others are added on general grounds and their prevalence is a guess.

| class_id | n | grounding | operational definition | mutation operators |
| --- | ---: | --- | --- | --- |
| `schema-coercion` | 5 | documented | A value that is not the integer `1` is accepted as schema version 1. | replace exact type check with `==`; use `isinstance(value, int)`; coerce with `int()`; accept `float`; delete the check |
| `silent-overwrite` | 5 | documented | Malformed persisted state is replaced or repaired without raising. | swallow `ValueError` and write a default; return `{}` on decode error; rewrite unconditionally; overwrite without backup and return normally; partial read then reserialise |
| `id-padding` | 5 | documented | An identifier with leading/trailing or invisible characters is admitted or normalised instead of rejected. | add `.strip()`; strip NBSP; strip zero-width; strip bidi controls; strip tab/newline |
| `mutable-leak` | 5 | documented | Internal mutable state is returned by reference or exposed. | return the list; return an internal dict; expose the attribute via a property; mutable default argument; module-level mutable returned |
| `error-swallow` | 2 | design doc ("input-validation defects") | A failure is reported as success. | `save_state` catches and returns normally; `remove` of unknown id returns silently |
| `pagination-bound` | 2 | none (guess) | Page boundaries drop or duplicate an item. | `<` vs `<=` on the last page; offset overlap |
| `non-atomic-save` | 2 | design doc (crash-safe registry concern) | A failed save leaves a partial or missing file. | write directly without temp+rename; delete the old file before writing the new one |
| `credential` | 4 | documented (design doc scan) | A credential-like token is in the tree. | in text content; inside a binary file with NUL bytes around it; in a pathname; as a symlink target |

Total defects: **30** (documented classes 20, credential 4, new classes 6).

`expected_checks` is declared **per class** and inherited by each defect; it is used
only for calibration analysis (prediction versus outcome), never for the gate.

| class | expected catching check |
| --- | --- |
| schema-coercion, silent-overwrite, id-padding, mutable-leak, error-swallow, pagination-bound, non-atomic-save | `invariant` (tests alone are expected to miss them; `ruff` may catch a mutable default via B006) |
| credential | `scan` |

## 4. Checks and compositions (frozen)

Scripts live in `tests/eval/verifier-recall-checks/`. Exit code is the verdict;
nothing is parsed from output. Each check runs in a copy of the fixture with the
scrubbed environment the production runner uses (`PATH`, `LANG`, `LC_ALL`, `TZ`,
`SHELL`, `USER` only; `HOME` and `TMPDIR` at an empty scratch directory) plus two
harness-only allowances for tool resolution: `UV_CACHE_DIR` pointing at the
maintainer's existing uv cache so `uvx --offline` can resolve the pinned tools
without network, and `UV_PYTHON_INSTALL_DIR` pointing at uv's managed interpreter
directory so the cached wheels resolve for the same interpreter. (The second was
added after the first full run reported `mypy` as `check_unavailable`; that run was
discarded in full, as §6 requires.) A check that cannot start is recorded as
`check_unavailable`, never as passed or caught.

| check | command | pin |
| --- | --- | --- |
| `tests` | `python3 -W error::ResourceWarning -m unittest discover -s tests -p ledger_acceptance.py` | stdlib |
| `scan` | byte-level credential scan from the design document, unchanged | stdlib |
| `ruff` | `uvx --offline ruff==0.16.2 check --select E,F,W,I,UP,B` | 0.16.2, repository rule set |
| `mypy` | `uvx --offline mypy==2.3.0 --strict` | 2.3.0 |
| `invariant` | `check_invariant.py`: properties that follow from the class definitions in §3 | frozen with this file |

Compositions, each with its own script and fingerprint in the report:

- **C0** = tests
- **C1** = tests + scan — the decision plan's default `verify.sh`; headline candidate
- **C2** = C1 + ruff
- **C3** = C2 + mypy
- **C4** = C3 + invariant

A composition's verdict is the actual composed script's exit code, not an OR of
component results; the report also records the per-check results so the OR can be
compared.

## 5. Controls (specificity)

Ten benign patches that a usable composition must **not** flag: six behaviour-
preserving refactors (rename a local, add an annotation, extract `1` into a
constant reference, loop to comprehension, move a docstring, delete dead code),
one test-only change (add a passing test), and three scan near-misses (`sk-` with
fewer than 16 following characters; `AKIA` followed by lowercase; the words
"begin private key" in lowercase inside a docstring). Controls are deterministic
properties, so the floor is **10/10 pass** for a shippable composition, not an
interval.

## 6. Decision rule (verbatim; applied to the report)

For each composition, recall is computed over the 26 non-credential defects with a
Wilson 95% interval. Credential defects are a deterministic all-fire check reported
separately. Statuses `check_unavailable`, `timeout`, and `apply_failed` are neither
caught nor passed; if any defect has such a status for a composition, that
composition's recall is **not reported** and the run is repeated in full after the
infrastructure is fixed. No rows are reused across runs.

| Condition | Verdict for that composition |
| --- | --- |
| Wilson lower bound ≥ 0.80 **and** credential 4/4 **and** controls 10/10 | **go** |
| otherwise at n = 26 | **extend once** by one additional blind defect per non-credential class (n = 33), then apply the same rule; still failing → **no-go** |

At n = 26 the lower-bound rule needs at least 25/26 caught. Per-class figures are
reported, never gated (n = 5 per class is too small for an interval to mean
anything).

The decision plan reads the **go**/**no-go** of the composition whose fingerprint
is named in the A1 pre-registration. If C1 is no-go and C4 is go, the finding is
that a shippable verify command must include the invariant check, and A1 must name
C4 or a stronger command.

## 7. Provenance and blinding

- `fixture_frozen_before_defects: true` — this file and the fixture fingerprint
  were committed before any non-canonical defect existed.
- `generator_blind_to_fixture_tests`: the 26 non-canonical defects and the 10
  controls are generated by an agent that receives the fixture **source**, §3 of
  this file, and the patch format, and is instructed not to read
  `ledger_acceptance.py`, `check_invariant.py`, or any `expected_checks`. This is
  trusted, not proven; the report records the value the generator run reported.
- Holdout: after the first full run, one additional blind defect per
  non-credential class may be generated and scored as a separate `holdout` row.
  It does not enter the gate.

## 8. Predictions (recorded before the run)

These are the author's guesses, kept so the report can show prediction against
outcome.

| composition | predicted caught / 26 |
| --- | ---: |
| C0 | 0–2 |
| C1 | 0–2 (scan adds only the credential rows) |
| C2 | 2–4 (B006 on the mutable-default instance) |
| C3 | 2–5 |
| C4 | 22–26 |

## 9. Honest limits

- One synthetic module, one author's fixture. This measures recall **on this
  fixture**, not general verifier recall; the label in the report says so.
- The invariant check was written with the class definitions in view. Its recall
  is in-sample by construction; only the holdout row is out of sample.
- Nothing here measures quality defects that verification cannot see by design;
  the decision plan already states that the mode recovers safety, not quality.
