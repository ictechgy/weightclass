# Handoff

_Last updated: 2026-08-13 19:35 KST by Codex_

## Goal

- Maintain `weightclass` as a public, local router that deterministically
  selects one reviewed native agent CLI workflow from explicit user policy.
- Preserve the local privacy boundary: task content is transient only. Never
  persist, log, hash, echo, or include it in reviewed output or diagnostics.
- The router never owns provider credentials, HTTP, billing, quota, or
  subscription-entitlement discovery.

## Current Status

- `weightclass 0.13.0` is merged, tagged, published, and installable.
  - Release commit and annotated `v0.13.0` tag target:
    `155f7446b295f3d226c0a392c3b52d93a4b5644f`.
  - GitHub Release: <https://github.com/ictechgy/weightclass/releases/tag/v0.13.0>.
  - Final release-gating CI:
    [31686174951](https://github.com/ictechgy/weightclass/actions/runs/31686174951), completed successfully.
  - Immutable Release workflow:
    [31686408438](https://github.com/ictechgy/weightclass/actions/runs/31686408438), completed successfully.
  - PyPI: <https://pypi.org/project/weightclass/0.13.0/>.
- `0.13.0` adds tier-specific opaque Grok model overrides to packaged
  `review-preset`, `route`, and `run`, together with the repository
  security/performance and release hardening described below. Custom selections
  remain explicitly unqualified; built-in routes and provider boundaries are
  unchanged.
- The current unreleased code adds the cost-recommendation feature:
  task-free `review-cost-profile` plus non-executing `recommend`. It is not
  tagged, published, or installed through Homebrew yet. Built-in routes and
  every execution path remain unchanged.
- The source-of-truth Homebrew formula was updated on `main` by
  `e18fd3988a6fa6ad642f44a084b436c6507a2f6b`. The matching tap commit is
  [`1244ed6`](https://github.com/ictechgy/homebrew-tap/commit/1244ed6f0baf6cf2c557bec356230e3ea199961d).
- `v0.8.1` was never published to PyPI: its Release workflow failed only on
  Ruff import ordering. It had no GitHub Release and was deliberately deleted
  from both the local and `origin` tag namespaces. Do not recreate it.
- `0.8.2` is an earlier published release. The `0.9.0` minor bump was
  intentional because Linux Claude semantic triage now fails closed before
  task egress; ordinary native Claude routing remains unchanged.
- `0.9.0` is an earlier published release. The `0.10.0` minor adds offline
  evaluation commands and an additive example-policy CLI surface; built-in
  route behavior and policy schemas remain unchanged.
- `0.10.0` is an earlier published release. The `0.11.0` minor adds
  packaged opt-in cost-focused policies for every built-in vendor, in-memory
  `--cost-focused` selection, and an opaque Codex model override. Built-in
  route behavior and policy schemas remain unchanged.

## Completed in 0.8.2

- V2 API runtime observation fails closed for malformed paths, including a
  symlink loop that raises `RuntimeError` on Python 3.10; diagnostics stay
  value-free as `invalid_input`.
- The V2 API egress-confirmation gate runs before runtime inspection and before
  task input is read. Invalid policy input still retains its existing
  `invalid_input` precedence.
- The reviewed runtime identity is observed again immediately before spawn;
  changed identity fails closed with `route_fingerprint_mismatch` and an
  unobservable runtime with `executor_unavailable`.
- Regression coverage covers the symlink loop, confirmation precedence, and
  runtime identity drift.
- The Homebrew formula installs the PyPI `0.8.2` source distribution:
  - sdist SHA-256:
    `c566a8f2835ba29e8fae6a30651fe0e40671a376ccca4db51459cc26770ca096`.

## Completed in 0.9.0

- All ordinary foreground execution paths now own direct-child status through
  `waitpid`; `ECHILD` is a redacted `executor_failed`, never synthesized exit
  zero. Exact argv/input, inherited stdout/stderr, exit/signal status, one-child
  behavior, and `agy`/`grok` argv task delivery remain covered.
- V2 API execution rejects a missing reviewed route fingerprint before process
  context checks, runtime observation, or task input.
- Foreground spawn and cleanup defer SIGINT across ownership seams and always
  drive the owned child through close, TERM, KILL, and final reap as needed.
- Vendor triage validates process context adjacent to spawn, owns group cleanup
  and direct reap, and erases its pinned private tree with a linear,
  descriptor-relative traversal. Darwin uses a reviewed static `sandbox-exec`
  profile and adapter version 2; Linux Claude semantic triage fails closed
  before task egress because no equivalent filesystem containment is reviewed.
  Ordinary native Claude routing is unchanged.
- macOS CI and release boundary jobs explicitly include the new
  `tests.test_foreground_process` and `tests.test_process_context` suites.

## Completed in 0.10.0

- Added an evaluation-only offline paired token scorer at
  `tests/eval/token_benchmark.py`. It accepts bounded task-free evidence,
  emits aggregate metrics bound to safe experiment/configuration identifiers,
  and never invokes a vendor, reads credentials, or infers provider pricing.
- Added a separate offline estimated-cost scorer at
  `tests/eval/cost_benchmark.py`. It accepts externally normalized integer cost
  units, reuses the token scorer's privacy/quality/statistical boundaries, and
  explicitly does not infer prices or claim actual billing.
- Promotion is deliberately strict: at least 30 paired tasks, both languages,
  every fixed category and tier, a nondegenerate savings interval with at least
  a 15% lower bound, quality non-inferiority within 5%, complete paired runs,
  no new critical failure, and all provenance assertions.
- README now describes weightclass as effort routing rather than a token-saving
  mechanism, counts `--ask-vendor` plus later execution as two full-task vendor
  invocations, and separates historical 15/40 + 33/40 figures from the current
  local-only 17/40 public-regression result.
- A schema-1 Claude effort-inheritance policy is documented only as a reviewed
  experiment. Its standard route omits the effort override; built-ins,
  `balanced`/`cautious`, schema 2, fingerprints, and runtime behavior are
  unchanged.
- An approved exploratory Claude 2.1.228 pilot used existing public fixture
  tasks with safe mode, tools disabled, and session persistence disabled. The
  two complete pairs used 15,866 raw tokens with the effort override omitted
  versus 8,302 with explicit medium, so the experimental arm used 91.1% more
  under the pilot normalization contract. A third explicit-medium invocation
  failed before the harness captured usable usage. The fixture was not fresh or
  blind, quality was not independently reviewed, and not every attempt could be
  normalized, so the run is not promotion evidence and collection stopped
  before the 30-pair gate. Task and response text were not recorded.
- A second approved Claude 2.1.228 pilot compared explicit low with explicit
  medium on two disposable low-risk editing fixtures, counterbalancing order.
  All four calls exposed complete usage, both arms passed both automated file
  checks, and no unexpected file was created. Low used 49,163 raw tokens versus
  49,144 for medium: 19 more tokens, or about 0.04%, rather than the required
  savings. Both arms took seven turns per fixture and cache input dominated the
  totals. The pilot reused one already-public README task across two layouts
  and had no independent blind review, so it is diagnostic rather than
  promotion evidence. Collection again stopped before the 30-pair gate; task
  and response text and disposable workspaces were not retained.
- A third approved Claude 2.1.228 pilot compared the default model at medium
  with explicit Haiku at low on the same two disposable fixtures, again with
  counterbalanced order. All four calls exposed complete usage and all four
  outputs passed the automated checks without unexpected files. The default
  arm used 49,099 raw tokens and seven turns per fixture; Haiku used 58,811 and
  eight turns per fixture, 9,712 tokens or 19.78% more. Explicitly selecting a
  lighter model therefore did not reduce raw tokens in this pilot. It reused
  the public task and automated rubric, so it is diagnostic only and stopped
  before the 30-pair gate. No task, response, or workspace was retained.
- A follow-up cost diagnostic reran and expanded that comparison to six
  counterbalanced disposable fixtures with complete model-level usage and
  CLI-reported estimated cost. Both arms passed 6/6 automated checks, protected
  files were unchanged, and no unexpected files appeared. Default/medium used
  147,771 raw tokens and reported $0.3951410; Haiku/low used 243,851 raw tokens
  but reported $0.0960305, a 75.70% estimated-cost reduction despite 65.02%
  more tokens. The same public English task, automated rubric, low-risk-only
  slice, and JSON measurement output make this diagnostic rather than
  promotion evidence.
- Added exact schema-1 policies for that cost comparison. The baseline remains
  at `tests/eval/claude_cost_baseline_policy.json`; after qualification, the
  byte-identical candidate moved to
  `src/weightclass/examples/claude_cost_focused_policy.json`. Only the low route differs;
  standard/high routes and fingerprints are identical, both low routes deliver
  the task on stdin, and their reviewed low fingerprints differ. A real
  `wclass route` acknowledgement followed by `wclass run` succeeded for both
  policies on one disposable public-fixture task. Both edits passed; the
  candidate used 27,867 tokens versus 23,133 (+20.46%) while reporting
  $0.0106916 versus $0.049921 (-78.58%). This closes the router-compatibility
  check only; one reused English low-risk task is not promotion evidence.
- Ran a fresh 30-pair, 60-arm counterbalanced evaluation through those exact
  `route`/`run` policies. The ephemeral synthetic corpus covered en/ko, all
  nine fixed categories, and all three tiers. All arms returned complete usage;
  both configurations passed 30/30 arm-blind Codex quality reviews and had no
  critical failure. Baseline reported 643,578 tokens and $1.9441010; candidate
  reported 688,268 tokens (+6.94%) and $1.5257985 (-21.52%). The cost-savings
  95% interval was 8.08% to 34.95%, failing the 15% lower-bound and 20% width
  gates. Tasks, responses, pair rows, and workspaces were not retained.
- Replaced the paired-quality normal interval/degeneracy workaround with a
  conservative Bonferroni-adjusted exact Clopper-Pearson interval over matched
  improvements and regressions. Thirty perfect ties remain insufficient for a
  5% non-inferiority claim, while 72 perfect ties can pass; perfect equality is
  no longer rejected forever or made weaker than one incidental improvement.
- Rejected a follow-up v2 cost candidate after a fresh nine-pair mixed-tier
  canary. It kept Haiku/low, tried Sonnet/medium for standard, and retained the
  existing high route. All 18 arms completed; aggregate estimated cost fell
  27.29% while raw tokens rose 19.35%. Low quality tied 3/3, but standard
  quality was 2/3 versus baseline 3/3 and its raw tokens rose 58.29%. The v2
  policy was removed instead of being promoted or left as dead configuration.
- Rejected an effort-only v3 canary as well. It retained Haiku/low, changed
  standard from medium to low effort without changing the default model, and
  kept high unchanged. Both arms passed 9/9 blind quality checks, but aggregate
  cost savings were only 14.08%, below the 15% floor; raw tokens rose 6.49%.
  Standard itself reported 11.08% higher cost for only 0.59% fewer tokens.
  The temporary policy and all task artifacts were discarded.
- A fresh 150-pair balanced promotion-scale run then exercised the retained
  low-only candidate over 300 complete counterbalanced arms. The corpus covered
  en/ko, all nine categories, and all tiers equally. Candidate quality was
  143/150 versus baseline 142/150; the exact paired 95% interval was -2.41% to
  +3.66%, and there was no new candidate critical failure. Candidate used
  3,604,732 raw tokens versus 3,359,583 (+7.30%) and reported $8.353250 versus
  $10.133820 (-17.57%). The cost 95% interval was 12.30% to 22.84% (10.54%
  wide), so the sole failing gate was its lower bound remaining below 15%.
  Decision: `no-go`. Tasks, results, pair rows, and workspaces were deleted.
- The first low-target qualification attempt completed 45/90 pairs, then a
  fourth-batch synthetic task failed the pre-arm `protected_files` validator.
  The entire partial run was invalidated rather than selectively reusing its
  favorable rows; its TemporaryDirectory removed all tasks, results,
  workspaces, and pair evidence. The restart contract permits order-preserving
  deduplication of repeated protected names but still rejects any name that is
  not an initialized flat file. Completed task-free rows may be checkpointed
  outside the repository until final scoring, then must be deleted.
- The clean low-target restart completed all 90 pairs/180 arms: 72 low, nine
  standard controls, and nine high controls, still covering en/ko and all nine
  categories. Blind quality tied 88/90; both arms had the same two critical
  failures, so the candidate introduced none. Baseline reported $4.8391435 and
  1,803,839 tokens; candidate reported $2.1708828 (-55.14%) and 2,066,975
  tokens (+14.59%). The cost 95% interval was 46.28% to 64.00%, quality was
  -4.02% to +4.02%, and every machine gate passed. All task artifacts and the
  task-free checkpoint were deleted.
- Moved the byte-identical evaluated candidate to
  `src/weightclass/examples/claude_cost_focused_policy.json` as a public explicit opt-in. Its
  route IDs remain unchanged to preserve the evaluated configuration binding.
  This is a cost-focused low-route `go`, not token savings or permission to
  change built-ins, standard/high, schema 2, or the posture vocabulary.
- Added `wclass example-policy claude-cost-focused` so wheel installs can emit
  that exact reviewed JSON without invoking a vendor or reading a task. The
  package-data and release-candidate gates now require the resource in the
  installed wheel.
- No independent promotion-grade provider token-savings evidence exists, so
  token/default promotion remains `no-go`.

## Completed in 0.11.0

- Added installable `codex-cost-focused`, `agy-cost-focused`, and
  `grok-cost-focused` schema-1 example policies beside the evaluated
  `claude-cost-focused` policy. Each new policy changes only its `standard`
  route from the built-in medium effort command to the already reviewed low
  effort command; low/high stay identical to their built-in commands.
- `wclass example-policy codex-cost-focused --model <opaque-label>` can now
  bind a user-reviewed model to only the Codex low and standard routes. High
  retains the installed Codex default. The label is validated as one bounded,
  printable, non-whitespace, non-option argv token; no availability or price
  claim is made, and the generated route fingerprint changes with the model.
- Native schema-1 `route` and `run` now accept `--cost-focused` and select the
  matching packaged policy in memory from the existing explicit
  `--source-vendor`. Codex also accepts the same `--model` override. No policy
  file or router preference is written; `--policy`/`--source-profile`
  conflicts and missing/unsupported vendors fail before task access.
- Automatic `run` still requires the exact fingerprint emitted by an
  otherwise identical automatic `route`; the opt-in flag never acts as
  execution acknowledgement. Removing the flag returns immediately to the
  unchanged built-in routes.
- The new examples are explicit optimization hypotheses only. They pin no
  model and have no provider usage, price, quality, token, or billing evidence.
  Built-ins, posture vocabulary, schemas, and default routing are unchanged.
- CLI integration coverage loads every example through the real schema-1
  parser and route path, verifies source-vendor containment and exact commands,
  preserves stdin delivery for Codex, preserves argv delivery for `agy`/Grok,
  and proves task text does not enter route output.
- Release candidate validation now requires every example policy to be
  available from clean installed wheels on Python 3.10 and 3.14, and exercises
  a cost-focused Codex route directly from the installed CLI without a policy
  file.

## Completed in 0.12.0

- Added `route`/`run --preset <vendor>-cost-focused` as a shorthand that binds
  the packaged source vendor without a separate `--source-vendor`. It conflicts
  with `--policy`, `--source-profile`, `--cost-focused`, and an independently
  supplied vendor; invalid combinations fail before task access. The existing
  `--cost-focused --source-vendor` form remains compatible.
- Added task-free `review-preset`, which reports all three exact commands,
  fingerprints, vendors, tiers, and stdin/argv delivery boundaries without
  reading task input or starting a vendor. Exact Claude output is labeled
  `measured_low_route_only`; the other packaged policies remain
  `unqualified_experiment`.
- Added `--low-model`/`--standard-model`/`--high-model` and matching effort
  flags to packaged Claude and Codex review/route/run. Values remain opaque,
  bounded argv tokens; availability, entitlement, vocabulary, quality, and
  price are not inferred. `agy`/Grok and selector-free overrides fail closed.
  Custom selections are labeled `unqualified_custom` and remain outside the
  measured Claude low-route claim.
- Review and run rebuild the same exact command and fingerprint. Automatic
  execution still requires acknowledgement before process-context validation
  or task input, starts one foreground child, and persists no preference or
  vendor configuration.
- The release candidate workflow now exercises task-free Claude custom review
  and direct Codex custom preset routing from the clean installed wheel on both
  Python boundary jobs.

## Completed in 0.13.0

- Packaged `grok-cost-focused` review/route/run accepts `--low-model`,
  `--standard-model`, and `--high-model`. Each selected opaque label is inserted
  through Grok's reviewed `--model` option and changes the exact route
  fingerprint.
- Grok's packaged `--reasoning-effort` values remain unchanged; tier effort
  overrides still fail closed. `agy` continues to reject all overrides.
- Review retains `{{task}}` and reports `task_delivery: argv`; acknowledged run
  substitutes the task only immediately before the one foreground spawn.
- Release boundary jobs now exercise a Grok custom model route from the clean
  installed wheel on Python 3.10 and 3.14.
- The repository-wide security/performance follow-up pins the Python 3.13.12
  release toolchain with exact versions and SHA-256 hashes, builds with the
  pinned setuptools backend and no build isolation, and rejects a release tag
  that is not reachable from `origin/main` before installing networked tools.
- The shared bounded JSON loader now converts decoder `RecursionError` to the
  existing value-free `invalid_input` boundary. Native and API policy commands
  cover the supported Python 3.10 failure mode directly.
- Local `classify` now enters through a small command-family dispatcher and
  does not import delegation, V2, native runtime, or triage modules. Vendor
  triage remains lazy and explicit; all other commands retain the full parser.
- CI and release workflows use SHA-pinned `actions/setup-python` v7.0.0. Its
  reviewed action metadata runs on Node 24, replacing the Node 20-based v5
  generation that GitHub runners warned about.

## Current Unreleased Cost Recommendation

- Added `src/weightclass/cost_recommendation.py` with strict version-1 cost
  profile and qualification-card schemas. Inputs are bounded regular JSON,
  duplicate-safe through the shared loader, and must assert that opaque IDs
  are not task-derived. User/evaluator cost and quality assertions remain
  explicitly unverified by the router.
- Added task-free `wclass review-cost-profile`, which validates the profile
  and emits its canonical fingerprint without reading task stdin or starting a
  vendor.
- Added `wclass recommend --preset <vendor>-cost-focused --cost-profile ...
  --qualification-card ...`. It loads both documents before task access,
  selects the packaged candidate and same-vendor built-in baseline for one
  tier, and emits `recommend` or valid `abstain` without starting a child.
- The fixed machine floors are 30 paired outcomes, 15% estimated-cost lower
  bound, 20% maximum savings interval width, 5% maximum quality margin, zero
  new critical failures, complete attempts, independent quality review, both
  languages, all nine categories, and all three tiers. Stale, mismatched,
  incomplete, or economically flat evidence abstains rather than falling back.
- The cost-profile fingerprint binds every cost and route value. A separate
  canonical qualification-card fingerprint binds every aggregate evidence
  field; both exact route fingerprints and both evidence fingerprints feed the
  recommendation fingerprint. It does not authorize execution: ordinary
  `run` acknowledgement is still required.
- Receipts expose provider differences without normalizing their semantics:
  Claude/Codex have reviewed model+effort override surfaces, Grok model only,
  and `agy` no model override. Claude/Codex retain stdin; `agy`/Grok retain
  `task_delivery: argv` and the existing process-inspection disclosure.
- Added `docs/cost-recommendation.md` and README entry-point documentation.
  Same-vendor packaged presets are the only initial scope; no runtime learning,
  retry, fallback, telemetry, provider access, price inference, built-in
  change, cross-vendor recommendation, or automatic execution was added.
- macOS CI/release boundary suites now include the focused recommendation
  module, and clean installed-wheel release jobs smoke both new command
  parsers.

## Key Files & State

- `src/weightclass/cli.py`: V2 route/run ordering and pre-spawn identity check.
- `src/weightclass/process_context.py`: shared direct-child wait-status
  ownership and safe process-context predicates.
- `src/weightclass/foreground_process.py`: behavior-preserving foreground
  stdin delivery, SIGINT deferral, escalation, and final reap.
- `src/weightclass/triage.py`: opt-in semantic triage containment, process-group
  lifecycle, and descriptor-relative private-tree cleanup.
- `src/weightclass/v2.py`: API runtime observation and redacted invalid-input
  conversion.
- `tests/test_process_context.py`, `tests/test_foreground_process.py`,
  `tests/test_triage.py`, and `tests/test_v2.py`: focused hardening regressions.
- `tests/eval/token_benchmark.py` and `tests/test_eval_token_benchmark.py`:
  bounded aggregate-only token evidence, decision gates, and privacy/input
  regressions.
- `tests/eval/cost_benchmark.py` and `tests/test_eval_cost_benchmark.py`:
  externally normalized estimated-cost evidence without internal pricing or
  billing claims.
- `tests/eval/claude_cost_baseline_policy.json`,
  `src/weightclass/examples/claude_cost_focused_policy.json`, and
  `tests/test_eval_cost_policy.py`: exact evaluated Claude commands, public
  low-route cost opt-in, unchanged non-low routes, stdin delivery, and
  fingerprint binding.
- `src/weightclass/examples/{codex,agy,grok}_cost_focused_policy.json`:
  unevaluated vendor-specific lower-effort opt-in policies; do not describe
  them as measured savings.
- `README.md`: documents the task-in-argv residual for `agy` and `grok`.
- `packaging/homebrew/weightclass.rb`: source of truth copied to
  `ictechgy/homebrew-tap` after a successful PyPI publish.
- `.github/workflows/release.yml`: builds one immutable candidate, validates it
  on Linux and macOS, then publishes the exact artifact through PyPI Trusted
  Publishing.
- `requirements/release.in` and `requirements/release.txt`: reviewed direct
  release tools and their exact hash-pinned installation closure.
- `src/weightclass/entrypoint.py` and `classification_cli.py`: lightweight
  local-classification dispatch and lazy vendor-triage loading.
- `src/weightclass/cost_recommendation.py`,
  `tests/test_cost_recommendation.py`, and
  `docs/cost-recommendation.md`: unreleased advisory expected-completed-cost
  contracts, CLI regressions, and user workflow.
- `tests/verify_release_source.py`: redacted release-tag ancestry gate used
  before the release job installs networked tools.
- `docs/completion-audit-v2.md`: requirement-to-test completion map. Goal g12 is leader-verified; retain this audit connection when refreshing this file.

## Important Context / Decisions

- Built-in native routing supports `claude`, `codex`, `agy`, and `grok`.
  Vendor/model/effort/profile labels are opaque user configuration; do not infer
  availability, entitlement, or billing state.
- A route normally stays with its source vendor. Cross-vendor routing requires
  an exact explicit directional policy grant; unknown, ambiguous, unsupported,
  or unsafe input must fail closed with redacted diagnostics.
- V1 and V2 start at most one reviewed foreground child. They do not retry,
  recover, supervise, background, proxy provider APIs, read credentials, or
  persist task content.
- Review/run binding is mandatory for policy routes: `route` produces the exact
  fingerprint and `run` requires acknowledgement before task access. File mode
  checks are defense in depth, not a replacement for the fingerprint.
- `agy` and `grok` accept prompts through the reserved `{{task}}` argv slot.
  This exposes task content to local process inspection for the child lifetime;
  it is documented residual risk, not a reason to log or persist the task.
- The built-in `grok` command cannot accept a task beginning with `-`; changing
  that requires a separately reviewed command-shape design.
- Do not populate a provider qualification registry or add a built-in command
  for an unmeasured CLI.
- Do not re-enable Linux Claude semantic triage with O_PATH cleanup alone.
  Exact-inode cleanup cannot prevent root replacement, writes outside the
  pinned tree, or process-group escape; a separate reviewed containment command
  is required.

## Verification

- Fresh unreleased cost-recommendation verification on 2026-08-13:
  - Behavior-first RED→GREEN covered missing `recommend`, flat-cost
    abstention, configuration-before-task ordering, full qualification-card
    fingerprint binding, stale route binding, every fixed gate, all four
    provider capability/task-delivery receipts, and task-free profile review.
    Review follow-up additionally rejects non-integer schema versions and a
    route object whose displayed command is not bound by its supplied route
    fingerprint.
  - Python 3.10.20 and Python 3.14.6 full `unittest` suites with
    `ResourceWarning` as an error: 810 tests passed on each interpreter.
  - Ruff 0.16.2 check/format over 125 files, strict mypy over 105 source files,
    compileall, and `git diff --check` passed.
  - Offline wheel/sdist build and strict Twine checks passed. The artifacts
    contain the new module, document, and focused test. Extracted-sdist
    isolation passed 802 tests with 13 platform skips.
  - A clean Python 3.10 wheel install completed the real
    `route -> review-cost-profile -> recommend` flow, returned `recommend`,
    preserved the exact candidate fingerprint, emitted no task text, and had
    `PATH=/nonexistent` during recommendation to prove no vendor launch was
    required.
  - Installing the complete hash-pinned release requirements offline was not
    possible because the macOS CPython 3.13 mypy 2.3.0 wheel was absent from
    the local cache. The independently cached strict mypy 2.3.0 tool passed;
    no network access was used.

- Fresh repository-review remediation verification on 2026-08-13:
  - RED→GREEN evidence covers a missing release lock, Python 3.10 native/V2
    decoder recursion tracebacks, unmerged Git release commits, eager protocol
    imports on local classification, and an overbroad local exception catch.
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 800 tests passed on each interpreter.
  - Ruff 0.16.2 check/format over 104 files, strict mypy over 103 source files,
    compileall, and `git diff --check` passed.
  - An offline wheel/sdist build with build 1.5.0 and setuptools 84.0.0 plus
    strict Twine checks passed. Extracted-sdist isolation passed 794 tests with
    13 platform skips, including the release ancestry helper.
  - A clean offline wheel install reported `weightclass 0.13.0`, exposed
    `weightclass.entrypoint:main`, and returned the exact local low-tier smoke.
  - Twenty-process local measurement: classification entrypoint median
    20.20 ms; end-to-end local classify median 34.84 ms, down from the reviewed
    71.57 ms baseline. This is host-local evidence, not a cross-platform SLA.
  - The Node-runtime follow-up first failed against all seven SHA-pinned
    setup-python v5.6.0 uses, then passed after moving them together to the
    verified v7.0.0 commit. The official tag points directly to that commit,
    its GitHub signature is valid, and its action metadata declares Node 24.
  - A clean Python 3.13 release-tool installation exposed that the first
    release lock omitted Twine's transitive runtime dependencies. The lock was
    regenerated for CPython 3.13 on x86_64 Linux with all 37 exact transitive
    packages and hashes. A fresh environment then executed Ruff, mypy, build,
    and Twine; the rebuilt `0.13.0` sdist passed 794 tests with 13 platform
    skips, and a clean wheel install passed version, classification, and Grok
    custom-model routing smokes.

- Fresh `0.13.0` Grok model-routing verification on 2026-08-13:
  - RED: the two focused review/run tests both returned `invalid_input` before
    implementation. GREEN: the same tests pass and bind exact tier commands,
    fingerprints, argv delivery, task substitution, and one-child execution.
  - Commit review reproduced an empty explicit model label being mistaken for
    an absent override. The focused regression failed with exit 0 before the
    fix and now receives redacted `invalid_input`; the common correction also
    protects Claude and Codex override fields.
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 791 tests passed on each interpreter.
  - Ruff 0.16.2 check/format over 99 files, strict mypy over 98 source files,
    compileall, and `git diff --check` passed.
  - Offline wheel/sdist build and strict Twine checks passed. The extracted
    sdist passed 786 tests with 11 platform skips.

- Fresh `0.13.0` release verification on 2026-08-13:
  - Main CI run `31686174951` passed on the exact annotated-tag target
    `155f744`: Python 3.10-3.14, macOS 3.10/3.14 boundaries, lint, strict
    typing, build, metadata, and extracted-sdist isolation.
  - Release workflow `31686408438` built one immutable Python 3.13 candidate,
    revalidated it on Python 3.10 and 3.14 plus macOS boundaries, and published
    those exact artifacts through PyPI Trusted Publishing after the required
    environment review.
  - PyPI exposes exactly one wheel and one sdist, neither yanked. Independent
    downloads matched SHA-256: wheel
    `88865ad50f8888f20e7716d099c39f7fcbb5c3fbb8a3a8e4c72f23e0271bc241`;
    sdist `9ba9db753c4aaf5189bf0210cd7933bd29a68168fcf473aeb1ad302586297ae9`.
  - A clean install of the independently downloaded wheel reported
    `weightclass 0.13.0` and returned the exact local low-tier smoke. The
    immutable release validators exercised the Grok custom-model route from
    that same wheel on both Python boundary versions.
  - Formula-scoped style and strict audit, source reinstall, `brew test`, and
    direct classification/Grok custom-model route smokes passed. Installed
    `/opt/homebrew/bin/wclass` reports `weightclass 0.13.0`; the route retained
    Grok argv delivery, the reviewed effort flag, and the custom model.
  - Post-release source-formula CI run `31687162123` passed on commit
    `e18fd39`: Python 3.10-3.14, macOS boundaries, lint, strict typing, build,
    metadata, and extracted-sdist isolation.

- Fresh `0.12.0` release verification on 2026-08-13:
  - Main CI run `31672912246` passed on the exact annotated-tag target
    `37ea1a7`: Python 3.10-3.14, macOS 3.10/3.14 boundaries, lint, strict
    typing, build, metadata, and extracted-sdist isolation.
  - Release workflow `31673091914` built one immutable Python 3.13 candidate,
    revalidated it on Python 3.10 and 3.14 plus macOS boundaries, and published
    those exact artifacts through PyPI Trusted Publishing.
  - PyPI exposes exactly one wheel and one sdist, neither yanked. Independent
    downloads matched SHA-256: wheel
    `2ffa1606ed2736e167867f7a5c007a182ded684d5be59d8cdf06b210f5e3ca5a`;
    sdist `d65eb74c2d9a26f8001bab736f5bae8accc753f328a76479d16eae86e472828d`.
  - A clean install of the independently downloaded wheel reported
    `weightclass 0.12.0`; task-free Claude custom review and Codex custom
    model/effort route smokes returned `unqualified_custom` without invoking a
    vendor.
  - Formula-scoped style and strict audit, source reinstall, `brew test`, and
    direct Claude review/Codex route smokes passed. Installed
    `/opt/homebrew/bin/wclass` reports `weightclass 0.12.0`.
  - Post-release source-formula CI run `31673761232` passed on commit
    `8e90b4e`: Python 3.10-3.14, macOS boundaries, lint, strict typing, build,
    metadata, and extracted-sdist isolation.
  - The first pre-tag CI run `31672522120` exposed a test-only macOS `/dev/fd`
    snapshot race. Commit `37ea1a7` replaced it with exact descriptor tracking;
    mutation testing proved the new assertion detects the intended leak, and
    no triage production code changed.

- Fresh `0.12.0` pre-release verification on 2026-08-13:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 789 tests passed on each interpreter.
  - The focused router and release-workflow suite passed 115 tests.
  - Ruff 0.16.2 check/format over 116 files, strict mypy over 98 source files,
    compileall, and `git diff --check` passed.
  - Offline wheel/sdist build and strict Twine checks passed. The extracted
    sdist passed 784 tests with 11 platform skips.
  - A clean offline `0.12.0` wheel install reported `weightclass 0.12.0`,
    reviewed a custom Claude preset without a task/vendor invocation, and
    routed a custom Codex standard model/effort command with
    `unqualified_custom` status.

- Fresh `0.11.0` verification on 2026-08-13:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 777 tests passed on each interpreter.
  - Ruff 0.16.2 check/format, strict mypy over 98 source files, compileall,
    and `git diff --check`: clean.
  - Offline wheel/sdist build and strict Twine metadata checks passed. The
    extracted sdist passed 772 tests with 11 platform skips; a clean wheel
    install reported 0.11.0 and emitted all four packaged policies.
  - Main CI run `31668199216` passed on release commit `1f4a15a`: Python
    3.10-3.14, macOS 3.10/3.14 boundaries, lint, strict typing, build,
    metadata, and extracted-sdist isolation.
  - Release workflow `31668338506` passed on annotated tag `v0.11.0` and
    published the exact immutable candidate through PyPI Trusted Publishing.
    PyPI exposes one wheel and one sdist; independent downloads matched the
    published SHA-256 values.
  - PyPI wheel SHA-256:
    `03880be90b4ad816015c26377835a9f195b2257c060cef716cc08fc9e39c34aa`.
    PyPI sdist SHA-256:
    `99cba03fcf5adbff04230bc360d84c6a24c03dffec0310b6128d724f390edd44`.
  - A clean install of the independently downloaded PyPI wheel reported
    `weightclass 0.11.0`, classified the low smoke correctly, emitted all four
    policies, and routed the direct Codex cost-focused model smoke.
    A separate no-cache install from the public PyPI simple index passed after
    index propagation completed.
  - Formula-scoped `brew style`, strict audit, source reinstall, `brew test`,
    and direct cost-focused routing smoke passed. The installed
    `/opt/homebrew/bin/wclass` reports `weightclass 0.11.0`.
  - Post-release source-formula CI run `31668873199` passed on commit
    `2c10b38`: Python 3.10-3.14, macOS boundaries, lint, strict typing, build,
    metadata, and extracted-sdist isolation.

- Fresh pre-release feature-worktree verification on 2026-08-13:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 777 tests passed on each interpreter.
  - The affected router/release-workflow suite passed 103 tests on
    both interpreters. Ruff 0.16.2 check/format and strict mypy passed for the
    changed Python files.
  - An offline sdist/wheel build succeeded with setuptools 80.9.0 and build
    1.5.0. Both artifacts contained exactly all four cost-focused example
    resources; a clean wheel install emitted and routed every policy using the
    matching source vendor.
  - A second clean wheel install generated the Codex example with
    `--model release-smoke-model`, proved the label was present only in low and
    standard commands, routed standard through the generated command, and
    emitted a bound SHA-256 route fingerprint.
  - A fresh clean-wheel installation selected all four packaged policies
    directly with `route --cost-focused`, including the Codex model override.
    Automatic `run` without a fingerprint returned exact exit 6 and
    `route_fingerprint_mismatch` before reading a task.

- Fresh `0.10.0` verification on 2026-08-13:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 769 tests passed on each interpreter.
  - Ruff 0.16.2 check/format over `src` and `tests`, strict mypy over 98 source
    files, compileall, and `git diff --check`: clean.
  - The README experimental policy parsed through the real schema-1 parser;
    its standard command omitted `--effort` and produced a 71-character route
    fingerprint. The documented two-pair token evidence parsed and correctly
    produced a bound `no-go` result.
  - The six-pair task-free cost diagnostic parsed through the new cost scorer:
    its estimated-cost interval was 72.55% to 78.84%, but the scorer correctly
    returned `no-go` for insufficient pairs/slices/interval/provenance.
  - The subsequent 30-pair run completed 60/60 arms and blind quality was
    30/30 for both configurations, but cost CI and exact quality bounds kept
    the decision at `no-go`.
  - The balanced 150-pair cost run failed only the 15% CI-lower-bound gate; the
    predeclared 90-pair low-target qualification then passed every machine gate
    with a 46.28% to 64.00% cost-savings interval and -4.02% to +4.02% quality
    interval. Scope remains explicit low-route opt-in only.
  - Offline sdist/wheel build succeeded; both distributions include
    `src/weightclass/examples/claude_cost_focused_policy.json`. A clean local
    wheel install emitted the same policy through `wclass example-policy`, and
    the real schema-1 route command selected its low Claude route, used stdin
    delivery, and emitted a 71-character review fingerprint.
  - Approved Claude measurement used the vendor CLI/network. No credential or
    secret-bearing file was read, and no task/response/workspace was retained.
  - Main CI run `31660433300` passed on release commit `09b92c0`: Python
    3.10-3.14, macOS 3.10/3.14 boundaries, lint, strict typing, build, strict
    metadata, and extracted-sdist isolation.
  - Release workflow `31660580129` passed on annotated tag `v0.10.0` and
    published the exact immutable candidate. PyPI exposes one wheel and one
    sdist; independent downloads matched their published SHA-256 values.
  - PyPI wheel SHA-256:
    `5fd21911bbe2fe43d1749a27324cc0690b079a98ea3144db6a258d7ddc2cc674`.
    PyPI sdist SHA-256:
    `65d19d4b5887913d933dd80d63d9f0789ef00e5375605bc491fe8a03e999603e`.
  - A clean PyPI install reported `weightclass 0.10.0`, classified the low
    smoke correctly, and emitted the packaged cost-focused policy.
  - Formula-scoped `brew style`, strict audit, source reinstall, `brew test`,
    and direct classification/example-policy smokes passed. The installed
    `/opt/homebrew/bin/wclass` reports `weightclass 0.10.0`.
  - Post-release source-formula CI run `31661083029` passed on commit
    `b29950a`: Python 3.10-3.14, macOS boundaries, lint, strict typing, build,
    metadata, and extracted-sdist isolation.

- Fresh pre-release verification of `0.9.0` on 2026-08-12:
  - Python 3.14.6 and Python 3.10.20 full `unittest` suites with
    `ResourceWarning` as an error: 738 tests passed on each interpreter.
  - Full pytest: 738 tests and 806 subtests passed; two existing Python 3.14 tar
    extraction deprecation warnings remain.
  - Ruff 0.16.2 check/format, strict mypy over 93 source files, and
    `git diff --check`: clean.
  - The `0.9.0` wheel/sdist build, strict Twine metadata check, and
    extracted-sdist isolation suite passed; 733 tests passed with 11 platform
    skips.
  - Release-tool installation and build isolation used the public Python
    package index within the approved release scope. No secret-bearing file was
    accessed.
- Final CI run `31566006243` passed on the tagged commit: Python 3.10 through
  3.14, macOS routing boundaries on Python 3.10 and 3.14, lint, strict typing,
  build, strict Twine metadata, and distribution-isolation checks.
- Release workflow `31566160612` built one immutable Python 3.13 candidate,
  revalidated it on Python 3.10 and 3.14 plus the macOS boundaries, and
  published those exact artifacts through PyPI Trusted Publishing.
- PyPI reports the canonical `0.9.0` sdist SHA-256 as
  `9f0c70cc4150a793ea99cfa51c878663a5575dde33fe8140ed6569d93b7b7d21`;
  an independent download matched it.
- A fresh virtual environment installed `weightclass==0.9.0` from the explicit
  PyPI simple index; `wclass --version` reported `weightclass 0.9.0` and the
  classification smoke test returned `{"tier": "low"}`.
- Homebrew verification passed for the `weightclass` formula: formula-scoped
  style and strict audit, `brew reinstall --build-from-source`, `brew test`, and
  direct low/high classification smokes. `/opt/homebrew/bin/wclass --version`
  reports `weightclass 0.9.0`.
  The full tap style check has an unrelated existing `relay.rb` ordering
  violation; do not change it as part of weightclass maintenance.

## Blockers & Open Questions

- No known blocker or mandatory release/deployment step remains for `0.13.0`.
- The cost-recommendation worktree is deliberately unreleased and uncommitted.
  Review the diff before deciding its version, commit, or deployment. Do not
  republish immutable `0.13.0`.
- GitHub repository settings were updated and re-read through the API on
  2026-08-13. The active `Protect version tags` ruleset targets `refs/tags/v*`,
  has no bypass actor, and blocks updates and deletions. The `pypi` environment
  has one required user reviewer. Repository code also enforces ancestry as
  defense in depth; do not remove either layer.
- The hash-pinned release closure passed the live Ubuntu tag workflow using
  the pinned Python 3.13 binary-wheel target and complete transitive closure.
- No promotion-grade paired provider token-savings evidence has been collected. Do not
  add an `efficient` posture or change built-in/schema-2 effort behavior based
  on the exploratory pilot. A fresh, blind, fingerprint-bound evidence set
  must pass the offline token gate first.
- Codex, `agy`, and Grok cost-focused examples are unqualified experiments.
  Do not promote them, infer provider pricing, or claim savings until each
  vendor independently passes the applicable aggregate gates.
- Real installed-Claude compatibility under the Darwin sandbox was not tested
  because that would invoke an external runtime/network boundary.
- Optional future work: collect real-user routing feedback; qualify a concrete
  external runtime only with independently reviewed evidence; update pinned
  GitHub Actions only after reviewing upstream migrations.

## Next Steps

1. Review and commit the unreleased cost-recommendation diff. If publishing,
   choose a new version, update release notes/HANDOFF, and run the normal
   immutable release process; never overwrite `0.13.0`.
2. Keep the cost-focused Claude policy explicit opt-in only. Collect real-user
   compatibility feedback without task telemetry, provider-price inference, or
   a claim about actual bills. Do not change built-ins, standard/high routes,
   schema 2, or posture vocabulary from the low-target result.
3. Any future token-efficiency candidate still needs fresh task-free paired
   evidence and must pass the separate raw-token gate; the cost `go` does not
   satisfy it.
4. Evaluate the Codex, `agy`, and Grok cost-focused examples independently
   before any promotion; do not combine vendor results or infer pricing.
5. Re-enable Linux Claude semantic triage only after reviewing a concrete
   filesystem-containment command and its process-tree boundary.
6. Preserve Protocol 1 compatibility, explicit cross-vendor opt-in, task
   no-retention, and the single-reviewed-child boundary.

## Resume Prompt

Open `/Users/jinhongan/Desktop/subscription-agent-router`, read `HANDOFF.md`
and `AGENTS.md`, then continue from: `weightclass 0.13.0 remains the immutable
published release. The current main worktree has an uncommitted, unreleased
same-vendor advisory cost router: review-cost-profile plus recommend, strict
cost/qualification schemas, full evidence fingerprints, fixed conservative
gates, explicit abstention, provider capability/task-delivery receipts, docs,
and eight focused tests. Built-ins and execution paths are unchanged. Python
3.10/3.14 each passed 808 tests; Ruff, strict mypy, compileall, build, strict
Twine, extracted-sdist isolation, and a clean-wheel recommendation smoke are
green. Review the diff before choosing a new version/commit/release. Do not
republish 0.13.0.`
