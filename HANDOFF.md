# Handoff

_Last updated: 2026-08-31 KST by Codex_

_Flexible advisory vendor support follow-up: 2026-08-23 KST._

## Advisory runtime cleanup follow-up (merged, unreleased)

Implementation PR #155 merged at `7db1aff`. The package version remains `0.25.0`; this source
follow-up has not been tagged or published as a new PyPI/Homebrew release.

The merged change:

- gives managed project verifiers a fixed environment allowlist plus workflow, a new session, and
  process-group timeout cleanup while explicitly retaining external container/jail confinement as
  the only honest hostile-code sandbox boundary;
- caches one-shot leader-exit observations, distinguishes live `EPERM` from the bounded Darwin
  exit-notification race, and keeps numeric PGID targets only while child-status ownership remains;
- moves selector construction inside owned cleanup for verifier Git, bounded capture, safe Git, and
  task-free preflight, including the case where an exited leader's descendant retains a pipe;
- blocks quiet bounded-capture and Git calls on kernel events until their actual deadline instead of
  waking every 100 ms;
- centralizes managed schema/workflow/vendor/role/default-timeout constants, removes unreachable
  path-based Skill walkers, retains and format-checks the complete descriptor-based historical
  bundle ledger, and documents `install-skill` as the sole explicit vendor-recognized-path write.

Two scrubbed GLM reviews completed through `packet-ask`. The architecture packet contained nine
public files, 121,575 bytes, and SHA-256
`f27d9c44cd02d9e37d7325a1fd7fbdd5d5f8d74b68446d689ce864a4f4e428f1`; it returned in 525.6
seconds. The final-state review packet contained 16 public files, 175,104 bytes, and SHA-256
`6f64b4c5895e8b8811eda87331169c2cad3acf98acc2e9dffab8ec2a78c58c54`; it returned in 726.6
seconds. A direct `--unstaged` inspect was rejected locally with packet policy code 10 before any
provider call, so the successful review used an explicit file list. No secret value, task, home
path, or repository-private state was sent. Provider output was treated as untrusted; confirmed
kqueue one-shot, selector-construction, and descendant-held-pipe findings were fixed and retested.
A separate fresh-context Sol bypass review found the last managed-verifier Git selector lifecycle
gap, which was also fixed.

Final verification:

- protected verifier and final full discovery: 1,514 tests passed with 35 skips;
- extracted-sdist isolation: 1,507 tests passed with 79 skips;
- advisory suite: 300 tests passed with 15 skips; speculative suite: 195 tests passed with 10 skips;
- Ruff check and format-check pass 234 files, strict mypy passes 194 source files, and compileall plus
  `git diff --check` pass;
- all eight PR checks pass on Python 3.10-3.14 and macOS Python 3.10/3.14;
- final Codex Security diff scan `f397b043-989b-47db-98c3-adaa6a556c90` reviewed all nine changed
  production source files with complete coverage and retained no finding. It used 2,455,815 total
  tokens, including 2,442,752 cached input tokens. TAC status was unavailable because the connector
  was not connected.

Twenty-run cold import observations moved `managed_cli` from 28.2 ms to 27.2 ms and
`managed_advisory` from 58.8 ms to 52.9 ms; these noisy local measurements are not product
guarantees. The durable performance regression asserts actual selector deadlines instead of fixed
100 ms polling. No real task or campaign was dispatched, no credential/vendor configuration was
read, and no campaign or usage record was changed.

## Advisory execution hardening and startup work (released in 0.25.0)

The performance, security, and structure follow-up is implemented, reviewed, and published. Exact
command, profile, campaign, and internal consult execution now require explicit task-egress
confirmation before task access, lane allocation, output-directory creation, or child start. This
is an intentional breaking change for callers that previously omitted `--confirm-task-egress`.

The release also:

- maps low-level lane failures to fixed redacted JSON codes and retains process-group ownership
  through timeout, cancellation, and output overflow so resistant descendants are terminated even
  after their leader exits;
- uses shared bounded I/O and bounded Git execution with hooks, fsmonitor, and optional locks
  disabled, and caps generated patches at 64 MiB;
- resolves managed verifier input once to an immutable Git object, validates its type and size, and
  streams that exact object under the configured cap;
- makes campaign JSONL scanning linear, hardens the owner-private workspace registry and advisory
  state-root namespace, and publishes Agent Skills through retained directory descriptors with
  atomic rollback;
- splits the managed CLI entrypoints, removes global `sys.argv` mutation, defers service imports,
  and preserves the documented wrappers and patch seams.

Seven-run local medians for campaign-record lookup improved from 5.6 ms to 0.105 ms at 100 KiB,
89.7 ms to 0.433 ms at 500 KiB, and 822.5 ms to 0.837 ms at 1 MiB. `wclass --help` median/p95
improved from 81.823/97.532 ms to 48.476/50.768 ms; `wclass-advisory status --help` improved from
80.447/110.625 ms to 37.635/41.865 ms. A fresh task-free managed status measured
55.034/56.236 ms median/p95 after the change.

Independent review found and fixed direct-script imports, orphan descendants after leader exit,
missing internal-consult confirmation, mutable managed-verifier references, and a symlink-ancestor
Agent Skill install bypass. The final complete 24-file Codex Security diff scan
`14e9f986-84a1-451c-925d-9de82fe3fd63` reviewed all changed production source files and retained no
finding. The issue-tracker connector was unavailable, so tracker duplicate search was not run. The
scan used 12,558,928 total tokens, including 12,512,256 cached input tokens.

Release evidence:

- implementation PR #151 merged at reviewed release commit
  `7367f41b259f2cfc141403a3b7edb0aceb382176`; tag `v0.25.0` points to that exact commit;
- Release run `33356402517` passed the immutable Python 3.13 build, Python 3.10/3.14 candidate
  validation, macOS 3.10/3.14 routing boundaries, protected PyPI approval, and exact publication;
- the final `v0.25.0` GitHub Release is published and marked Latest. The release workflow now
  creates future final GitHub Releases only after the protected PyPI publish job succeeds;
- PyPI exposes one wheel and one non-yanked canonical sdist at
  `https://files.pythonhosted.org/packages/fa/ba/4419948743082f00a1d80498df8869c006c5ae4c1e952b6587a2105e60e9/weightclass-0.25.0.tar.gz`
  with SHA-256 `0621c94d57fd45621a376364a6b8d65ab4f0a359d700733137d74147c6f56522`;
- source-formula PR #152 merged at `882f0a3`; tap PR `ictechgy/homebrew-tap#33` merged at
  `e413394`. Homebrew style, strict audit, source reinstall, `brew test`, and exact binary smokes
  pass;
- the user-level uv tool and exact Homebrew binary both report `0.25.0`. Codex and Claude packaged
  advisory skills are exact-current, and a dry run plans no upgrade.

The protected verifier and separate full discovery each pass 1,496 tests with 35 skips. The
advisory suite passes 285 tests with 15 skips; the speculative suite passes 195 tests with 10 skips.
Ruff check and format-check pass 232 files, strict mypy passes 193 source files, compileall and
`git diff --check` pass, Twine strict accepts both distributions, and extracted-sdist isolation
passes 1,478 tests with 76 skips. No real task was dispatched during release, packaging, or local
installation, and no campaign, usage record, policy decision, credential, or vendor configuration
was read or changed.

## Security, performance, and structure hardening (released in 0.24.0)

A repository-wide Standard Codex Security scan at `f1c7be3` completed as scan
`f4db11e6-fc50-4045-bde9-4e6d901cd99c` with three medium and two low findings. Coverage is
intentionally partial at 65/248 fully audited files, focused on production execution, advisory,
parser/state, and release surfaces. The derived hardening portfolio selects shared bounded
primitives over either scattered guards or the compatibility-blocked verified-object redesign.

The implementation candidate:

- disables executable repository-local Git fsmonitor configuration for advisory-owned Git calls;
- observes protocol-1 delegation runtimes before task access and compares identity immediately
  before spawn;
- bounds vendor/verifier capture, handles interruption through owned group cleanup, and cancels
  parallel jobs without waiting for their full eight-hour ceiling;
- releases triage numeric signal targets after child-status loss;
- streams campaign/legacy logs with record limits, bounds price and integer parsing, and rejects
  final symlink inputs through no-follow readers;
- defers the 5k-line speculative runner on task-free status. A 30-process cold measurement moved
  status median/p95 from 59.205/61.397 ms to 52.358/54.198 ms (about 11.6% lower).

Three packet-ask GLM reviews were attempted with 103 KiB, 47 KiB, and 15.5 KiB scrubbed public
packets; all reached the provider's 600-second limit without returning review text. No repository,
task, local state, or credential content was sent. Focused hardening plus existing regression suites
pass 435 tests with 10 skips; the new direct hardening suite passes 12 tests. A complete 11-file
terminal Security diff scan found two cancellation/status-loss gaps, both were fixed and
independently re-reviewed, and the sealed final report has no surviving finding. The protected
verifier and a separate full unittest discovery each pass 1,441 tests with 35 skips. Ruff check and
format-check pass all 217 files, strict mypy passes all 178 source files, and compileall plus
`git diff --check` pass. The implementation and release are complete:

- implementation PR #147 merged at `7521c84`; release PR #148 and tag `v0.24.0` point to the
  exact reviewed release commit `729fd8f`;
- Release run `33344604461` passed the immutable Python 3.13 build, macOS 3.10/3.14 boundaries,
  Python 3.10/3.14 candidate validation, protected PyPI approval, and exact publication;
- PyPI exposes one wheel and one non-yanked canonical sdist at
  `https://files.pythonhosted.org/packages/6c/eb/9e86837523d098265b218b4a0addf7f6d8a12e2b3e3a74d5b04b4336d665/weightclass-0.24.0.tar.gz`
  with SHA-256 `1bc0b377199c011e1ff6e62fec1c1f48e38b5e48e946e50a65d31d594cf02f2c`;
- source-formula PR #149 and tap PR `ictechgy/homebrew-tap#32` are merged. Targeted Homebrew
  style, strict audit, source reinstall, `brew test`, and exact binary smoke pass;
- the user-level uv tool and Homebrew source installation both report `0.24.0`. Codex and Claude
  packaged advisory skills are already exact-current with no upgrade planned.

No real task was dispatched, no existing campaign or usage record was rewritten, and no policy
decision was authorized during the release and packaging work.

## Usage guidance and advisory operating recommendation (released in 0.23.0)

The released implementation adds only task-free, additive guidance. It does not rewrite the usage
store, existing campaign manifests, records, gates, or policy decisions.

- `wclass usage enable/report` exposes closed onboarding reasons and next actions for missing
  baseline weights, unweighted observed buckets, prospective-only historical gaps, and empty
  evidence;
- usage reports expose canonical-current-state capacity against the existing 4,096-bucket and
  256 KiB limits and explicitly require one caller-defined relative unit while admitting that
  consistency is not verified;
- usage management errors retain `invalid_input` with an operation-level, value-free reason code;
- advisory status derives a closed diagnostic-only `operating_recommendation`. Healthy Shape-B
  advice with rejected retries requests review of a non-authorizing Shape A+B design without
  changing the existing campaign or authorizing a pilot dispatch or policy decision.

Applying the source status to the existing Codex review population reports 14 usable tasks, nine
advised failures, one accepted retry among nine attempts, healthy advice delivery, and
`review_shape_a_b_design`. The same receipt remains `decision_state:abstain` and
`policy_decision_allowed:false`; no task was dispatched and no campaign state was written.

Focused usage and portfolio tests pass at 53 tests. The protected verifier and a separate unittest
discovery each pass all 1,429 tests with 35 skips. An independent scrubbed-diff GLM review found no
Critical or High issue. Follow-up changes correct the canonical-capacity label, make the existing
sealed stopping-rule boundary explicit, and add missing decision-tree, byte-threshold, onboarding,
and enable-failure regressions. Ruff check and format-check pass all 215 files,
strict mypy passes all 176 source files, compileall and `git diff --check` pass. The implementation
is merged in PR #143 at merge commit `9663d25`. The `0.23.0` release PR, tag, publication,
Homebrew promotion, and local upgrade are complete:

- release PR #144 merged at `90c7d94`, and tag `v0.23.0` points to that exact reviewed main commit;
- Release run `33323065111` passed the immutable Python 3.13 build, macOS 3.10/3.14 boundaries,
  Python 3.10/3.14 candidate validation, protected PyPI approval, and exact publication;
- PyPI exposes one wheel and one non-yanked canonical sdist at
  `https://files.pythonhosted.org/packages/fa/b2/995e2b54f7e401c5d452f1fba3187337a5706080624bd8a5cd87862cb1e9/weightclass-0.23.0.tar.gz`
  with SHA-256 `90d9bce1cf7d144248ef9a3d67edf1003f678903f97e25fa2e6560917036c871`;
- source-formula PR #145 and tap PR `ictechgy/homebrew-tap#31` are merged. Targeted Homebrew style,
  strict audit, source reinstall, `brew test`, and exact advisory smoke pass; the whole-tap style
  command retains one unrelated pre-existing ordering warning in `Formula/relay.rb`;
- the user-level uv tool and Homebrew source installation both report `0.23.0`. Codex and Claude
  packaged advisory skills are exact-current, and both installed CLI paths pass usage/advisory
  guidance smokes.

No real task was dispatched, no existing campaign or usage record was rewritten, and no policy
decision was authorized in this batch. The next safe action is to review a non-authorizing Shape A+B
pilot contract separately; do not change the sealed Shape-B floor, order, or stopping rules.

## Advisory next-experiment instrumentation (2026-08-30)

The next measurement tools are implemented, merged in PR #139, and released as
weightclass 0.22.0 without modifying existing campaign manifests, records,
gates, or task data. Release PR #140, source-formula PR #141, and tap PR
`ictechgy/homebrew-tap#30` are merged; Release run `33260915491` succeeded.

