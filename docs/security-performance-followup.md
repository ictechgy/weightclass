# Security, performance, and architecture follow-up

_Reviewed and measured on 2026-08-19 against main commit `5265501`._

## Scope and method

This follow-up combined one repository-wide Codex Security Standard scan, two
independent focused source reviews, a separate architecture review, cold-start
measurements, and CI-run evidence. Source review did not read credentials or
start a vendor runtime. The security scan was intentionally marked partial:
all six critical trust surfaces were traced, while only 29 of 183 tracked files
were counted as fully reviewed.

The scan recorded two medium and two low findings:

- a legacy `{{task}}` slot could occupy `argv[0]` and let task text select the
  executable;
- reviewed executable metadata or bytes are still followed by path-based
  `Popen`, leaving the documented post-observation replacement race;
- usage-store parsing did not share duplicate-key handling and could leak a
  large-integer `ValueError` traceback;
- a custom usage-store directory under an unsafe ancestor is not anchored by a
  retained directory descriptor across lock and replace operations.

The first and third findings have narrow compatible fixes in this change. The
usage-store transaction is now anchored to one opened, revalidated parent
descriptor. The executable race is narrowed by admission checks below, but it
still needs platform and migration design and is not described as fixed.

## Implemented improvements

### Security and input boundaries

- Schema-1 policy parsing now rejects `{{task}}` in `argv[0]`. Whole-token
  prompt slots later in argv remain compatible with built-in `agy` and Grok
  routes.
- The aggregate usage store now uses the shared recursive duplicate-key hook
  and normalizes every JSON `ValueError`, `RecursionError`, decode error, and
  schema error to `UsageAggregationError`.
- The schema-3 interactive selector caps one controlling-console line at 4,096
  characters and a numeric choice at 32 characters. Oversized input therefore
  reaches the existing value-free `invalid_input` boundary instead of an
  unbounded read or integer conversion.
- Executable observation rejects other-writable executable files,
  group-writable files not owned by root or the current user, and a non-sticky
  world-writable containing directory in both the lexical and resolved target
  chains. Root/current-user-owned group-writable files, sticky directories, and
  user-owned group-writable ancestors remain compatible with existing
  installations. World-writable hosted tool caches are not implicitly trusted;
  automation tests and deployments must stage or select a private runtime path.
  No verified-object execution claim is made.
- Usage-store lock, read, temporary creation, replacement, cleanup, and
  directory fsync now use names relative to one opened and revalidated private
  parent descriptor. The aggregate schema, owner-only modes, no-follow checks,
  and public API are unchanged.

Python documents that the default JSON integer conversion has a digit limit
from Python 3.11 and that `object_pairs_hook` is the supported way to inspect
object pairs during decoding: [Python JSON documentation](https://docs.python.org/3/library/json.html).

### Cold-start performance

The installed entry point already lazy-loaded local classification. It now
handles an exact standalone `--version` query without importing the full
command dispatcher and its runtime protocol families. Extra arguments still go
through the full parser and fail closed.

Thirty cold subprocesses were measured on Apple M4 Pro, Darwin 25.5.0, Python
3.14.6, with `PYTHONPATH=src` and bytecode writes disabled:

| Command | Before median | After median | Change |
| --- | ---: | ---: | ---: |
| `python -m weightclass --version` | 77.022 ms | 19.412 ms | −74.8% |
| `python -m weightclass classify` | 33.970 ms | 36.580 ms | no code-path change; timing noise |
| `python -m weightclass --help` | 79.212 ms | 81.038 ms | no code-path change; timing noise |

There is no timing assertion in the test suite. The durable regression checks
the import boundary instead. Python's `-X importtime` remains the official
profiling mechanism for future work: [Python command-line documentation](https://docs.python.org/3/using/cmdline.html#cmdoption-X).

### CI efficiency

Before this change, one PR head SHA started both a `push` run and a
`pull_request` run. For example, SHA `9555232` produced runs `32228895612` and
`32228913890`, each executing the full matrix. CI now runs branch pushes only
on `main`; PR heads use the pull-request event. A PR-number concurrency group
also cancels a stale in-progress run for the same pull request, while main pushes
retain unique run IDs.

GitHub documents branch filters and expression fallbacks for concurrency. The
workflow uses `github.event.pull_request.number || github.run_id`, so unrelated
forks with the same branch name cannot cancel one another:
[workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

## Architecture decisions and deferred hardening

`cli.py` remains the composition root. A broad split of native, delegation,
V2, and usage handlers would touch the ordering of confirmation, task access,
executable observation, and usage validation. No measured hot path justifies
mixing that refactor with these security fixes. Future extraction should begin
with behavior/import tests per command family and move one family at a time.

The executable race needs a new execution capability rather than another
metadata comparison. Python supports passing an open descriptor to
`os.execve()` only on platforms listed in `os.supports_fd`; `subprocess.Popen`
does not provide a portable verified-descriptor seam. Options under review are:

1. reject executables or ancestors writable by unrelated users as incremental
   admission control;
2. add a Linux verified-descriptor launcher behind an explicit capability;
3. use a private staged launcher only where script, code-signing, dynamic
   library, and package-relative semantics can be preserved.

See the [Python OS documentation](https://docs.python.org/3/library/os.html#os.execve)
for the platform-dependent descriptor behavior. Until a design passes
macOS/Linux compatibility and process-status tests, the existing double
observation remains defense in depth, not proof of exact bytes at exec.

The usage-store transaction race is closed for the operations covered by that
descriptor, but an unsafe ancestor can still affect pathname resolution before
the parent is opened; the default private home location remains the low-risk
path. The executable check is admission hardening only: an actor that replaces
an admitted path after the final observation can still win the path-based
time-of-check/time-of-use race. A portable descriptor launcher is still a
separate design. Neither residual changes the aggregate-only schema or
authorizes task persistence, credential management, background execution, or a
bundled provider runtime.
