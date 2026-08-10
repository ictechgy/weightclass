# Protocol 2 security boundary

Protocol 2 makes routing reviewable; it does not make caller declarations true. Model, effort, account profile, intended recipient, billing boundary, subscription, entitlement, permissions, capabilities, worktree, ownership, and orchestration labels are opaque configuration. Provider/vendor, transport, and builder values use closed structural vocabularies, but those vocabularies still do not prove effective provider behavior. Structural validation proves only that the reviewed declarations form an allowed route.

## Review and execution

Native schema 2 and delegation protocol 2 use the same binding pattern:

1. `route` validates and selects a route, emits its complete canonical descriptor, and includes a fingerprint computed from that descriptor minus only `route_fingerprint`.
2. The caller reviews the descriptor, including source and destination profiles, model and effort, executable, exact argv, transitions, and authorizations.
3. `run` requires the exact reviewed fingerprint. A missing acknowledgement stops before task access. A supplied mismatch stops before executable inspection or spawn.
4. Execution consumes the same immutable compiled descriptor, fingerprint, executable, and argv used for review. It starts exactly one foreground direct child with `shell=False`; there is no retry, fallback, or command rebuilding.

Native execution requires the main thread and a reviewed native `SIGCHLD`
disposition before task input, then rechecks that process context immediately
before executable observation and spawn. The caller must exclusively own the
direct child's wait status for the invocation; the check cannot lock out a
hostile concurrent native disposition change or foreign reaper.

Cross-profile changes require a directional profile grant. Cross-vendor changes additionally require a directional vendor grant. Delegation compares and authorizes provider, intended recipient, billing boundary, transport, and opaque account-profile label independently. Reverse, unused, redundant, ambiguous, and missing grants fail closed.

## Task privacy

Task content is transient standard input. Protocol 2 performs one bounded binary read, strict UTF-8 validation, and byte-identical delivery. weightclass never persists, logs, echoes, hashes, fingerprints, or includes task content in diagnostics. Delegation sends canonical descriptor bytes and exact task bytes in one bounded WCD2 frame to the reviewed external runtime. That runtime, not weightclass, owns any provider authentication, network access, billing, output, and retention behavior.

## Executable observation and residuals

`ExecutableObservation` records the exact lexical path, `st_dev`, `st_ino`, file type/mode, size, `mtime_ns`, `ctime_ns`, and whether any POSIX execute bit is set (`mode & 0o111 != 0`). Final-component `lstat` rejects a symlink, nonregular file, or file with no execute bit. A zero-size regular file with an execute bit is accepted at observation; the operating system may still refuse to execute it.

The executable is observed before execution and again immediately before spawn, all fields must match, and the spawn seam rechecks `executable == argv[0]`. Intermediate path components may still be symlinks. Replacement after the second observation remains possible. Execution is path-based, not inode-bound, and these checks do not prove which bytes the kernel ultimately executed.

weightclass observes only its direct child's exit status. Exit zero is not proof of task completion, orchestration completion, synthesis, gate approval, settlement, effective model/account/billing, descendant containment, or runtime honesty. External runtimes own descendants and all orchestration semantics.

The named guarded runtime suites provide only current test process evidence: direct calls in that process reject INET sockets and nonallowlisted executable prefixes. The guard does not instrument child processes; behavior below an allowed CLI or harness is covered by separate test-owned fixture and lifecycle assertions. Build and extracted-sdist subprocesses are outside the guard claim.

## Planning and rollback truth

The five-round RALPLAN history ended `max_rounds/ITERATE`. Its Critic findings were mandatory implementation input; it was never consensus approval.

Protocol 2 is additive. Rollback removes only native schema-2 and delegation protocol-2 dispatch and their documentation/tests. It must leave built-ins, absent-version and explicit schema 1, legacy render, delegation protocol 1 lifecycle, WCD1, qualification behavior and its empty registry, V2 API routing, and distribution isolation unchanged.