- task-free status now reports the sealed arm, coverage-aware advice
  character/flag counts, failure stages separated by cheap/retry/expensive
  attempts, and a closed cheap-stage-to-retry-outcome transition matrix;
- missing legacy diagnostic fields remain explicitly unrecorded or `unknown`
  rather than becoming false values;
- `docs/advisory-next-experiments.md` prioritizes naturally occurring Codex
  review tasks, defines a separate Shape A+B population, and uses separate
  opaque state roots for implementation cohorts without persisting task labels;
- README and the campaign contract link the new operating plan while preserving
  `policy_decision_allowed:false` and the existing statistical gates.

Applying the new source status locally to the existing Codex review population
shows that all nine failure-stage advice calls produced nonempty, untruncated,
successfully extracted advice. Of eight cheap verification failures, one retry
passed, four remained verification failures, and three moved to the read-only
`result` contract; the one cheap `result` failure remained a `result` failure.
This is task-free evidence that advice delivery itself is healthy while fresh
retry result shaping remains a likely bottleneck. Older implementation rows
retain visible `unknown` stage coverage instead of being reclassified.

No real task was dispatched and no Shape A+B or cohort population was created
in this batch. Existing populations remain unchanged. Validation passes the
protected verifier and a separate full unittest run at 1,422 tests with 35
skips, compileall, Ruff check/format over 215 files, and strict mypy over 176
source files.

## Completed advisory hardening batch (2026-08-29)

The implementation is **applied, independently re-reviewed, fully validated,
merged in PR #135, and released as weightclass 0.21.0**. Release PR #136,
source-formula PR #137, and tap PR `ictechgy/homebrew-tap#29` are merged.

### Managed workflow evidence

- The protected prospective acceptance and verifier were committed first as
  `36fdd3e` (`test: preregister advisory hardening batch`). Before dispatch,
  focused acceptance failed with the seven intended missing-feature failures;
  `.weightclass/verify` then passed all 1,415 baseline tests with 35 skips and
  returned the required exit `42`.
- Exactly one approved managed Codex implementation dispatch was run with an
  owner-only temporary task file and `--confirm-task-egress`. The temporary
  task file was deleted by the command trap. Do **not** dispatch or retry this
  task again.
- Codex cheap passed (958.5-second child, 138.7-second verifier). The accepted
  622-line patch is retained at
  `~/Library/Application Support/weightclass/advisory-v1/codex-results/spec-cheap-ydlc_srl.patch`.
  It was inspected, confirmed not to touch the protected files, checked with
  `git apply --check`, and applied.
- `.weightclass/verify` and `tests/test_advisory_hardening_batch.py` remained
  byte-unchanged through the observed provider candidate and final independent
  review. After PR CI isolated the preregistered test's sole Ruff formatting
  difference, the owner explicitly authorized a separate formatting-only
  follow-up; `.weightclass/verify` remains unchanged.

### Integrated changes

The implementation commit modifies ten files; the preceding preregistration
commit adds or updates the two protected acceptance files:

- product/docs: `HANDOFF.md`, `README.md`;
- implementation: `src/weightclass/cli.py`, `router.py`, `v2.py`,
  `advisory/advisory_campaign.py`, and `advisory/managed_advisory.py`;
- ordinary regression tests: `tests/test_router.py`,
  `tests/test_usage_aggregation.py`, and
  `tests/test_advisory_campaign_lanes.py`.
- protected acceptance: `.weightclass/verify` and
  `tests/test_advisory_hardening_batch.py`.

The applied behavior is the bounded approved batch: opt-in observed executable
binding for explicit custom schema-1 policy routes; Bedrock as a V2 destination
without a Kiro/source mapping; additional CLI lazy loading and deferred usage
store defaults; count-only managed validation; corrected usage-store HANDOFF
wording; and a marked hardened Kiro custom-policy README section. It does **not**
implement fd/verified-object execution or a built-in Kiro adapter. The
post-observation path-based spawn race remains documented.

An independent read-only review found type/format regressions, higher-route
executable-path disclosure through bound escalation, CR/splitlines divergence
in the streaming counter, an unredacted default-home lookup failure, and docs
gaps. Follow-up changes address those findings and add ordinary regressions:

- binding-aware fingerprints now use a typed helper while preserving unbound
  canonical bytes;
- escalation retains its reusable bound fingerprint but does not print the
  higher executable identity/path;
- `_iter_bound_records` streams with legacy `bytes.splitlines()` boundaries,
  including CR/CRLF and trailing-partial handling;
- deferred default usage-path failures map to redacted `invalid_input`;
- Bedrock destination-only, custom binding exit behavior, and escalation null
  reasons are documented; non-symlink bound paths are normalized.

The requested independent re-review after those fixes is complete. It confirmed
the binding/fingerprint privacy, fail-closed ordering, lazy symbol loading, and
Bedrock cross-provider behavior. It also found and fixed two final concrete
regressions in ordinary files without changing the protected acceptance:

- the streaming counter now recognizes only CR and LF byte boundaries, exactly
  matching legacy `bytes.splitlines()` rather than also splitting on unrelated
  control bytes or the UTF-8 continuation byte `0x85`;
- schema-3 default usage-store home lookup failures now return the redacted
  `usage_unavailable` diagnostic before task access instead of allowing a
  `RuntimeError` to escape.

### Validation completed after the follow-up fixes

- `uvx --offline mypy==2.3.0 --strict src tests`:
  `Success: no issues found in 176 source files`.
- Gated prospective acceptance plus router, V2, CLI startup, usage aggregation,
  advisory lane/managed, and productization suites: 255 tests passed with 7
  skips in 25.934 seconds.
- `python3 -m compileall -q src tests tools` passed.
- CI-pinned Ruff 0.16.2 was available from the uv offline cache. Targeted
  `ruff check --fix` fixed two import-order errors and targeted `ruff format`
  reformatted `src/weightclass/cli.py` only.
- An earlier post-patch targeted run also passed 252 tests with 7 skips. The
  latest 255-test result above supersedes it.

### Final validation after independent re-review

- `./.weightclass/verify`: 1,419 tests passed with 35 skips, exit `0`.
- Full `unittest` discovery with `ResourceWarning` promoted to an error: 1,419
  tests passed with 35 skips.
- `python3 -m compileall -q src tests tools` passed.
- `uvx --offline ruff==0.16.2 check .` passed.
- `uvx --offline mypy==2.3.0 --strict src tests` passed with no issues in 176
  source files.
- Focused final regressions plus protected acceptance passed: 10 tests with 7
  skips.
- `git diff --check` passed. `.weightclass/verify` remains byte-equal to the
  preregistered commit; the acceptance test has only the separately authorized
  Ruff formatting follow-up described above.
- Full Ruff format-check passes all files after that owner-authorized formatting
  follow-up.

### Next safe action

1. PR #135 is the implementation integration record for this batch; release PR
   #136, source-formula PR #137, tap PR #29, and Release run `33254537431`
   complete the publication record.
2. The implementation, independent re-review, focused regressions, full
   verifier, and release-style gates are complete; do not repeat the managed
   advisory dispatch.

## Goal

- Maintain `weightclass` as a public, local router that can discover and let
  the user select installed Codex, Claude Code, Antigravity (`agy`), and Grok
  agents/models/efforts without hand-writing vendor commands.
- Allow explicit reviewed cross-agent/schema-3 native execution while keeping
  the default source-vendor boundary and one-foreground-child contract.
- Measure whether routing helps cost using opt-in local aggregate counters and
  user-supplied relative weights, without inferring prices, subscription usage,
  entitlement, or persisting task content.

## Current Status

- Project root: the current repository checkout.
- **`weightclass 0.16.2` is published on PyPI.** Release run `32798010700`
  passed immutable build, macOS 3.10/3.14, candidate validation on Python
  3.10/3.14, and exact trusted publication. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/6c/44/5007d87eb3d17dd6d87ae23bbf9c3d56e6781895c1254215cf18777502cf/weightclass-0.16.2.tar.gz`
  - sha256: `5f6173e4fb7aeb625b2d196a8ad36b76a8ccce7e2330b3e0678c65c57c869786`
- **`weightclass 0.17.0` is published on PyPI and Homebrew.** Release run
  `32807440505` passed immutable build, macOS 3.10/3.14, candidate validation
  on Python 3.10/3.14, and exact trusted publication. PyPI has one wheel and
  one sdist, neither yanked. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/74/df/d1060ba9b4fbcde82f66bd77a041ab04ca7abfc916863499e02a434956bd/weightclass-0.17.0.tar.gz`
  - sha256: `12f2679a53314750be30d4bd2cbe225d7561c137c26279619b72b3f7912a6d88`
- **`weightclass 0.17.1` is published on PyPI and Homebrew.** Release run
  `32820191068` passed immutable build, macOS 3.10/3.14, candidate validation
  on Python 3.10/3.14, and exact trusted publication. PyPI has one wheel and
  one sdist, neither yanked. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/25/8e/0c386f5c48db429eec8874a5b8090b4387688f2bc1678188ac451a111927/weightclass-0.17.1.tar.gz`
  - sha256: `3c6f456a962537f4e3f72647c083556faa0c70854d60f01ee9766734b1837333`
- **`weightclass 0.17.2` is published on PyPI and Homebrew.** Release run
  `32839986511` passed immutable build, macOS 3.10/3.14, candidate validation
  on Python 3.10/3.14, and exact trusted publication. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/4f/07/37b45d7a06c27dc300a0c170143fa053d767e3df3f66ead1833511de1fdf/weightclass-0.17.2.tar.gz`
  - sha256: `8ec81718ddc363412438e31dd9dac766e14847c6144fd46f08cfc4296ce13722`
- **`weightclass 0.17.3` is published on PyPI and Homebrew.** Release run
  `32861214923` passed immutable build, macOS 3.10/3.14, candidate validation
  on Python 3.10/3.14, and exact trusted publication. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/85/e3/81a154a49de27e765f2e14ba7b2f3e4c75283eb958e9ff349ce68ed27d72/weightclass-0.17.3.tar.gz`
  - sha256: `f54cb17162fe040428331abf7e819949d9018309816c78956dbf61d0cbd54096`
- **`weightclass 0.17.4` is published on PyPI and Homebrew.** Release run
  `32942266996` passed immutable build, macOS 3.10/3.14, candidate validation
  on Python 3.10/3.14, and exact trusted publication. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/e0/dd/7e122f748c56c99058f8e39e8558d0b4a804290d16fa9d5e80a0a6136ed0/weightclass-0.17.4.tar.gz`
  - sha256: `133fe963b54ba45bfe7419911a5c7b5f06f0ac16f8d8b7d66b701a0399fe35ef`
- **`weightclass 0.17.5` is published on PyPI but intentionally not promoted
  to Homebrew.** Release run `32960180505` passed every immutable gate and
  exact publication. Installed migration then exposed a safe fail-closed
  generation-fingerprint mismatch, so source formula PR #106 was closed and
  0.17.6 supersedes it. The 0.17.5 sdist is:
  - url: `https://files.pythonhosted.org/packages/1f/ff/b3149e13a08492fe760f141794d9ca81ab80e6bbe68061144b76428097a1/weightclass-0.17.5.tar.gz`
  - sha256: `d23979793552f0d14e415456a67b14934b3e70f85516191d8b1c87e79722edb6`
