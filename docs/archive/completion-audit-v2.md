# Protocol 2 completion audit

This is the current-tree requirement map for the executable model/profile routing
and orchestration protocol 2 brief. RALPLAN: `max_rounds/ITERATE` (not consensus-approved).
“Current” means the cited repository test and the G01–G12 durable checkpoint
were independently verified against this tree.

| Requirement | Files | Tests | Reproduction command | Current evidence | Status |
| --- | --- | --- | --- | --- | --- |
| OBJ-01 | `docs/protocol-v2-specification.md` and schema fixtures | `test_protocol_v2_specification.py`, legacy contract tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_protocol_v2_specification tests.test_legacy_contract` | G01 contract and legacy checkpoint | current |
| OBJ-02 | `v2_validation.py`, `task_v2.py`, `canonical_v2.py`, `executable_observation.py` | four matching focused modules | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_v2_validation tests.test_task_v2 tests.test_canonical_v2 tests.test_executable_observation` | G02 immutable foundation checkpoint | current |
| OBJ-03 | `native_v2_schema.py`, `native_v2_compile.py` | native v2 schema and compile tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_native_v2_schema tests.test_native_v2_compile` | G03 compiler checkpoint | current |
| OBJ-04 | `cli.py`, `native_v2_runtime.py` | native v2 CLI and runtime tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_native_v2_cli tests.test_native_v2_runtime` | G04 integration checkpoint | current |
| OBJ-05 | delegation v2 types, schema, versions | delegation schema and version tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_schema tests.test_delegation_v2_versions` | G05 parser checkpoint | current |
| OBJ-06 | delegation graph and permissions modules | graph, projection, permission tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_graph tests.test_delegation_v2_projection tests.test_delegation_v2_permissions` | G06 graph checkpoint | current |
| OBJ-07 | delegation compile and protocol modules | compile, grants, WCD2 tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_compile tests.test_delegation_v2_grants tests.test_delegation_v2_protocol` | G07 compiler checkpoint | current |
| OBJ-08 | `delegation_v2_runtime.py`, `cli.py` | delegation v2 CLI and runtime tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_cli tests.test_delegation_v2_runtime` | G08 runtime checkpoint | current |
| OBJ-09 | `tests/runtime_guard.py` | guard activation and named guarded suite | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_runtime_guard_activation tests.test_guarded_runtime_suite` | G09 guarded-suite checkpoint | current |
| OBJ-10 | integration, security, migration docs and traceability fixture | orchestration traceability tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_orchestration_traceability` | G10 documentation checkpoint | current |
| OBJ-11 | release candidate, comparison, distribution verifiers | release candidate and distribution tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_release_candidate tests.test_distribution_isolation` | G11 candidate and artifact checkpoint | current |
| OBJ-12 | CI/release workflows, classifiers, this audit | CI, release, completion-audit structure tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.13 -m unittest tests.test_ci_workflow_structure tests.test_release_workflow_structure tests.test_completion_audit_v2` | G12 leader verification: full dual-version source and extracted-artifact suites plus quality gates | current |
| CONTRACT-VERSIONING | native and delegation version dispatch modules | legacy, native CLI, delegation version tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_legacy_contract tests.test_delegation_v2_versions` | absent/1/2 and exact tuple precedence covered | current |
| CONTRACT-IMMUTABLE-TRUTH | canonical and both compiler/runtime modules | canonical, compile, runtime tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_canonical_v2 tests.test_native_v2_runtime tests.test_delegation_v2_runtime` | descriptor/fingerprint/argv binding covered | current |
| CONTRACT-NATIVE | native schema, compiler, runtime, CLI | all native v2 tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_native_v2_schema tests.test_native_v2_compile tests.test_native_v2_cli tests.test_native_v2_runtime` | closed builders, grants, review/run covered | current |
| CONTRACT-TASK-BYTES | `task_v2.py` and runtime integrations | task and CLI/runtime privacy tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_task_v2 tests.test_native_v2_cli tests.test_delegation_v2_cli` | bounded transient exact bytes covered | current |
| CONTRACT-EXECUTABLE | executable observation and both runtimes | observation and runtime replacement tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_executable_observation tests.test_native_v2_runtime tests.test_delegation_v2_runtime` | two lexical observations and exact spawn covered | current |
| CONTRACT-DELEGATION | all delegation v2 schema/graph/compile/runtime modules | all delegation v2 tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest discover -s tests -p 'test_delegation_v2_*.py'` | tuple, graph, transitions, runtime covered | current |
| CONTRACT-PROJECTIONS | delegation schema and graph modules | projection and graph tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_projection tests.test_delegation_v2_graph` | sibling binding, ownership, ancestry covered | current |
| CONTRACT-WCD2 | `delegation_v2_protocol.py` | WCD2 protocol tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_delegation_v2_protocol` | byte-only exact framing and bounds covered | current |
| CONTRACT-BOUNDS | specification fixtures and all v2 validators | protocol specification and schema boundary tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.10 -m unittest tests.test_protocol_v2_specification tests.test_native_v2_schema tests.test_delegation_v2_schema` | lower/upper/overflow/wrong-type cases covered | current |
| EVIDENCE-GUARDED-RUNTIME | runtime guard and enrolled launch tests | named guarded runtime suite | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.13 -m unittest tests.test_guarded_runtime_suite` | current test process rejects direct INET and nonallowlisted launches; child processes are explicitly outside this guard claim | current |
| EVIDENCE-TRACEABILITY | traceability fixture and integrations docs | orchestration traceability meta-tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.13 -m unittest tests.test_orchestration_traceability` | adopted rows bind real path, validator, test | current |
| EVIDENCE-DISTRIBUTION | candidate/comparison/isolation helpers | release and distribution tests | `python3.13 tests/verify_release_candidate.py --artifact-download artifact-download --create-staging dist-under-test` | G11 exact or normalized boundary evidence | current |
| EVIDENCE-CI-RELEASE | `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `pyproject.toml` | workflow structure tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.13 -m unittest tests.test_ci_workflow_structure tests.test_release_workflow_structure` | matrices, one build, immutable staging, DAG tested | current |
| AUDIT-FORBIDDEN-BEHAVIOR | runtime sources and security docs | privacy, legacy, runtime, guarded tests | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.14 -m unittest tests.test_task_v2 tests.test_legacy_contract tests.test_guarded_runtime_suite tests.test_orchestration_traceability` | included in the leader-verified full source runs on both supported boundary interpreters | current |
| AUDIT-REPOSITORY-HYGIENE | `.gitignore`, manifest rules, status | distribution and completion-audit tests | `git status --short && git diff --check` | exact intended working-tree inventory and whitespace gate audited | current |

## Negative-scope audit

The current diff is constrained by these non-goals: no task persistence or task hashes;
no credential access; no provider HTTP calls; no retries or fallback;
no authentication or profile overrides; no protocol-1 lifecycle migration; and
no unsupported orchestration claims. Opaque declarations are not asserted to be
effective accounts, subscriptions, entitlements, billing recipients, models, or
permissions. Child processes below an allowed test CLI or harness, plus build
and extracted-sdist subprocesses, remain outside the guarded runtime claim.

No commit, push, tag, release, deployment, or publication was performed. The
leader reran both boundary source suites, the exact wheel/sdist candidate and
extracted-sdist gates, installed-wheel goldens, quality gates, diff check, and
status audit before checkpointing G12.