- **`weightclass 0.17.6` is published on PyPI and Homebrew.** Implementation
  PR #107, source-formula PR #108, and tap PR `ictechgy/homebrew-tap#22` are
  merged. Release run `32963131319` passed immutable build, macOS 3.10/3.14,
  candidate validation on Python 3.10/3.14, and exact trusted publication.
  The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/c4/e2/40e23b2f217078b97ce553df774b779d5dfa05bb98518d60f47312c38093/weightclass-0.17.6.tar.gz`
  - sha256: `08aa58d9c6c8ca944aa05b8707430312d3527df4a13781686d3a53a02d85b90f`
- **`weightclass 0.17.7` is published on PyPI and Homebrew.** Implementation
  PR #110, source-formula PR #111, and tap PR `ictechgy/homebrew-tap#23` are
  merged. Release run `32967606531` passed immutable build, macOS 3.10/3.14,
  candidate validation on Python 3.10/3.14, and exact trusted publication.
  The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/7e/97/107a9d27655032e87fcc338793343e2c0e13230979af57f1d852882c6209/weightclass-0.17.7.tar.gz`
  - sha256: `d1f0c7f93f284a32b2810c9ebbae220a1ae22d5126fe6358a592dbd2f711011d`
- **`weightclass 0.17.8` is published on PyPI and Homebrew.** Implementation
  PR #113, source-formula PR #114, and tap PR `ictechgy/homebrew-tap#24` are
  merged. Release run `32981140244` passed immutable build, macOS 3.10/3.14,
  candidate validation on Python 3.10/3.14, manual PyPI environment approval,
  and exact trusted publication. The user-level uv tool and Homebrew source
  install report 0.17.8, `brew test` passes, and exact 0.17.7 personal skills
  upgraded to the packaged 0.17.8 bundle for both Codex and Claude. The
  canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/04/ee/b8ccb1ef866fac82552a20193978be6a69f21c0e4fb25600fe78013e0479/weightclass-0.17.8.tar.gz`
  - sha256: `75b6ab98a84660a637cfc583f0b93b2b4bb07db65420981b7b2f5bda6300b4ef`
- **`weightclass 0.17.9` is published on PyPI and Homebrew.** Implementation
  PR #116, source-formula PR #117, and tap PR
  `ictechgy/homebrew-tap#25` are merged. Release run `33029726299` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. A complete seven-file terminal Security diff scan found no
  reportable findings. The user-level uv tool and Homebrew source install both
  report 0.17.9, `brew test` passes, and exact 0.17.8 Codex/Claude skills
  upgraded to managed onboarding 11. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/b2/b3/e9d011103d7dde79614064359d34effb724babe32842a66387d328365624/weightclass-0.17.9.tar.gz`
  - sha256: `634dcbe9f66934f15262611115ca3e7dace51712cb9883fd1e1254f6f6d19a75`
- **`weightclass 0.18.0` is published on PyPI and Homebrew.** Implementation
  PR #119, release PR #120, source-formula PR #121, and tap PR
  `ictechgy/homebrew-tap#26` are merged. Release run `33046470848` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. The user-level uv tool and Homebrew source install both report
  0.18.0, `brew test` passes, and exact 0.17.9 Codex/Claude skills upgraded to
  managed onboarding 12. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/af/59/127e1df37ea364cc34161b2916c0d319fe197bcc403e03db176d79f72b6a/weightclass-0.18.0.tar.gz`
  - sha256: `1b6813901cdad9ddfd8fc9508befa5a0138ece89069bcefe3ad444a627d0a60f`
  This release centralizes advisory failure enums,
  adds distinct managed preflight reason codes, additive attempt/advice/metric
  denominators, task-free vendor heartbeat/completion receipts, and explicit
  untrusted evidence framing. A new read-only `consult` path runs exactly one
  selected role without lanes or campaign records; custom providers must pass
  a confirmed task-free conformance check before task inspection. Schema-1
  profile-only `review --consult` makes that route review independent of sealed
  campaign records and renders exact argv plus a child-enforced profile digest.
  `route --explain` adds task-free provenance, delegation-v2 dependencies are
  loaded only for delegation, and isolated `experiment` analysis covers
  conservative sequential stopping, Context Guard 2x2, generator-critic
  brainstorming, and confidence/abstention without changing core routing.
  PR review additionally hardened every internal advisory Python child with an
  isolated, parent-package-pinned bootstrap and bounded experiment input to
  10,000 prevalidated records with recursive-parser failures redacted. Current
  offline gates pass 1,365 unittest tests with 27 skips, Ruff over the
  full source/test/verifier set, strict mypy over 172 source files, wheel/sdist
  build plus strict metadata/isolation checks, and a clean-wheel CLI smoke test.
  PR #119 merged as `fadf362`; its final immutable security diff review found
  no reportable findings after independently rechecking both review-time fixes.
- **`weightclass 0.19.0` is published on PyPI and Homebrew.** Implementation
  PR #123, release PR #124, source-formula PR #125, and tap PR
  `ictechgy/homebrew-tap#27` are merged. Release run `33135546910` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. The user-level uv tool and Homebrew source install both report
  0.19.0, `brew test` passes, and exact 0.18.0 Codex/Claude skills upgraded to
  managed onboarding 13. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/db/ef/f2d98183e68b32e756368874db4236fc8e4e6fbb09f1d1154d781c494921/weightclass-0.19.0.tar.gz`
  - sha256: `04a87d4c87dacb51fc50bd5c10f5168a54715daef363745040a6487e25d975d2`
  This release adds filtered managed status, a
  provenance-bound sealed campaign gate that never authorizes policy, closed
  consult failure stages/reasons with arbitrary internal stderr rejection,
  completion-order consult NDJSON and a 5,400-second default deadline, and an
  explicitly confirmed task-free conformance check before custom-provider
  dispatch task access. Core `wclass run`, existing campaign bytes, and schema-2
  profile shape remain unchanged; the packaged skill advances to onboarding 13
  and recognizes the exact published 0.18.0 bundle for safe upgrade. Current
  offline gates pass 1,374 unittest tests with 27 skips, Ruff, formatting, and
  strict mypy over 172 source files.
  Breaking changes are confined to the explicit advisory companion: sequential
  analysis output advances to schema 2 with signal-oriented decision values,
  schema-2 custom dispatch requires explicit provider-egress confirmation, and
  multi-vendor consult NDJSON is emitted in completion order. Core `wclass`
  routing and its one-child contract are unchanged.
- **`weightclass 0.20.0` is published on PyPI and Homebrew.** Implementation
  PRs #127-#129, release PR #131, source-formula PR #132, and tap PR
  `ictechgy/homebrew-tap#28` are merged. Release run `33166192142` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. The user-level uv tool and Homebrew source install both report
  0.20.0, `brew test` passes, and exact 0.19.0 Codex/Claude skills upgraded to
  managed onboarding 14. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/ec/d5/e968d6fdfa7b7cf722de1793e77ecd7e8e83edb5b8bfceef2c692c62d55a/weightclass-0.20.0.tar.gz`
  - sha256: `f56d135948e7f075943f9d87a90354556b310f5bcd4f631e677775d133857c4e`
- **`weightclass 0.21.0` is published on PyPI and Homebrew.** Implementation PR
  #135, release PR #136, source-formula PR #137, and tap PR
  `ictechgy/homebrew-tap#29` are merged. Release run `33254537431` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. PyPI has exactly one wheel and one sdist, neither yanked. The
  user-level uv tool and Homebrew source install both report 0.21.0, and
  `brew test` passes. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/7d/63/d0d7235590ba420183325fd60cca5b959906530075784757bfc35d9c7aea/weightclass-0.21.0.tar.gz`
  - sha256: `33e060c06c5b9f03e75042e03fd6ff64f2870dc57ccab902961bf5d1e5621dd4`
- **`weightclass 0.22.0` is published on PyPI and Homebrew.** Diagnostics PR
  #139, release PR #140, source-formula PR #141, and tap PR
  `ictechgy/homebrew-tap#30` are merged. Release run `33260915491` passed the
  immutable Python 3.13 build, macOS 3.10/3.14 boundaries, Python 3.10/3.14
  candidate validation, explicit PyPI environment approval, and exact trusted
  publication. PyPI has exactly one wheel and one sdist, neither yanked. The
  user-level uv tool and Homebrew source install both report 0.22.0, and
  `brew test` passes. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/f2/9e/89d41c9b65e72990c66dcf18cd030516136aa21a9290b603b778d342c7f3/weightclass-0.22.0.tar.gz`
  - sha256: `0076a9b8dca20e2e3d2b398821a8e3f5a2383b876e3250007fbcdfc16193c4fc`
- **0.22.0 task-free advisory experiment diagnostics.** Status now exposes the
  sealed arm, coverage-aware advice character/flag counts, failure stages by
  executor attempt, and closed cheap-to-retry transitions without changing any
  campaign byte or gate. The new operating plan keeps Codex review collection,
  Shape A+B, and opaque implementation cohorts in separate populations. Source
  gates pass 1,422 unittest tests with 35 skips, Ruff/format over 215 files,
  strict mypy over 176 source files, extracted-sdist isolation, and clean-wheel
  smoke. No task/provider dispatch or new population was created for this
  release.
- **0.21.0 advisory routing hardening.** PR #135 adds opt-in observed executable
  identity binding for explicit custom schema-1 routes, Bedrock as a
  destination-only V2 provider, additional CLI lazy loading, deferred usage
  defaults, and count-only managed validation. Independent review fixed
  escalation path disclosure, streaming record-boundary divergence, and an
  unredacted schema-3 default-home failure before merge. Final source gates pass
  1,419 unittest tests with 35 skips, Ruff and formatting, strict mypy over 176
  source files, protected acceptance, and a no-reportable-findings security diff
  review. The documented pathname-spawn race remains; this release does not add
  verified-object execution or a built-in Kiro adapter.
- **0.20.0 performance and evidence hardening.** PR #127 (`e5e8865`)
  lazy-loads core/advisory command families,
  streams bounded lane-capacity validation without full record copies, scopes
  custom consult conformance to the selected role, and runs distinct executable
  identities under a fixed four-group provider-check ceiling. Interleaved local
  source-worktree medians improved from 105.81 ms to 64.28 ms for core help,
  105.15 ms to 68.11 ms for schema-1 route, 107.20 ms to 34.22 ms for advisory
  help, and 106.59 ms to 34.04 ms for advisory run help. A synthetic 500-record,
  32-KiB-per-record capacity check reduced traced peak memory from 46.42 MiB to
  0.29 MiB and elapsed time from 27.0 ms to 17.3 ms. These are local comparative
  measurements, not product guarantees.
- PR #128 (`7d7ade7`) adds a separate schema-3 `gate-v1` population that seals
  one nonzero target, metric, alpha, simultaneous-Hoeffding method version, and
  population-rule version. `migrate-gate` creates exactly one primary
  vendor/workflow per managed state root, never copies or rebinds old records,
  recovers only matching empty partial generations, and preserves the current
  pre-gate source generation for explicitly exploratory, never
  promotion-eligible analysis. The packaged optional skill advances to managed
  onboarding 14 and recognizes the exact published 0.19.0 bundle for safe
  upgrade.
- PR #129 (`8a12e53`) adds a bounded parent-owned no-follow snapshot for
  read-only evidence. Execution failure, invalid result, or detected mutation
  now skips the already-doomed second clone and verifier. A valid unchanged
  result still uses the original fresh full handover clone; design review
  rejected exposing a repository-aware verifier to the child workspace or a
  shared object store. Root/symlink/hardlink/mount replacement, nested
  relocation, special files, and cleanup races are covered on Python
  3.10/3.12/3.14. Moving a workspace entirely outside `.work` remains part of
  the documented same-user host-isolation non-goal: the child already has host
  filesystem authority without an external sandbox.
- Current merged-source gates pass 1,408 unittest tests with 28 skips, Ruff and
  formatting over 179 files, and strict mypy over 175 source files. Each of PRs
  #127-#129 passed Linux Python 3.10-3.14, macOS Python 3.10/3.14, lint, typing,
  and package-build CI. Release validation additionally exercised installed
  `migrate-gate` and `campaign-gate` on Python 3.10 and 3.14.
- README PR #134 (`3fc1ebd`) documents the 0.20 gate workflow, source-generation
  exploration, provider-check grouping, consult role scope, rejected-evidence
  snapshot fast path, clean-clone verifier boundary, and onboarding-14 skill
  upgrade. Its complete Linux/macOS CI matrix passed. On this Mac, the
  user-level uv tool and Homebrew entrypoints both report 0.20.0; Codex and
  Claude skills are exact onboarding-14 bundles; and `wclass-advisory-local`
  now resolves to the official user-level `wclass-advisory` entrypoint rather
  than the obsolete standalone cross-project script. A login-shell smoke and
  managed Codex review `doctor` both pass with ten free lanes.
- Release 0.17.8 adds a
  task-free local `cli-check`, installed-CLI status to `doctor`, a separately
  confirmed and non-persisted `provider-check`, and task-before-inspection
  dispatch gating. Failure receipts are schema 2 with fixed vendor/role fields;
  model availability, account limits, configuration, and result-contract
  failures have closed categories. Provider checks always report
  `sample_recorded:false` and never append a campaign record.
- Claude evidence uses workflow-specific `structured-v6` schemas instead of
  the provider-rejected four-workflow union. Existing v5 and older populations
  remain read-only and migrate only by explicit `migrate-evidence`. Agy
  read-only routes omit the rejected `--effort`, preserve effective plan mode,
  and use workflow schemas; Grok evidence executors now use the supported JSON
  Schema. Their changed fingerprints use new empty generations created by
  explicit `migrate-routes --vendor agy` or `migrate-evidence --vendor grok`;
  no old record is rewritten or merged.
- Live task-free acceptance passed Codex 3/3, Claude 3/3, Grok 3/3, and an Agy
  plan/schema probe. Claude's prior two-second exit and Grok's late
  result-contract rejection were reproduced before the fix. Local CLI
  compatibility passes Claude 2.1.246, Codex 0.149.0, agy 1.1.21, and Grok
  1.0.5. The final source gates pass 1,329 unittest tests with 27 skips and
  1,302 pytest tests with 27 skips plus 1,586 subtests; Ruff, format, and strict
  mypy over 167 source files pass.
- Codex Security diff scan `548a6a93-9838-4ce1-a051-9e745af668e5` found one
  low PATH/environment exposure in the first local probe implementation. The
  fix uses executable admission, rejects repository-local built-ins, and
  passes only a minimal environment; the original reproducer no longer creates
  its inherited-secret marker while all four real CLI checks still pass. A
  final post-fix diff scan `4e1d5a3a-f623-473d-ac5b-888c45f8026e` completed
  all ten changed production/package surfaces with no reportable finding. It
  measured 15,804,217 total tokens (15,784,225 input; 15,544,320 cached input).
  The known path-based spawn TOCTOU remains an explicitly documented
  pre-existing architecture residual rather than a 0.17.8 regression.
- Release 0.17.9 closes the reviewed advisory
  follow-ups. Vendor credential families now require exact built-in executable
  basenames; Grok/custom `{{task_file}}` delivery streams through an inherited
  pipe without creating a task pathname or file; setup locks have
  a bounded `managed_setup_busy` result; slow outer timeout reaps transfer to a
  daemon; CLI probes reject relative PATH results and the complete nearest Git
  workspace while still allowing nested user installs outside a repository;
  and the package identity no longer names only Claude/Codex. Managed dispatch
  also binds its internal runner to the parent-loaded package version. A
  concurrent uv/Homebrew/package replacement now stops before task access with
  `managed_runner_version_changed`, instead of mixing an old campaign parent
  with a newly imported result-contract runner. The change preserves raw-output
  non-retention and emits only the fixed reason. The packaged skill documents
  both new errors, advances to managed onboarding 11, and safely recognizes the
  exact published 0.17.8 bundle for upgrade. Focused 81 tests, all 1,341
  unittest tests with 27 skips, 1,314 pytest tests with 27 skips plus 1,587
  subtests, Ruff, formatting, compileall, and strict mypy over 167 source files
  pass.
- `v0.16.0` and `v0.16.1` reached
  immutable candidate validation but failed Python 3.10 installed-route smoke
  before publication; retain both tags and never reuse them. Homebrew 0.16.2 is
  published from tap PR #16 and passed source upgrade plus `brew test`.
- The published 0.15.1 hardening baseline was PR #57 (`84826cb`). Post-release
  repository and advisory follow-ups through merged PR #82 are present on
  `c92ddf9`; the productization work below starts from that commit.
- Implementation/release PRs **#40 through #57 are all merged**. Merge commits:
  `c8a3311` (#40),
  `1a7c91f` (#41), `fe93a5c` (#42), `ec5eb29` (#43), `a763d9c` (#44),
  `887159a` (#45), `bf1ad11` (#46, version bump), `4145745` (#47, downward
  result), `77802ed` (#48, speculative-run measurement tooling), `8ae5a8f`
  (#49, measured cheap-path cost from API-key billing), `7ff2917` (#50, the
  vendor-neutral advisor arm), `a489044` (#51, the release-gate fix below), and
  `1cf2f6a` (#52, the 0.15.1 routing/accounting fixes and policy 4), and
  `59acd7b` (#53, the Homebrew source formula), `97b83c5` (#54, the final
  0.15.1 handoff), `dddf804` (#55, advisory measurement hardening and blind
  evidence), `5265501` (#56, the advisory follow-up handoff), and `84826cb`
  (#57, security/performance hardening). PRs #40-#51 used the recorded review
  loops; #52, #55, and #57 had independent Sol reviews plus full CI, and #53
  had full CI plus formula-specific Homebrew verification.
- The stale linked-worktree metadata for the old detached study worktree was
  pruned. Only the root worktree remains.
- **The paired token study is closed.** See "The routing-economics result" below.
- **`weightclass 0.15.0` is published (2026-08-18).** The annotated tag
  `v0.15.0` points at `a489044`; every `Release` job passed and the maintainer
  approved the `pypi` environment. PyPI holds exactly two artifacts, the wheel
  and the sdist. A PyPI version can never be reused, replaced, or deleted — a
  defect needs `0.15.1`, never a re-upload of `0.15.0`.
- **`weightclass 0.15.1` is published (2026-08-19).** Annotated tag `v0.15.1`
  points at `1cf2f6a`; Release run `32217717269` passed every job and published
  the exact reviewed candidate after the `pypi` environment approval. PyPI has
  exactly one wheel and one sdist, neither yanked. The canonical sdist is:
  - url: `https://files.pythonhosted.org/packages/db/94/533630e84006e7fec7561b99aa1b0a9b0ed9bee46df4d42d411910de6213/weightclass-0.15.1.tar.gz`
  - sha256: `684cedcaa3a3ec75edb9ff44d6f37eba972a2fdf6ffbeafe86c7e1b3d50000dc`
- **The 0.15.1 Homebrew formula is published.** Source PR #53 merged as
  `59acd7b`; tap PR #14 merged as `d1c623a`. The exact copied formula passed Ruby
  syntax, formula-only `brew style`, strict audit, source upgrade, and
  `brew test`. Homebrew has 0.15.1 installed; `brew cleanup` removed the old
  0.14.0 keg and the prior `ca-certificates` 2026-07-16 keg. The user-level and
  Homebrew entrypoints now report 0.15.1.
  `packaging/homebrew/weightclass.rb` in this repo is the source of truth; copy
  it into `ictechgy/homebrew-tap` rather than editing the tap by hand, because
  `brew style`/`brew audit` only apply tap rules to a file already inside a tap.
- Release notes live only in the session scratchpad
  (`release-notes-0.15.0.md`); rewrite them from `git log v0.14.0..main` if
  lost. The two breaking changes must appear there because no commit body
  carries a `BREAKING CHANGE:` trailer: aggregate schema 1 -> 2 (a 0.14.0 build
  cannot read a schema-2 store) and classification policy 2 -> 3 (same input
  can route to a different tier).
### Productization follow-up (2026-08-25)

- The documented `example-policy --low-model` path is wired through the shared
  tier-specific override parser and covered by prospective and ordinary tests.
- Built-in/default schema-1 routes resolve the current `PATH` to one admitted
  absolute executable for review and run. An optional acknowledgement binds
  the reviewed absolute path; without it, a direct run resolves `PATH` again.
  The path-based post-observation replacement race remains open.
- Wheel and sdist verification explicitly reject top-level `tools/` and
  `skills/` content. Installed advisory code and its closed skill bundle live
  only below `weightclass.advisory`, preserving the core/package boundary.
- The README now presents identity, installation, and a quick start before the
  long-running evidence narrative and labels the heuristic classifier
  experimental. The stale root security report was removed; the current
  security record remains `docs/security-performance-followup.md` plus
  `docs/verified-object-execution.md`.
- The advisory implementation campaign used only the Codex cheap arm; it
  passed its prospective verifier, so advisor, retry, and expensive arms did
  not run. Final local verification passed 57 focused and 1,263 full tests.

### Installed advisory companion (released, 2026-08-25)

- Release `0.16.2` moves the existing advisory runtime into the
  `weightclass.advisory` package and adds `wclass-advisory` to the same wheel as
  `wclass`; it is not a separate distribution.
- `wclass-advisory` exposes explicit `review`, `run`, `prune`, `seal`, `report`,
  `portfolio`, and `install-skill` commands. Core `wclass run` remains
  single-child and never selects advisory automatically.
- Profiles, campaigns, verifiers, prices, task files, and campaign roots remain
  caller-supplied. No advisory configuration is discovered or written.
- Distribution availability is not an effectiveness promotion: the existing
  60-task/12-advised-failure gates, separate populations, and abstention rules
  remain unchanged.
- Source PR #84, release-smoke fixes #85 and #86, source-formula PR #87, and
  tap PR `ictechgy/homebrew-tap#16` are merged. PyPI Release run `32798010700`
  and every PR CI job passed. Both user-level and Homebrew installs report
  0.16.2 and provide `wclass` plus `wclass-advisory`.

### Managed advisory onboarding (released in 0.17.0)

- Release `0.17.0` adds explicit managed onboarding
  without changing core `wclass`. `wclass-advisory init` accepts caller-selected
  opaque model/effort labels (or a reviewed schema-2 profile) and an optional
  price table, then creates owner-private implementation, review, research,
  diagnosis, and design campaigns in the platform state directory.
- `doctor`, managed `review`, `dispatch`, `status`, and `cleanup` remove the
  need for public Agent Skill users to supply profile, manifest, verifier,
  pricing, and result-root paths on every task. The low-level explicit
  `run --campaign-root ...` surface remains compatible.
- The package-staged cross-project verifier executes the workflow verifier from
  committed `HEAD`, rejects candidate edits to it, and retains the task-free
  exit-42 baseline contract. Managed dispatch keeps confirmation before task
  inspection, allocates all selected vendor lanes together, and never puts task
  content in router diagnostics or state.
- Agent Skill installation now ships an exact four-file bundle. Ordinary
  conflicts remain protected; explicit `install-skill --upgrade` replaces only
  the exact known 0.16.2 three-file bundle and preserves customized content.
- Local evidence: Ruff and strict mypy passed; the full source suite passed
  1,284 tests with 19 skips in 108.094 seconds; vacuity stayed byte-stable at
  227 identity-redaction failures, 107 passes, 334 leaves. A 0.17.0 wheel/sdist
  passed distribution isolation plus clean-venv `init -> doctor -> review` and
  skill dry-run. Codex Security diff scan
  `68974988-d4eb-4ad4-a392-6352f7fae8cd` covered all seven changed source/package
  files and found no reportable issue; it used parent-only fallback and TAC
  display status was unavailable.
- Source PR #89, source-formula PR #90, and tap PR
  `ictechgy/homebrew-tap#17` are merged. Release run `32807440505` succeeded;
  the PyPI simple index reports 0.17.0 as latest. Clean exact-wheel smoke,
  user-level uv upgrade, Homebrew source upgrade, and `brew test` all passed.
  Both local installation paths report 0.17.0. The active Codex and Claude
  personal skills are byte-identical to the packaged four-file 0.17.0 bundle,
  and the default managed root passes `doctor` for both vendors and all five
  workflows. The Mac-only `wclass-advisory-local` shim remains executable; its
  prior customized skills are recoverable from the Trash backups.
- Do not append to the legacy cross-project implementation campaigns without a
  deliberate repair. Their root Codex and Claude manifests were re-sealed by
  another local process at 12:36 KST and no longer match the pinned August 20
  lane manifests/records. No legacy log was changed during this release work.
  The active 0.17.0 managed campaigns are fresh, separate populations and must
  not be merged with those legacy records.

### Advisory CLI contract follow-up (released in 0.17.1)

- A fresh external-use audit found that 0.17.0 managed `dispatch` and low-level
  forwarding both work, but `run --help` displayed only the three wrapper-owned
  options. That misleading output caused an agent to conclude that repo, task,
  campaign, verifier, advice, and egress-confirmation options were unsupported.
- The 0.17.1 candidate labels `run` as the advanced explicit-campaign surface,
  lists its security-critical forwarded options, and points managed users to
  `dispatch`. `prune`, managed `review`, and `install-skill` now render their
  exact public command names; review help also advertises the explicit-profile
  compatibility form.
- The packaged skill explicitly treats path-requesting `run` instructions or a
  missing-egress conclusion based only on `run --help` as stale session state.
  It requires reloading the installed skill and forbids substituting direct
  implementation for an explicitly requested advisory run.
- Safe skill upgrade now recognizes both the exact published 0.16.2 three-file
  bundle and exact published 0.17.0 four-file bundle. Customized, extra-file,
  and symlinked skills still fail closed. The destination is revalidated against
  the same known version immediately before rename.
- Prospective help/forwarding/stale-skill/upgrade tests pass; all 118 advisory
  tests and the full 1,288-test suite pass with 19 skips. Ruff, format, strict
  mypy over 162 source files, stable 227/107/334 vacuity, 0.17.1 wheel/sdist
  isolation, managed onboarding, every corrected help surface, and an actual
  0.17.0 four-file skill upgrade in a fresh HOME all pass. Codex Security diff
  scan `2459da50-526f-4360-8597-e57470ef67e3` covered all five changed
  source/package files and found no reportable issue; it used parent-only
  fallback and TAC display status was unavailable.
- Source PR #93, source-formula PR #94, and tap PR
  `ictechgy/homebrew-tap#18` are merged. Release run `32820191068` succeeded.
  The user-level uv tool and Homebrew source install both report 0.17.1; exact
  0.17.0 personal skills upgraded through `upgrade_planned` to `upgraded` for
  both Codex and Claude, then matched the packaged bundle byte-for-byte.
  Managed `doctor` remains ready for both vendors and all five workflows, and
  the four corrected help surfaces pass from both uv and Homebrew entrypoints.

### Advisory contention diagnostics (released in 0.17.2)

- Managed lane allocation remains ten lanes per vendor/workflow. A live probe
  with one Codex and one Claude implementation job active observed nine free
  lanes each and atomically leased lane 1 for both, proving the reported
  repeated contention was not actual lane exhaustion.
- The root cause was diagnostic collapse: repository/verifier/config/record and
  lane failures all became `managed_dispatch_rejected`, allowing callers to
  guess “contention.” The candidate preserves distinct
  `LaneUnavailableError` and `CampaignCapacityError` through orchestration and
  emits `managed_lane_unavailable` only when every lane is leased, versus
  `managed_campaign_capacity_reached` at the sealed sample cap.
- `doctor` reports `lane_count: 10`. The packaged skill forbids relabeling
  generic dispatch rejection as contention and recognizes exact 0.17.1 skills
  for safe upgrade. All 120 advisory and 1,290 full tests pass with 19 skips;
  Ruff, strict mypy, wheel/sdist isolation, actual 0.17.1 skill upgrade, and
  Codex Security diff scan `05fcc392-ae12-4aa0-9f86-1226334789c4` pass with no
  reportable finding.
- Source PR #96 merged as `268dd2e`; Release run `32839986511` published the
  exact reviewed candidate. Source-formula PR #97 merged as `6689cb7`, and tap
  PR `ictechgy/homebrew-tap#19` merged as `c5658f3`. The user-level uv tool and
  Homebrew source install both report 0.17.2, and `brew test` passes. Exact
  0.17.1 personal skills upgraded through `upgrade_planned` to `upgraded` for
  both Codex and Claude and match the packaged bundle byte-for-byte. Managed
  `doctor` is ready for both vendors and reports `lane_count: 10`.

### Advisory campaign-binding diagnostics (released in 0.17.3)

- The legacy machine-local implementation populations remain intentionally
  unusable when their current manifest no longer matches sealed records. No
  record, fingerprint, manifest, or population is rewritten or synthesized.
- Every rejection originating in `validate_record_bindings()` now carries one
  reviewed value-free `campaign_record_*` reason. The lane allocator preserves
  that reason as `CampaignRecordsInvalidError` instead of collapsing it into a
  blank `ValueError`, so `wclass-advisory-local` can print an actionable code
  without exposing paths, fingerprints, record values, or task material.
- Managed `doctor` validates all existing anonymous lane populations before it
  reports ready. Both `doctor` and `dispatch` return the same fixed record code;
  lane contention and campaign capacity retain their separate 0.17.2 codes.
- The packaged skill treats record-binding errors as an unhealthy sealed
  population, forbids repair/reseal/implicit replacement, and recognizes the
  exact 0.17.2 bundle for safe upgrade. Prospective acceptance, 124 advisory
  tests, Ruff, strict mypy over 163 source files, the 1,294-test source suite,
  and 0.17.3 wheel/sdist isolation pass. Security diff scan
  `c750fa5d-2489-4737-a10a-dd4e875440f4` found no reportable vulnerability;
  its functional coverage note exposed one remaining wrapper-level reason-code
  collapse, which is fixed by a direct `load_merged_lane_records()` regression.
  The final source suite passes 1,295 tests with 23 skips.
- Source PR #99 merged as `32fca6b`; Release run `32861214923` published the
  exact reviewed candidate. Source-formula PR #100 merged as `091b23c`, and tap
  PR `ictechgy/homebrew-tap#20` merged as `bca4d42`. The user-level uv tool and
  Homebrew source install both report 0.17.3, and `brew test` passes. Exact
  0.17.2 personal skills upgraded through `upgrade_planned` to `upgraded` for
  both Codex and Claude and match the packaged bundle byte-for-byte. Managed
  `doctor` remains ready for both vendors/all workflows with `lane_count: 10`.
  A read-only installed-package probe against the unchanged legacy Codex
  population returns `campaign_record_binding_mismatch` instead of an empty
  message.

### Advisory failure receipts (released in 0.17.4)

- Each failed cheap, advised-retry, or expensive attempt emits one canonical
  `advisory_attempt_failed` JSON receipt to stderr. Its closed schema contains
  only fixed route/kind/stage values, booleans, bounded numeric exit/timing/count
  fields, and no task, path, model, error string, verifier stream, patch, advice,
  profile, credential, or workspace material.
- Ordinary nonzero verifier exits are now recorded as
  `failure_kind=route`, `failure_stage=verification`, and
  `error=verification_failed`; the receipt exposes the numeric verifier exit
  code. Prospective verifiers should use distinct nonzero codes for materially
  different acceptance phases when useful. Raw verifier output remains
  transient for the existing advisor flow, and failed workspaces/patches are
  still deleted.
- Existing report compatibility is preserved: receipt fallback values are not
  forced into stored attempt records, so an unclassified legacy failure cannot
  become a usable effectiveness sample merely because it was rendered.
- The implementation Advisory dispatch ran both vendors once. Codex cheap
  passed after a 569.5-second child and 135.6-second verifier and produced the
  retained patch. Claude cheap failed after 907.4 seconds and a 152.5-second
  verifier; its 2,593-character advice, retry, and expensive route also failed.
  No external retry was run.
- Prospective acceptance, focused receipt/privacy/cleanup tests, Ruff, strict
  mypy over 165 source files, and the pre-review 1,298-test source suite pass
  with 25 skips.
- PR #102 was reviewed through one separate read-only Advisory dispatch. Codex
  cheap passed after 326.8 seconds and a 1.2-second verifier. Claude cheap
  returned an invalid result after 1,010.2 seconds, advice/retry failed, and the
  expensive review passed. Both accepted reviews identified receipt-output
  failure as a medium control-flow issue. The integrated follow-up makes receipt
  and parent replay writes best-effort, removes partial patches after write or
  chmod failure, adds `verification_integrity`, redacts cleanup diagnostics,
  and clarifies that only cheap/retry/expensive acceptance arms emit receipts.
- The reported silent-lock diagnosis was narrowed in source: lane and legacy
  `dispatch.lock` probes were already nonblocking, while `.allocator.lock` was
  the only unbounded acquisition. The allocator now has a two-second ceiling
  and exact `managed_allocator_busy` error; a legacy lane-0 owner moves a new
  run to another free lane. `doctor` reports point-in-time free/busy counts and
  dispatch immediately emits `managed_dispatch_started` with anonymous lane
  indices before vendor work begins.
- The Mac-only `wclass-advisory-local run` compatibility path is redirected to
  managed `wclass-advisory dispatch`; its legacy report/status/prune surfaces
  and all legacy records remain read-only. The packaged skill forbids invoking
  legacy run populations. The final source suite passes 1,307 tests with 25
  skips; the Advisory-focused suite passes 137 tests with 6 skips, and Ruff plus
  strict mypy over 165 source files pass. The 0.17.4 wheel/sdist isolation run
  passes 1,300 tests with 64 environment-specific skips. Final security diff
  scan `bb6284ae-78b3-438d-baf1-02eff5bfb100` covered all six changed
  source/package files and found no reportable vulnerability; measured usage
  was 8,449,226 total tokens with complete coverage and parent-only fallback.
  Source PR #102 merged as `763fa49`; Release run `32942266996` published the
  exact reviewed candidate. Source-formula PR #103 merged as `d316f01`, and tap
  PR `ictechgy/homebrew-tap#21` merged as `68491ee`. The user-level uv tool and
  Homebrew source install both report 0.17.4, and `brew test` passes. Exact
  0.17.3 personal skills upgraded through `upgrade_planned` to `upgraded` for
  both Codex and Claude and match the packaged bundle byte-for-byte. A live
  installed `doctor` snapshot correctly reported busy and free lanes while
  other projects were running, and the Mac legacy `run` shim emitted its fixed
  compatibility redirect before invoking managed dispatch.

### Claude evidence contract and execution precedence (0.17.6 candidate)

- Completed managed evidence records showed repeated Claude
  `failure_stage=result` with no verifier execution, while Codex sometimes
  reached the same closed workflow verifier. Source and local Claude 2.1.246
  help confirmed that the built-in Claude evidence route used plan mode plus a
  JSON envelope but did not request JSON Schema structured output.
- Claude cheap/expensive evidence executors now use `dontAsk`, only Read/Glob/
  Grep, and the complete closed union of all four task-free evidence schemas;
  every nested object, required field, bounded list, and bounded string is
  represented. The runner still enforces the exact selected workflow with its
  local byte-bounded parser. Claude advisor prose remains in plan mode. Claude
  implementation and every Codex route are unchanged.
- Existing Claude evidence populations are never rewritten or merged. Managed
  paths use a new `structured-v5` generation, and explicit
  `migrate-evidence --vendor claude` validates old bindings and creates empty
  current campaigns while leaving every legacy manifest/result byte in place.
  The migration prefers and preserves the newest complete `structured-v4`,
  `structured-v3`, `structured-v2`, or 0.17.5 `structured-v1` population,
  then falls back to the original unversioned population. This repairs the
  0.17.5 route-fingerprint mismatch without merging any source. V2 through V4
  existed only during local pre-release checks.
  Managed status selects explicit current paths rather than directory-wide
  discovery.
- Evidence attempts now record only fixed output shapes and whether a provider
  envelope was extracted. Portfolio output aggregates those shapes and failure
  stages through closed allowlists. Implementation children that exit nonzero
  without changes take `failure_stage=execution`, are classified as
  infrastructure, skip the irrelevant verifier and advisor, retain the numeric
  child exit plus only a fixed heuristic failure category and stream-presence
  booleans, and can still escalate under the sealed policy. Portfolio output
  aggregates only allowlisted child failure categories.
- Focused route, migration, output-shape, implementation-precedence, child
  diagnostic, portfolio, distribution-command, and existing advisory tests
  pass. The full source suite passes 1,313 tests with 26 skips; the
  Advisory-focused suite passes 143 tests with 7 skips; Ruff, formatting,
  compileall, and strict mypy over 165 source files pass; and 0.17.6 wheel/sdist
  isolation passes 1,306 tests with 66 environment-specific skips.
  The machine's Claude evidence dry-run and actual migration both succeeded,
  legacy data remained separate, and source doctor/review accept every new
  evidence workflow. Final 0.17.6 security diff scan
  `e9d99a3e-2f5a-47ca-8dfc-e1bdb84ab6c7` reviewed all five changed source/
  package files, found no reportable vulnerability with complete coverage, and
  used 4,880,148 tokens in one sequential thread. PR CI, merge, and all release
  gates pass. Exact uv and Homebrew installs report 0.17.6; both Codex and
  Claude skills report managed onboarding 8; installed migration returns
  `already_migrated=true` for `structured-v5`; installed doctor reports all
  five Claude workflows ready with ten free lanes each; and installed review
  reports the expected read-only stdin route.

### Advisory state retention (0.17.7 candidate)

- A metadata-only local audit found 8,730,345,998 bytes and 203,353 files in
  the managed advisory root. Three registered implementation workspaces account
  for about 8.72 GB; manifests, records, profiles, and prior evidence
  generations are only tens of kilobytes. No task or log body was read.
- The growth is structural: successful implementation attempts preserved both
  the verified patch and a full reconstructed repository workspace. Interrupted
  attempts were also intentionally registered for later cleanup, but managed
  cleanup required every lane in a population to be idle.
- A successful implementation now keeps the owner-only verified patch and
  immediately discards the full workspace. Before a new campaign attempt, the
  runner also removes registered residue from that lane while holding its
  campaign lock and emits only task-free counts.
- Managed `cleanup` now locks each lane independently, cleans every inactive
  lane, skips active lanes, and returns one closed JSON receipt with population,
  lane, busy, registered, removed, and retained counts. It never deletes
  profiles, manifests, run records, patches, or prior generations.
- Focused tests cover immediate success cleanup, path-free automatic cleanup,
  partial cleanup with one busy lane, and the managed aggregate receipt. The
  existing 8.72 GB of registered workspaces has not been deleted because that
  material deletion requires a separate explicit confirmation.
- The Advisory-focused suite passes 146 tests with 8 skips; the full source
  suite passes 1,317 tests with 27 skips; Ruff, formatting, compileall, and
  strict mypy over 165 source files pass. Wheel/sdist isolation passes 1,310
  tests with 68 environment-specific skips. Security diff scan
  `a3cfbc45-1027-4eaa-bce8-58e95428c0c3` reviewed all five changed source/
  package files, found no reportable vulnerability with complete coverage, and
  used 6,699,818 tokens in one sequential thread. PR, release, uv install,
  exact skill upgrade to managed onboarding 9, Homebrew upgrade, and `brew
  test` all pass. Existing-state cleanup remains pending explicit destructive
  approval; all lanes were free at the last read-only doctor check.

## The routing-economics result

This is the reason the study existed, so keep the conclusion with the code.

- **Phase 1 pilot** (36 real invocations, two vendors): pinning effort to
  `medium` came out ahead of weightclass routing by point estimate on both
  vendors — 5.1% on Claude, 5.5% on Codex — but only Claude's fixed-arm interval
  excluded zero; Codex was a wide null. Zero rework in 36 runs.
- **Phase 1b calibration** (18 candidates, Codex, 23 invocations, 981,613
  tokens): **0 tasks were tier-sensitive.** 15 passed at the routed tier; 3
  failed at both their routed tier and one step up. The pre-registered floor for
  entering Phase 2 was 9 tier-sensitive tasks out of 36, and the design
  instructed reporting a shortfall as the finding rather than lowering the bar.
  **Phase 2 was never started.**
- Two tasks routed to `high` (`p08`, `p21`) also passed a tier down. Of the nine
  calibrated tasks blind raters called `high`, seven were routed `standard` and
  five passed there.
- What this supports: **routing up bought nothing on work of this shape.**
- **The downward question was measured afterwards** (2026-08-16, outside the
  closed study; `DOWNWARD-REPORT.md` in the study repo). The 15 tasks that
  passed at their routed tier were re-run pinned at `low`: **15 of 15 passed**,
  0 critical failures. `p08`, which the router sent to `high` for 77,170 tokens,
  passed at `low` on 31,727. So the classifier over-routed on all 15.
  Token savings are directionally favourable but **not established**: `low`
  used 506,529 tokens against the routed tiers' 632,983, or 20.0% fewer, but
  that aggregate is dominated by the largest tasks. The per-task mean saving is
  14.1% with a 95% interval of [−1.0%, +29.1%], which includes zero, and 4 of
  the 15 cost *more* at `low`.
  This does not license "always route `low`" — those 15 were selected for having
  already passed, and a binary acceptance test cannot see work that passes while
  being subtly worse.
- Two of the three failures were traced to defects in the study's own acceptance
  tests, which over-specify the interface for tasks that ask for a new API
  (`p27` misread a raise as a hang; `p13` looked only for a tuple return when
  the agent used a callback). `p26` was not investigated. The defect did not
  reach Phase 1: all 36 pilot runs recorded `completed: true`, and an
  over-specified test can only reject correct work, never accept wrong work.
- Scope: one vendor for calibration (Codex), one synthetic fixture, small
  well-specified maintenance tasks. This does not show effort never matters.
- Full study design and the closure section: `docs/paired-token-study.md`. The
  public summary is in `README.md`. The fixture, 36 rated tasks, harness, and
  per-task verdicts live in a **separate study repository outside this repo**
  (a separate study repository outside this repo), deliberately not vendored here.

## The advisor arm and what its redaction cost

PR #50 reimplements the **pattern** behind Anthropic's advisory tool locally
instead of adopting the tool. That keeps it usable on a subscription or an API
key, on Claude or Codex, and on paths that cannot carry a beta header. Two
shapes are measured under separate flags: Shape A (`--advise-first`, worth it
when `a_A + (c_A - c) < p - p'`) and Shape B (`--advise-on-failure`, worth it
when `s > a_B + q*r`). Shape B's condition has no `c` in it, so a
subscription log with zero cost can still decide it.

The advisor is **the only path in this design that sends text off the machine**,
so verification output, diffs, and task text all cross it. That one function
took 47 adversarial review rounds and never converged; roughly half of each
round's findings were created by the previous round's fix. Two things ended
that pattern, and they are the transferable part:

- **Judge on a format's own properties, not on what the text looks like.**
  base64 comes in groups of four, DER puts a length after its tag, and a PuTTY
  key declares its own extent with `Private-Lines:`. Every judgement moved onto
  a property stopped being re-litigated; every shape heuristic kept coming back.
- **A knob that is wrong in both directions should be removed, not tuned.**
  Several findings were "narrowing this leaks, widening it destroys the failure
  signal". Those have no stable setting. Deleting the knob or writing the limit
  into a named test (`test_a_dotted_lowercase_value_is_a_known_limit`,
  `test_a_context_line_head_with_changed_body_is_a_known_limit`) held; splitting
  the difference did not.

`tools/check_test_vacuity.py` exists because a test of mine passed while
checking nothing — its probe string was not in the input — and hid a real key
leak for five rounds. It reruns the suite against a copy of the runner whose
redaction functions are replaced by identity and lists what still passes.
It now uses deterministic parent-plus-ordinal subtest IDs instead of printing
parameter reprs, retains parent errors/skips/missing outcomes and expected-
failure semantics, and fails closed unless every reviewed neutralization target
appears exactly once. The hardened audit still reports **227 leaves fail, 107
pass, 334 total**, with byte-stable output across repeated runs. Passing is not
by itself a defect (preservation tests are supposed to pass); the list is for a
human to read.

### First real Shape-B measurement (2026-08-19)

- One pre-registered real policy-4 implementation task was run with a cheap
  Codex route, prompt-only strong Codex advisor, and strong Codex escalation.
- The first attempt exposed an infrastructure mistake: prompt-only Codex advice
  needs `--skip-git-repo-check` in its empty directory. It returned no advice
  and is excluded as an advisor-effectiveness sample.
- The corrected run reached the full Shape-B path. Cheap failed; the advisor
  returned 1,889 redacted characters; the advised retry failed; escalation also
  failed in that run. Thus `q=1` and `s=0/1` (95% interval [0.0%, 79.3%]). A
  separate strong-route attempt produced the independently verified patch used
  for policy 4.
- Descriptive tokens were 795,825 cheap, 69,290 advisor, 1,259,310 retry, and
  1,117,130 escalation. No user-supplied price table was available, so the
  report correctly abstained from `a`, `r`, and the economic verdict. One task
  cannot support a product decision.

### Advisory follow-up and blind policy check (2026-08-19)

- Three additional real maintenance tasks exercised the measurement tooling.
  The cheap route passed 2/3. The remaining task reached Shape B; advice was
  non-empty and the advisor route itself succeeded, but both the advised retry
  and escalation failed verification. New-sample rescue was therefore 0/1;
  combined with the first corrected task, observed rescue is 0/2. The three
  follow-up tasks reported 2,823,560 child tokens in total. This is descriptive
  only: there was still no reviewed single-origin price table, so no economic
  verdict is available.
- The follow-up exposed measurement-tool defects rather than product evidence:
  prompt-only advisors needed their own empty Git boundary; inherited `GIT_*`
  routing variables could bypass it; `spec-retry-` and `spec-advice-` crash
  workspaces were missing from the prune allowlist; setup could leave a newly
  created workspace untracked if registry I/O failed; and the vacuity recorder
  could lose or expose several unittest leaf shapes. Focused RED tests now bind
  those cases.
- A separately pre-registered 24-prompt blind direction check used two
  independent generators and three isolated raters, with ratings sealed before
  policy-4 predictions. There were no three-way splits and 20/24 ratings were
  unanimous. Policy 4 agreed on 10/24 (41.7%; Wilson 95% CI 24.5%–61.2%), found
  1/9 majority-`high` prompts (11.1%; 2.0%–43.5%), and over-routed 6/24 (25.0%;
  12.0%–44.9%). This small synthetic sample is a warning, not a promotion gate;
  it does not justify lowering the default or tuning against the spent corpus.
  Aggregate protocol and results are in
  `docs/policy4-fresh-blind-evaluation.md`; task text and per-row artifacts stay
  outside this repository.

### Read-only advisory evidence workflows (2026-08-22)

- Repository-only advisory execution now keeps five separate workflow
  populations: `implementation`, `review`, `research`, `diagnosis`, and
  `design`. The four evidence workflows require bounded, duplicate-key-safe,
  closed JSON results; design returns a problem, principles, bounded options
  (each with evidence, strengths, risks, and affected surfaces), a
  recommendation, acceptance criteria, validation steps, and limitations.
  They reject tracked, untracked, ignored, and known scaffolding changes, and
  stream the result only to a scrubbed verifier stdin. Winning JSON is printed
  after aggregate logging; task, result, advice, verifier output, paths, and
  fingerprints are not added to the log or persisted as patches.
- Profile-based evidence executors compile to Claude `plan` without `Edit` or
  Codex `read-only`. Exact commands remain operator-reviewed, with filesystem
  edits rejected mechanically.
- Evidence campaigns use manifest schema 2 and bind the workflow into the
  fingerprint and record. Existing implementation manifests remain schema 1
  and byte/shape compatible. Schema validity is not factual validity: every
  campaign still needs a pre-registered task-specific verifier.
- Brainstorming is deliberately not a production workflow. Its separate
  assessment is `docs/advisory-brainstorming-assessment.md`; the decision is
  “experiment before promotion,” because binary Shape-B rescue does not measure
  constraint compliance, idea diversity, or human preference.
- A portable personal Agent Skill is available as the explicit opt-in
  `advisory` skill for Codex and Claude Code. The installed companion
  preflights both destinations, refuses overwrite/symlink conflicts, copies
  only the closed three-file bundle with owner-only permissions, and requires
  the closed package-owned bundle. It ships with `wclass-advisory` but does not
  promote advisory into core `wclass`; see `docs/advisory-skill.md`.
- The implementation task itself became sealed ordinal 7. Both vendors reached
  the complete Shape-B fallback and failed acceptance, so no candidate patch
  was retained; the final change was integrated manually against the unchanged
  prospective verifier. This is useful failure/cost evidence, not evidence for
  advisory promotion.
- The design-mode follow-up became sealed ordinal 8. Both cheap paths passed
  without advice or escalation: Codex produced the smaller accepted candidate
  and Claude independently passed the same prospective verifier. This adds
  ordinary cheap-path evidence but no new advised-failure rescue observation.

### Flexible advisory vendor support (2026-08-23)

- The prospective flexible-vendor acceptance is committed in
  `tests/test_advisory_flexible_vendors.py`; do not edit it or the protected
  verifier. The implementation keeps schema-1 Claude/Codex profile digests and
  commands byte-compatible while adding deterministic schema-1 agy and Grok
  builders.
- Advisory agy routes use the reviewed `{{task}}` argv slot and disclose local
  process-inspection exposure. Advisory Grok routes use an inherited
  `{{task_file}}` pipe outside the Git workspace, preventing `git add -A` from
  staging task bytes; no task pathname or file is created, and both pipe ends
  are closed on success, failure, timeout, and child-start errors.
- Schema-2 profiles are closed, bounded, duplicate-key-safe arbitrary-vendor
  profiles with exact implementation/evidence command matrices for cheap,
  advisor, and expensive roles. Each command permits at most one exact
  `{{task}}` or `{{task_file}}` token, never executable position or embedding.
- Review reports uniform or per-role delivery and whether any selected task
  enters argv. agy/Grok JSON usage is parsed only when the executable identifies
  that vendor; unknown structured stdout remains untrusted. Their environment
  prefixes and known `.agy`/`.grok` scaffolding directories are separately
  bounded. An unknown custom executable receives no vendor credential prefixes
  by default; required exact names are an explicit per-arm opt-in.
- No active agy/Grok measurement profile is created here. Model labels remain
  user-selected opaque configuration; do not infer quality, pricing,
  entitlement, or subscription usage. Keep every vendor/workflow campaign
  separate and preserve the existing schema-1 campaign/fingerprint contract.
- Machine-local multi-vendor advisory dispatch starts independent vendor
  campaigns concurrently and replays their memory-captured output in stable
  vendor order. The cheap/advisor/retry/expensive stages within one campaign
  remain sequential, and each campaign retains its own lock, ordinal, and log.
- The prospective parallel-dispatch task was offered to the sealed
  implementation campaign, but admission failed because another campaign run
  held the lock. No ordinal or evidence record was appended, and the attempt is
  not an effectiveness sample. The fixed prospective acceptance was integrated
  manually without retrying the campaign.
- Codex Security diff scan `d41c9731-3c2f-4547-8eb1-07c46bdb8b08` reviewed the
  parallel process, validation, output, and campaign-boundary change and found
  no reportable security issues. Its complete measured usage was 2,060,279
  total tokens (2,055,059 input; 2,020,864 cached input).
- A task-specific `.weightclass/verify-review` now preregisters three factual
  controls for the current repository-improvement review: the two documented
  pathname residuals must remain open, while the parallel shell-injection claim
  must be suppressed. New findings need tracked exact locations, direct
  location citations, counterevidence, and concrete recommendations. Replace
  this verifier deliberately for a different review task; schema validity is
  still not a reusable factual oracle.
- The deliberately paired Claude/Codex review became each vendor's review
  ordinal 4. Claude's cheap result was invalid and its advised retry passed;
  Codex passed on the cheap route. The validated synthesis found no immediate
  new vulnerability. A repository-owned overall cancellation/deadline contract
  and CI coverage for the machine-local wrapper remain productization work.
  The generic output-cap concern is not reachable through today's exact wrapper,
  which launches only the bounded `speculative_run.py`; revisit it if the helper
  becomes a general or distributed API.
- Review campaign totals are still below every promotion gate: Claude has 4
  tasks, 3 advised failures, and 1 advised rescue; Codex has 4 tasks, 1 advised
  failure, and 0 advised rescues. No cost/effectiveness decision is licensed.
- The five-item hardening batch became Codex implementation ordinal 14 and
  passed on the cheap route. Claude did not admit this task because another
  project already held implementation ordinal 15; it was not retried and is
  not this task's evidence. That collision exposed an attribution bug in the
  machine shim: checking only that an ordinal advanced could mistake another
  project's record for its own, and one post-processing error hid later vendor
  output. Repository-owned owner-only dispatch locks now cover every selected
  vendor before partial launch, and all completed results are replayed before a
  record-binding error is returned.
- Parallel advisory jobs now have an eight-hour outer ceiling, a 1 MiB combined
  retained-output limit, concurrent pipe draining, isolated process sessions,
  and timeout process-group cleanup. Inner Shape-B child/verifier limits remain
  narrower. The repository-owned CLI seam and orchestration core contain no
  machine paths; the Mac shim supplies its roots and is strict-mypy/import
  checked against them.
- Same-vendor/workflow advisory now uses ten fixed anonymous lanes by default.
  Lane 0 is the byte-compatible existing result root; lanes 1-9 live under
  owner-only `.lanes/lane-XX` directories. Allocation atomically leases one
  free lane for every selected vendor before any child starts, releases partial
  leases on exhaustion, and stores no project, repository, task, PID, timestamp,
  profile, or fingerprint-derived lane identity. A crashed owner releases its
  OS locks without a reservation file.
- The generic advisory seam disables argparse long-option abbreviation. This
  keeps the forwarded runner `--campaign` option distinct from the seam-owned
  `--campaign-root`; otherwise a sealed manifest path could be misparsed as the
  result directory and every dispatch would fail before task access.
- Evidence extraction accepts a provider's object-valued `structured_output`
  envelope by canonically serializing only that value and then applying the
  existing closed workflow schema. Existing text envelopes are unchanged; a
  malformed or non-finite structured value still fails closed.
- Repository-grounded design advisory now has a prospective
  `.weightclass/verify-design` gate. It requires at least three independently
  grounded options, tracked `path:line` evidence, explicit security,
  performance, and product/operator dimensions, a recommendation naming one
  option, and measurable acceptance and validation criteria.
- `tools/advisory_portfolio.py` renders deterministic, task-free status across
  independently sealed vendor/workflow populations. It validates every
  manifest and bounded lane population, emits no input paths, rejects duplicate
  labels or campaign inputs, and reports sample floors, rescue/escalation
  counts, abstention reasons, and the next collection action without pooling
  populations or making a promotion decision.
- Portfolio metrics keep actual money cost, token volume, and child latency in
  separate per-stage structures. Missing observations produce `null` totals,
  and incomplete metrics or infrastructure failures automatically add explicit
  abstention reasons instead of silently reporting partial consumption.
- Portfolio readiness never authorizes a policy change. A complete population
  becomes only `evaluate`-ready and points to the separate statistical gate;
  incomplete metrics point to measurement repair, while
  `policy_decision_allowed` remains false.
- Descriptor-bound execution remains unenforced. A new test-only compatibility
  probe independently characterizes native fd exec, shebang scripts, and an
  opened-object pathname swap where the OS advertises support; unsupported
  platforms return a bounded task-free result. Production observation and
  foreground Popen are unchanged.
- Script fd-exec classification now distinguishes the two unsupported-format
  errno values from operational failures, and timeout cleanup transfers a slow
  child reap to a daemon reaper after the bounded poll. Portfolio status can
  also discover exact, complete populations from one owner-only campaign
  directory without persisting or rendering its paths.
- Each lane retains its own local contiguous ordinal, campaign lock, registry,
  workspaces, and log. Reporting discovers every bounded lane, validates each
  independently against the exact sealed manifest, rejects mixed/damaged/gapped
  or aggregate-over-cap inputs, and renumbers only an in-memory copied view for
  combined task/failure/rescue/cost gates. Prune covers every existing lane.
- The lane implementation became Codex implementation ordinal 16 and passed on
  the cheap route after a 925.7-second child and 145.1-second verifier. Fixed
  lane allocation counts both completed records and live leases against the
  sealed global `max_tasks`, retains the legacy `dispatch.lock` for lane-0
  migration safety, rejects symlink/shared lane directories, and refuses prune
  while any lane campaign lock is active.
- Codex Security diff scan `1940cb8d-5ac8-46b0-91d7-d618e38ef719` reviewed
  allocator atomicity, path admission, crash recovery, legacy migration,
  capacity reservations, merged reporting, pruning, and privacy with complete
  changed-source coverage and found no reportable issues. Measured usage was
  7,276,858 total tokens (7,273,054 input; 7,256,064 cached input).
- Usage-store lock/read/temp/replace/cleanup/fsync operations are relative to
  one opened and revalidated private parent descriptor. A deterministic parent
  swap test proves that a replacement directory is not written and the staged
  file is recovered. Path resolution before the parent descriptor is opened
  remains a narrower residual; the default private home location remains the
  preferred path.
- Executable admission now rejects other-writable executable files,
  group-writable files not owned by root/current user, and non-sticky
  world-writable containing directories in both lexical and resolved target
  chains. Root/current-user-owned group-writable files, sticky directories, and
  user-owned group-writable ancestors remain compatible with macOS and
  Homebrew. GitHub Actions' world-writable hosted toolcache remains rejected;
  V2 tests now stage a private runtime fixture instead of weakening admission.
  Security diff scan
  `f408aeb2-976c-432d-8d8a-d7ca87defb46` found that the first implementation
  checked only the resolved chain and accepted an intermediate symlink under a
  public lexical ancestor; a focused reproduction confirmed the medium finding.
  The follow-up checks both chains, rejects that reproduction, and preserves the
  four installed executable probes. The scan used 8,768,564 total tokens
  (8,759,893 input; 8,710,144 cached input). Path-based spawn after the final
  observation remains a medium residual; this change is not described as
  verified-object execution.
- The flexible-vendor implementation became Codex implementation ordinal 11.
  Luna passed the prospective verifier without advice or escalation after a
  799.2-second child run. Claude ordinal 11 was concurrently consumed by a
  different project, so those equal ordinals are not a paired task and must not
  be used for cross-vendor comparison.

### Security, performance, and architecture follow-up (2026-08-19)

- Codex Security Standard scan `d2175938-1f01-47e6-b0f2-8d5089f5d839`
  recorded two medium and two low findings. Coverage was deliberately partial:
  29/183 files were fully reviewed, while all six security-critical surfaces
  were traced by an independent baseline, two focused investigators, and parent
  validation.
- Immediate fixes reject `{{task}}` in `argv[0]`, apply recursive duplicate-key
  rejection and complete `ValueError` normalization to usage stores, and bound
  schema-3 controlling-console lines/numeric choices.
- The standalone `--version` path now bypasses the full command dispatcher.
  On the same Apple M4 Pro/Python 3.14.6 host, 30-process median cold start fell
  from 77.022 ms to 19.412 ms (−74.8%). Classification and help paths were not
  changed, and timing is not a test gate.
- CI no longer starts both push and pull-request matrices for one feature-branch
  SHA. Push CI is restricted to `main`, while PR-number concurrency cancels
  only stale runs for the same pull request.
- Path-based executable spawn remains a medium residual even after detailed
  re-observation; a custom usage store under an unsafe ancestor remains a low
  pathname race. The derived hardening portfolio recommends safe-ancestor
  admission first and capability-gated descriptor execution research, but its
  sticky-directory/group-write compatibility decisions are not silently chosen
  in this patch. See `docs/security-performance-followup.md`.
- Native schema-2 model/effort labels now share an execution-specific boundary:
  option-like leading `-`, edge whitespace, and invisible/non-ASCII whitespace
  fail before task access with redacted `invalid_input`. Internal ASCII spaces
  remain compatible as one argv token; labels stay opaque and no availability,
  quality, price, or entitlement is inferred.
- Advisory productization measurements now have a repository-only sealed
  campaign contract (`docs/advisory-campaign.md`). It binds full route argv
  digests, verifier and price-table bytes, Shape A/B configuration, the existing
  12-advised-failure floor, and planned/max task counts before task access.
  Campaign logs use opaque ordinals, reject mixed/damaged/duplicate records,
  and cannot emit a decision before both sealed minimums are met. Campaign
  inputs are nonblocking no-follow descriptor reads; verifier and price-table
  bytes are staged privately before task access. This is measurement
  infrastructure, not evidence that advisory should ship.

## Completed

- Released state is `weightclass 0.22.0`. Published tags and the failed,
  unpublished `v0.16.0`/`v0.16.1` candidates must never be moved, reused,
  relabelled, or republished.
- The in-progress follow-up passes the already validated source vendor to usage
  accounting only for the model-free `medium` counterfactual lookup. Actual
  usage remains in the destination-agent bucket; schema 2 and its persisted key
  set are unchanged, and no per-run source-vendor field is stored.
- Harmful-outcome scanning now collapses consecutive whitespace only for the
  outcome matcher. This closes a window-boundary evasion caused by unbounded
  `\s+`/`\s*` separators without changing task length or mechanical-pair distance.
- Classification policy 4 adds two narrowly paired structures without tuning
  against the visible fixture: two English imperative sentences close every
  inferred cheap path, while description plus one imperative stays eligible;
  explicit root-cause intent plus an intermittent/nondeterministic symptom uses
  `high.uncertain_diagnostic`, while either signal alone does not escalate.
  The public fixture remains a direction check only: 21/40 agreement, high
  recall 5/15, over-routing 9/40 (22.5%).
- Advisory measurement workspaces now have an isolated zero-commit Git anchor;
  every child drops inherited `GIT_*` routing variables, including under
  `--child-env-all`; retry and advice prefixes are prune-recognized; and a
  registry failure during attempt setup removes the just-created workspace.
- The vacuity audit records subtests with opaque stable ordinals, preserves
  parent failures, skips, missing outcomes, expected failures, and unexpected
  successes, and refuses an incomplete neutralization rewrite.
- Legacy policy task slots cannot occupy executable position, usage-store JSON
  shares the duplicate-safe parser boundary, and interactive schema-3 selection
  has explicit line and numeric-choice bounds.
- Exact standalone version queries use the lightweight entry point; PR CI runs
  one feature-head matrix and cancels stale same-PR work.
- The schema-3 branch adds installed-agent discovery, interactive selection,
  reviewed cross-vendor/profile grants, observation-bound execution, and
  `wclass delegate native route|run` for one bounded child.
- Commit `5fddc38` adds opt-in aggregate-only accounting:
  - `wclass usage enable|weight|report`;
  - automatic recording for installed-entrypoint schema-3 `run` and
    `delegate native run` after a real child status is obtained;
  - cumulative agent/model/effort/tier, success/failure/status, rework, and
    escalation counters;
  - user-supplied prospective relative-cost weights and savings versus `1.0`;
  - private regular-file validation, locking, atomic replacement, bounds,
    symlink/shared-directory rejection, and redacted failure code `9`;
  - no task text/hash, per-run event, timestamp, profile/account, executable
    path, or route fingerprint in the store.
- PR #41 (`fix/routing-cost-evidence`, merged as `1a7c91f`) answers a review of
  whether routing can actually reduce cost. The review found three defects with
  evidence:
  - the classifier over-routed (public fixture: `low` recall 2/12, over-routing
    32.5%), so the cheap lever was effectively closed;
  - length alone forced `high`, making a pasted file list the most expensive
    route;
  - the savings metric compared against `weighted_runs x 1.0`, so it was an
    identity on user-supplied weights, and rework inflated the baseline. A
    measured 30% overrun (13.0 vs 10.0 units) was reported as 13.3% savings.
  - model grade, the largest cost variable, was unreachable from built-in
    routes.
- What those three commits changed:
  - classification policy `2` -> `3`. Length no longer raises a tier
    (`standard.length_floor`); the backtracking bound moved to
    `PATTERN_SCAN_CHARACTERS` while signals scan the full task;
    `low.mechanical_pair` and `low.substitution` opened the cheap tier; `auth`
    became a high signal. Fixture agreement 17/40 -> 22/40, `low` recall
    2/12 -> 6/12, over-routing 32.5% -> 22.5%. `high` recall unchanged at 5/15.
  - aggregate store schema `1` -> `2`. Savings compare against the same tasks
    on the fixed `medium` route; only first attempts count as tasks; the report
    abstains with `savings_reason_code` unless every run is weighted and every
    task has a baseline weight; per-bucket savings fields are gone. Schema 1
    stores are promoted on read without inventing baseline evidence.
  - `--{tier}-model` / `--{tier}-effort` now bind to built-in routes with a
    required `--source-vendor`, sharing one insertion-rule function with the
    preset path.
- Review-loop fixes merged on top of those three commits:
  - `b321893` `_load_store` now normalizes `RecursionError`. `json.loads` raises
    it (a `RuntimeError`, not `ValueError`) on deeply nested input, so a bounded
    hostile store crashed the router instead of failing closed. CI Python
    3.10/3.11 were red on this; 3.12+ hid it behind a different recursion limit.
  - `f0ae444` harmful-outcome detection scans overlapping windows over the whole
    task. Once length stopped raising a tier, leading filler could deterministically
    hide a costly-outcome description past the first 1,200 characters.
  - `dbea212` the counterfactual baseline is looked up without a model, matching
    the built-in standard route. Reusing the routed model priced a counterfactual
    that never existed and cancelled the very saving model routing produces.
  - `b5bea06` the cheap rules no longer fire on multi-instruction requests, and
    English `from/to` needs a numeric value swap or a mechanical verb.
- **PR #42** (`fe93a5c`) removed `\.\s+\S` from the multi-instruction guard.
  A plain sentence boundary was matching, so the guard fired on 34 of 36 study
  tasks and disabled the cheap rules almost everywhere.
- **PR #43** (`ec5eb29`) added `wclass run --suggest-escalation`. On a failed
  run it names the next tier up, with `from_tier`/`to_tier`/`route`/`vendor`/
  `route_fingerprint`/`record_as_rework`/`failure_cause_diagnosed`. It
  deliberately **does not print a runnable command**: two review tracks raised
  that as a blocker, because escalation fires on a failure path for a route the
  user never reviewed, unlike `wclass route` which is a deliberate review
  command.
- **PR #44** (`a763d9c`) added `docs/paired-token-study.md`, the pre-registered
  study design. **PR #45** (`887159a`) closed it with the Phase 1b result.
- **This repo has two test runners and they gate different things.** CI
  (`ci.yml`) installs pytest and runs `pytest -q`. The release workflow
  (`release.yml`) installs only `requirements/release.txt` — no pytest — and
  runs `python -m unittest discover -s tests`. A module written in pytest style
  is green on CI and either dies on import or, worse, **contributes zero tests**
  under the release gate, because unittest does not collect module-level
  `test_*` functions. Always reproduce with `unittest discover` before tagging.
  `tests/test_suite_structure.py` now fails on either cause.
- Verified on the advisory-review-verifier tree: `unittest discover` runs
  **1253** tests and `pytest -q` reports 1241 passed plus 12 skipped;
  Ruff check/format is
  clean on 189 files; `mypy --strict src tests` is clean on 144 source files;
  strict mypy is also clean on the route/campaign/runner/reporter tools; and an isolated sdist/wheel
  build succeeds. Note the mypy target: `src` **and** `tests`. Checking only `tools/`
  hides real errors — that is how 139 of them once reached `main`.
- The verification venv is not an editable project install. Full local test
  commands therefore need `PYTHONPATH=<repo>/src`; omitting it produces
  `No module named weightclass` import failures and is not a product result.
- The published `weightclass 0.22.0` is installed through both the user-level
  and Homebrew entrypoints; both `wclass` and `wclass-advisory` were checked.
- The default macOS aggregate store is enabled at its platform-default private
  application-state location.
  The directory is `0700`; store and lock are `0600`.
- Current report is intentionally empty: `runs=0`, `weights=[]`. No historical
  provider/session data was read or backfilled.

## Key Files & State

- `HANDOFF.md`: this restart-safe state. A rewrite that drops required strings turns
  `tests/test_completion_audit_v2.py` red in the working tree while CI stays
  green. Keep the `docs/completion-audit-v2.md` line below.
- `docs/completion-audit-v2.md`: requirement-to-test completion map.
  Goal g12 is leader-verified; retain this audit connection when refreshing
  this file. Both that path and that sentence are asserted verbatim by
  `test_handoff_points_to_current_g12_audit`, so do not let a rewrap split them.
- `AGENTS.md`: privacy, networking, one-child, and persisted-aggregate boundary.
- `docs/paired-token-study.md`: the closed study — design, pre-registered gate,
  and the Phase 1b result that stopped it.
- `src/weightclass/classification.py`: classification policy 4.
- `docs/policy4-fresh-blind-evaluation.md`: aggregate-only protocol and result
  from the fresh 24-prompt blind direction check.
- `docs/security-performance-followup.md`: current security findings,
  cold-start/CI evidence, implemented local fixes, and deferred architecture.
- `src/weightclass/advisory/advisory_campaign.py` and
  `managed_advisory.py`: schema-3 primary gate sealing, source-generation
  exploration, provider-role checks, and managed generation selection.
- `src/weightclass/advisory/readonly_snapshot.py`: bounded no-follow evidence
  mutation detection and relocated-workspace identity search; successful
  evidence still verifies in a fresh clean clone.
- `README.md`: public 0.20 advisory onboarding, gate, provider concurrency,
  snapshot boundary, and onboarding-14 skill upgrade guidance.
- `src/weightclass/router.py`: `_TIER_LADDER` and `next_tier()` for escalation.
- `src/weightclass/usage_aggregation.py`: aggregate schema, validation, locking,
  atomic writes, reporting, and default platform paths.
- `src/weightclass/cli.py`: `usage` surface, schema-3 recording integration, and
  `_print_escalation_suggestion()`.
- `src/weightclass/entrypoint.py`: enables automatic default-store resolution
  only for the installed CLI entrypoint, preventing in-process tests from
  mutating a user's store.
- `tests/test_usage_aggregation.py`: privacy, failure ordering, concurrency,
  atomicity, weight semantics, ordinary run, and native-delegation coverage.
- `README.md` and `docs/native-schema-3.md`: public accounting contract and
  exit-code `9` semantics.

## Important Context / Decisions

### Confirmed facts

- Model, effort, availability, price, and subscription/quota claims remain
  opaque user assertions. Relative weights are not provider prices.
- Weights apply prospectively. A weight changed later does not rewrite already
  aggregated units; configure weights before comparison runs.
- Omitting `--model` from `wclass usage weight` selects the native default.
  Passing `--model default` means a literal opaque model named `default`.
- Pre-child failures are not counted. If aggregate validation fails before
  execution, code `9` starts no child. If the child completed but persistence
  failed, code `9` includes `"child_completed": true`; do not auto-retry.
- Claude/Codex tasks use stdin; built-in `agy`/Grok schema-3 routes use the
  reviewed argv task slot and retain local process-inspection exposure.
- The aggregate feature does not edit Grok/Codex/Claude/vendor configuration.
  Investigation of an unexpected Grok startup prompt found no weightclass
  config-write path. The installed Grok route adds only `-p <task>`,
  `--permission-mode acceptEdits`, and `--reasoning-effort`; the user concluded
  the displayed rules were likely the session's automatic system prompt.

### Assumptions

- The user has not yet chosen defensible relative weights, so leaving every
  bucket unweighted is safer than inventing price/subscription claims.

## What Worked

- RED→GREEN tests fixed the privacy contract before runtime integration.
- Validate the enabled store before task access, then record only after a real
  child status. This prevents counting attempts and makes retry risk explicit.
- A private owner-only directory plus no-follow regular files, advisory locking,
  bounded exact JSON, fsync, and atomic replace survived concurrent-process
  tests without lost updates.
- Installing a verified local wheel through `uv tool` preserved the Homebrew
  installation and made rollback simple (`uv tool uninstall weightclass`).
- Pre-registering the study's stopping condition. When calibration returned 0
  tier-sensitive tasks, the rule was already written, so the null result was
  reportable instead of negotiable.
- The review loop earned its cost on a docs-only PR: 12 rounds, blocker count 0
  every round, but non-blocker findings caught a genuine over-generalization
  ("the cheapest tier is always correct" — never measured), an off-by-one in a
  task count, and an unchecked blast radius for the acceptance-test defect.
- Moving a judgement onto a format property instead of a shape heuristic. See
  "The advisor arm" above: those fixes stayed fixed, the heuristics did not.
- Writing a deliberate limit into a named test instead of tuning a two-sided
  knob toward a setting that does not exist.
- Proving a new guard is not vacuous by restoring the defect and watching it
  fail. `tests/test_suite_structure.py` was checked against the pre-conversion
  file: all three of its checks failed, then passed on the fixed tree.

## What Did Not Work / Avoid

- Do not claim aggregation already happened: the store is enabled but has zero
  real runs and zero weights.
- Do not infer or scrape provider pricing, bills, quotas, credentials, auth, or
  raw session histories. Do not retroactively synthesize usage.
- Do not read `.grok`, auth, credential, key, cookie, or token files to explain
  startup prompts. Ask for a redacted prompt excerpt if the issue recurs.
- Do not assume plain `wclass` exercises the Homebrew build: the user-level
  executable can shadow the separate Homebrew entrypoint, although both now
  report 0.22.0. Test an exact entrypoint when packaging provenance matters.
- Do not reuse/relabel the published `0.14.0` artifacts or protected tag for
  unreleased work.
- Do not narrow `HIGH_SIGNALS` on the calibration result. `p08`/`p21` both
  matched the `migration` signal and both passed a tier down, but that is n=2 on
  a synthetic fixture, and routing data-destroying migrations to `high` is a
  defensible posture independent of those two outcomes.
- Do not tune the classifier against the visible public fixture.
- **Do not treat `pytest -q` as the gate.** It is CI's runner, not the release
  gate's. Running it alone is how a whole test module reached `main` in a form
  the release workflow could not execute, and how `mypy` errors in `tests/`
  went unseen for a full review cycle.
- Do not let a tool that audits the suite use a different runner than the gate.
  `check_test_vacuity.py` counted pytest node IDs, which do not exist for
  `subTest` cases, so it silently lost per-parameter resolution and reported
  leak-direction tests as "passes under identity redaction".

## Next Steps

1. **Collect real advisory evidence without mislabeling failures.** Ten lanes
   per vendor/workflow are available, but sample caps remain independent of
   lane availability. Treat only `managed_lane_unavailable` as contention;
   report `managed_campaign_capacity_reached`, `campaign_record_*`, and generic
   dispatch rejection by their exact codes. Do not synthesize samples, repair
   fingerprints, or merge sealed populations. The legacy local implementation
   population is still unhealthy by design; use only an independently valid,
   explicitly selected managed population. For a formal new claim, run
   `migrate-gate` before the first dispatch and choose exactly one primary
   vendor/workflow in that managed state root. The pre-gate source remains
   available only for exploratory, never promotion-eligible analysis.
2. **Verified-object execution remains an open architecture item.** Current
   double observation narrows replacement but `Popen` still resolves a path.
   Before enforcing safe ancestors, settle sticky-directory and group-writable
   installation semantics and run the macOS/Linux compatibility matrix in the
   completed hardening plan. Do not claim the medium finding is fixed by another
   metadata comparison.
3. **Custom usage-store ancestry remains a low residual.** The parser is fixed,
   and lock/read/temp/replace/cleanup/fsync transactions are parent-directory-fd
   anchored. The remaining residual is unsafe ancestor pathname resolution
   before the parent is opened. Prefer the default private home location;
   implement ancestor admission only after the sticky and shared-group rules are
   fixed, or move directly to a dirfd transaction if privileged/shared-tree
   support becomes a requirement.
4. **The default tier is not being lowered. That question is settled for now.**
   The quality instrument was built, calibrated, and run
   (`QUALITY-INSTRUMENT.md`, `PRE-REGISTRATION-quality.md`, `QUALITY-RESULT.md`
   in the study repo). Blind pairwise review of both arms on 14 tasks: `low` won
   3 of 12 decisive tasks, 25.0%, 95% CI [8.9%, 53.2%]. Under the rule fixed
   before collection that is inconclusive, and the pre-registered consequence of
   inconclusive was **do not lower the default**.
   The reasons matter more than the tally. Six of the routed tier's nine wins
   were real defects in the cheap arm that its acceptance test passed anyway:
   `p21` accepts `True` as schema version 1, `p08` silently overwrites a
   malformed state, `p32` admits padded email-like ledger IDs, `p07` hands back
   a mutable cache list. The other three were test organisation and line
   wrapping. So the 20% token saving from routing down is not free — it is paid
   for in input validation.
   Do not reopen this by pointing at `DOWNWARD-REPORT.md`'s "all 15 passed".
   That result was always qualified as "relative to what the acceptance test
   required", and this is what that qualifier was hiding.
5. **The one cheap lever still standing is model grade, and it is measurable
   now.** A 90-pair qualification put a cheaper Codex model 69.02% below the
   stronger one on estimated API cost (95% interval [60.57%, 77.47%]) at equal
   quality (85/90 both arms), and it was rejected only for two new critical
   failures out of ninety. Those failures are mechanically detectable and
   reversible, so `docs/speculative-cheap-route-design.md` proposes running the
   cheap route in a disposable clone, verifying, and escalating only on failure.
   Expected cost is `c + p`; at c = 0.31 break-even is p = 0.69, so the cheap
   route can fail two times in three and still not lose money.
   **Measure `p` before changing core routing.** The installed
   `weightclass.advisory.speculative_run` and `speculative_report` modules do
   exactly that. The latest three real maintenance tasks had
   cheap acceptance 2/3, far too few to estimate `p`; if a larger sample lands
   under 20% the saving may justify moving the V1 boundary, while near 69% the
   idea is dead. Note this recovers safety, not quality — the defects in
   `QUALITY-RESULT.md` all passed their tests.
6. **Advisor adoption remains undecided.** Two corrected Shape-B failures now
   have observed rescue `s=0/2`, and neither study had a price-derived `a` or
   `r`. Deterministic installed profiles now compile operator-selected
   Claude and Codex model/effort labels into the same reviewed argv for seal and
   run, require explicit task-egress confirmation, and support disjoint Codex
   cached-input pricing. Current Claude Sonnet/Opus and Codex Luna/Sol synthetic
   stdin probes exited successfully, but they are connectivity checks, not
   evidence. The separate sealed campaigns still need at least 60 usable tasks
   and 12 advised failures each under a user-supplied single-origin price table;
   see `docs/advisory-vendor-profiles.md`. Do not integrate retry/advice into
   core `wclass` from these pilots; the companion remains explicit and experimental.
7. **Policy 4 needs broader high-tier evidence before another classifier
   change.** The public fixture remains 5/15 high recall; the fresh blind
   direction check found 1/9 with a wide interval and sent the other eight to
   `standard`. Do not tune on either visible corpus. A new policy candidate
   requires a new independently generated, rated, and sealed corpus.
8. If the paired token study is ever reopened, fix the three acceptance tests first
   (`p13`, `p26`, `p27`). They reject correct implementations that chose a
   different interface, which would mark a correct arm `completed: false` and
   fail the study's completion gate for reasons unrelated to routing.
9. If measuring routing economics, set the `medium` weight first — it is the
   counterfactual the report compares against, and without it the report
   abstains with `missing_baseline_weight`. Pass `--usage-rework` on any retry of
   an already counted task; a failed run prints
   `{"usage_hint": "record_retry_with_usage_rework"}` as a reminder.
10. Review an exact schema-3 route/fingerprint before any real run. Never launch a
   vendor merely to populate metrics without explicit task authorization.

## Resume Prompt

Open the current repository checkout, read `HANDOFF.md` and the applicable
`AGENTS.md`, then continue from: `weightclass 0.25.0 is published on PyPI and
Homebrew. Implementation PR #151, source-formula PR #152, and tap PR #33 are
merged. Tag v0.25.0 points to reviewed release commit 7367f41, and Release run
33356402517 passed the immutable build, Python 3.10/3.14 candidate
validation, macOS 3.10/3.14 boundaries, protected PyPI approval, and exact
publication. Both local uv and exact Homebrew entrypoints report 0.25.0, brew
test passes, and the Codex/Claude advisory skills are exact-current. Advisory
execution now requires explicit task-egress confirmation across exact-command,
profile, campaign, and internal-consult paths; bounded process-group and Git
execution, immutable-object verification, linear campaign scanning, safe state
roots, directory-descriptor Skill installation, and lazy managed CLI entrypoints
are implemented. The final complete 24-file Security diff scan has no surviving
finding; full source gates pass 1,496 unittest tests with 35 skips, Ruff/format
over 232 files, and strict mypy over 193 source files. Verified-object execution
for broader router paths and custom usage-store ancestry remain documented
architecture residuals. The separately invoked advisory companion remains
explicit and experimental; no campaign gate may authorize core routing. Never
infer prices, read vendor credentials/config, backfill task/session data, or
reuse a published version or tag.`

## Historical Resume Prompt (obsolete; retained for audit history)

Open the repository checkout, read `HANDOFF.md` and
applicable `AGENTS.md` files, then continue from: `0.16.2 is merged, tagged, and
published from main commit d252403; Release run 32798010700 passed. A local
0.17.0 managed-advisory release adds init, doctor, managed review,
dispatch, status, cleanup, the package-staged cross-project verifier, and a
safe exact-legacy Agent Skill upgrade. It passed 1,284 tests, Ruff, strict mypy,
distribution/clean-venv smoke, stable 227/107/334 vacuity, and Codex Security
scan 68974988-d4eb-4ad4-a392-6352f7fae8cd with no reportable findings, but is
published by Release run 32807440505. Source PR #89, formula PR #90, and tap PR
#17 are merged; PyPI, uv, Homebrew source upgrade, and brew test all report
0.17.0. Release 0.17.1 fixes misleading low-level run/prune,
managed review, and install-skill help; teaches the packaged skill to reject
stale run/path instructions; and safely upgrades exact 0.17.0 four-file skills
without overwriting customizations. Its 118 advisory and 1,288 full tests,
distribution upgrade smoke, and Security diff scan
2459da50-526f-4360-8597-e57470ef67e3 pass. Source PR #93, formula PR #94, tap
PR #18, Release run 32820191068, PyPI, uv, and Homebrew are complete. Four real Shape-B samples now include two
failures that reached advice, with observed rescue 0/2 and no economic verdict
because no reviewed price table existed. Claude/Codex task-free route profiles,
exact argv review, explicit egress confirmation, and Codex disjoint cached-input
pricing are implemented for repository-only campaigns; no product promotion is
allowed until each separate sealed campaign reaches 60 usable tasks and 12
advised failures. Release 0.17.2 preserves ten lanes per vendor/workflow and
separates true lane exhaustion (`managed_lane_unavailable`), sealed sample-cap
exhaustion (`managed_campaign_capacity_reached`), and generic dispatch failure.
Its 120 advisory and 1,290 full tests, artifact isolation, exact 0.17.1 skill
upgrade, and Security diff scan pass. Source PR #96, formula PR #97, tap PR #19,
Release run 32839986511, PyPI, uv, Homebrew, and exact Codex/Claude skill
upgrades are complete; managed doctor is ready with `lane_count: 10`. The fresh
24-prompt blind direction check found policy-4 agreement 10/24, high recall
1/9, and over-routing 6/24;
it is spent direction evidence, not a tuning set. Security scan
d2175938-1f01-47e6-b0f2-8d5089f5d839 found two medium and two low findings;
argv[0] task substitution and usage JSON fail-closed parsing are fixed on
`main`; native schema-2 option-like/edge-whitespace model labels are hardened,
while verified-object execution and directory-fd usage state
remain design work with compatibility decisions recorded in the hardening
portfolio. Never infer prices, read vendor credentials/config, backfill
task/session data, or reuse a published version or tag.`
