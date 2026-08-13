# Model and Effort Routing Roadmap

## Purpose

Make `weightclass` more reliable at choosing an effort tier without weakening
its local-first boundary. The roadmap improves the tier decision, not vendor
selection: a Codex-originated task remains on Codex and a Claude-originated
task remains on Claude unless a reviewed policy explicitly opts in to mixing.

The current classifier is a transparent heuristic, not a reliable automatic
high-stakes classifier. It catches documented signals and narrow outcomes, but
the public evaluation fixture shows meaningful high-tier under-routing for
ordinary-language security, data-integrity, concurrency, and reliability work.

## Protocol 2 implementation status

Native schema 2 is implemented as an additive path: explicit profiles and
source-profile selectors, exact allowed model/effort pairs, closed Codex and
Claude argv builders, directional profile/vendor grants, canonical review
descriptors, acknowledgement-bound run, two executable observations, and one
exact foreground spawn. Model, effort, profile/account, subscription, and
entitlement labels remain opaque declarations; vendor and builder values are
closed structural vocabularies. Cross-vendor routing remains policy-authorized
only. See `protocol-v2-security.md` and `protocol-v2-migration.md`.

The earlier five-round RALPLAN ended `max_rounds/ITERATE`; mandatory Critic
findings informed this implementation, but there was no consensus approval.
Rollback removes only schema-2 dispatch and leaves schema 1 and legacy render
unchanged.

## Non-negotiable constraints

- Runtime task text remains transient: read it from standard input and deliver
  it to children on standard input whenever they support that. The sole native
  exception is an explicitly reviewed `{{task}}` argv slot for a CLI that only
  accepts its prompt there, including the built-in `agy` and `grok` routes;
  those routes must disclose `"task_delivery": "argv"` and the documented
  local process-inspection exposure. Never persist, log, hash, echo, or include
  task text in diagnostics or review output.
- Default classification stays deterministic, offline, and reviewable.
- Model labels, subscription availability, billing, and entitlement remain
  opaque user configuration; the router does not infer them.
- V1 starts exactly one selected vendor process in the foreground. It does not
  retry, supervise, or speculatively run a lower tier before escalating.
- Cross-vendor routing remains an explicit policy opt-in.
- Router-owned policy and artifacts stay separate from vendor configuration.

## Decision model to validate

The candidate model separates risk from confidence instead of adding more
unbounded keyword exceptions:

| Decision | Candidate rule | Result |
| --- | --- | --- |
| Clearly mechanical | Narrow, short low-risk allowlist | `low` |
| Known high-impact risk | Reviewable hard risk floor or documented outcome | `high` |
| Long context | Existing length floor | `high` |
| Explicit user choice | Existing `--tier` override after input validation | requested tier |
| Ordinary engineering | No high floor and not clearly mechanical | `standard` |
| Ambiguous work under a user-selected cautious policy | Not clearly mechanical or standard-safe | `high` |

The classifier should expose static reason codes only when explicitly asked to
explain a decision. It must not reveal task text or matched excerpts.

## Phases and gates

### Phase 0 — Evaluation contract

**Goal:** decide what “appropriate routing” means before tuning rules.

- Keep `tests/eval/corpus.json` as a public regression fixture only; do not
  use it for new general-accuracy claims.
- Define a fresh, independently rated blind corpus process with English and
  Korean coverage across security, privacy, data integrity, destructive work,
  concurrency, reliability, performance, migration, and routine work.
- Extend offline scoring to report the confusion matrix, high-tier recall,
  over-routing, language/category slices, and confidence intervals.
- Agree release thresholds before classifier changes. A starting proposal is
  at least 90% high-tier recall on a sufficiently large held-out high subset,
  with an explicit, measured limit on high-tier inflation.

**Likely files:** `tests/eval/README.md`, `tests/eval/score.py`, new synthetic
fixture tooling, and focused classifier tests.

**Exit gate:** the evaluation protocol, labels, thresholds, and no-retention
handling are reviewed before any score-driven rule change lands.

### Phase 1 — Explainable risk floors

**Goal:** replace ad-hoc expansion with small, reviewable risk categories.

- Introduce versioned static reason codes for high-risk floors and narrow
  outcome patterns.
- Keep low classification conservative: only clearly mechanical tasks qualify.
- Preserve the existing stable default CLI output; add explanation only behind
  an explicit flag and only with reason codes.
- Add boundary tests for each floor, its false-positive exclusions, Korean and
  English phrasing, and privacy-safe explanation output.

**Likely files:** `src/weightclass/classification.py`,
`tests/test_classification.py`, `src/weightclass/cli.py`, `README.md`.

**Exit gate:** blind evaluation improves the pre-agreed high-tier metric, no
task text reaches output, and the default route remains source-vendor pinned.

### Phase 2 — User-selected risk posture

**Goal:** make the cost/safety trade-off explicit rather than hidden.

- Design a reviewed policy setting for at least `balanced` and `cautious`
  classification modes.
- In `cautious`, ambiguity can only escalate effort; it must never silently
  choose another vendor or model label.
- Render the posture and static decision reason for review before `run`.
- Measure high-tier share per fixed evaluation slice; report it rather than
  using a global cap that can hide distribution changes.

**Likely files:** `src/weightclass/router.py`, `src/weightclass/cli.py`,
`tests/test_router.py`, `README.md`, `docs/integrations.md`.

**Exit gate:** policy parsing fails closed, fingerprints bind the rendered
selection, and representative policies prove no accidental cross-vendor route.

### Phase 3 — Opt-in semantic triage experiment

**Goal:** evaluate whether a provider CLI improves ambiguous classifications
without becoming a default or a silent fallback.

- Use the existing opt-in vendor triage boundary only; do not add credentials,
  HTTP, retries, or persistent task storage.
- Compare local-only, vendor-only, and any proposed raise-only composition on
  a fresh blind corpus. Do not reuse the public corpus as acceptance evidence.
- Preserve terminal failure for unavailable triage; a failed triage call must
  not quietly become a local result.
- Reassess the documented prompt-injection limitation and tier-parser contract.

**Likely files:** `src/weightclass/triage.py`, `tests/test_triage.py`,
`tests/eval/score.py`, `README.md`.

**Exit gate:** the chosen composition beats the baseline against the agreed
metric and does not violate the source-vendor, no-retention, or fail-closed
contracts.

### Phase 4 — Local semantic model decision

**Goal:** decide whether an offline semantic classifier earns its complexity.

- Prototype only after Phases 0–3 establish a persistent semantic gap.
- A candidate must be pinned, deterministic across supported platforms,
  dependency-audited, benchmarked for startup/latency/memory, and raise-only
  until independently proven safe.
- Do not add local feedback learning, task hashes, acceptance telemetry, or
  automatic model downloads.

**Likely files if approved:** new isolated classifier adapter, pinned package
metadata, benchmark harness, supply-chain documentation, and platform tests.

**Exit gate:** proceed only when measured improvement justifies the package,
performance, maintenance, and supply-chain cost. Otherwise retain the
deterministic policy engine.

## Rejected directions

- Run a standard workflow first and retry at high after failure.
- Persist task-derived feedback or adaptive local routing state.
- Infer or switch vendors, model labels, subscriptions, or billing accounts.
- Scan arbitrary workspace code or route by unreviewed repository context.
- Tune toward the committed public corpus and present the result as general
  accuracy.

## Verification for every phase

1. Run focused unit and boundary tests before changing broader behavior.
2. Run the full suite, compile check, formatting/lint/type checks when
   available, and `git diff --check` before merge.
3. Confirm diagnostics and explanations never contain task text, task hashes,
   credentials, or provider output.
4. Review `wclass route` output and its fingerprint before any `wclass run`
   behavior changes.
5. Record aggregate evaluation results and confidence limits without storing
   runtime task content.

## Delivery status (2026-08-06)

The initial direction came from a partial, three-track brainstorming quorum
(Codex, Claude, Antigravity). A Planner–Architect–Critic `ralplan` worker
ended in a parent-process cascade before it produced a usable plan; active
unrelated `lterm` sessions were preserved rather than restarting the daemon.

The verified durable-goal run completed the roadmap through the triage gate:

- Phase 0: the committed corpus is regression-only; fresh synthetic corpora
  have aggregate-only, offline scoring and a blind release gate.
- Phase 1: static English/Korean high-risk floors and privacy-safe reason
  codes now supplement the deterministic classifier.
- Phase 2: `--explain` exposes static local decision metadata, while reviewed
  `balanced` and `cautious` policy postures preserve source-vendor pinning and
  fingerprint binding.
- Phase 3: vendor-only and raise-only comparisons are offline, opt-in
  experiments over evaluator-supplied blind evidence; they do not change the
  runtime default or invoke a vendor CLI.
- Phase 4: the current decision-gate branch accepts evaluator-supplied,
  opaque-ID-bound candidate predictions and produces deterministic aggregate
  candidate and same-corpus local-baseline metrics plus resource, supply-chain,
  privacy, raise-only comparison, and `go`/`no-go` evidence. It does not emit
  unverified candidate/baseline identifier values. A reproducible review
  template binds those identifiers to source revisions, records provenance,
  predeclared slice and confidence-sufficiency rules, and decides whether
  measured improvement justifies the added cost without adding a production
  model.

Phase 4 remains a **no-go** decision because no independently supplied
blind-corpus candidate evidence has satisfied that gate. The deterministic
policy engine remains the production path until measured improvement justifies
the dependency, resource, maintenance, and supply-chain costs.

### Token-efficiency evaluation gate

An offline paired scorer now evaluates externally collected, task-free net-token
evidence. It compares one frozen candidate with one frozen baseline on the same
sealed tasks, counts every invocation and rework attempt, requires blind quality
non-inferiority and no new critical failure, and emits aggregate-only evidence.
Collection remains outside weightclass: the scorer never invokes a vendor,
reads credentials, or infers token accounting or prices.

A separate offline cost scorer accepts externally normalized integer cost
units under an opaque reviewed contract. It reuses the privacy, quality,
coverage, and confidence gates but never fetches pricing or calls the result an
actual bill. A candidate may therefore reduce estimated cost while increasing
raw tokens; both metrics remain visible and neither scorer changes routing.

The safe experiment is a reviewed schema-1 policy whose standard command omits
a vendor effort override. No such policy has supplied independent passing
evidence, so `efficient` is not a posture, built-in standard routes remain
explicitly medium effort, and schema 2 still requires an explicit model/effort
pair. A passing scorer record would authorize review of the opt-in policy—not
an automatic built-in or default change.

A separate cost diagnostic now has exact evaluation-only schema-1 baseline and
candidate policies for Claude low-tier routing. A real `route` fingerprint
acknowledgement followed by `run` succeeded for both arms on one disposable
public-fixture task; Haiku/low reported 78.58% lower estimated cost while using
20.46% more raw tokens. This verifies the router path and the distinction
between cost and token objectives, but it is still a one-task diagnostic and
does not satisfy either promotion gate.

The next full 30-pair cost run completed all 60 counterbalanced arms and both
configurations passed 30/30 arm-blind quality reviews with no critical failure.
Haiku/low still used 6.94% more raw tokens while the mixed-tier policy reported
21.52% lower estimated cost. Its 95% cost-savings interval was only 8.08% to
34.95%, so the lower-bound and width gates failed; exact paired quality bounds
also require more than 30 perfect ties to establish the fixed 5%
non-inferiority margin. Phase 4 therefore remains `no-go`.

A nine-pair diagnostic then tested whether changing standard to Sonnet/medium
would strengthen the cost signal. Although aggregate reported cost fell
27.29%, standard quality regressed from 3/3 to 2/3 and standard raw tokens rose
58.29%. That candidate was discarded before any promotion-scale run.

Changing only standard effort from medium to low also failed its nine-pair
canary: blind quality tied 9/9, but aggregate cost savings were 14.08%, below
the fixed 15% floor, while raw tokens rose 6.49%. The standard slice itself was
11.08% more expensive. The original low-only cost candidate remains the sole
promotion-scale experiment.

A later exact Codex standard-tier diagnostic independently confirmed that the
standard-low direction is not a savings basis. Across nine existing public
standard fixtures, low effort used 111,567 raw tokens versus 101,602 for medium
(+9.81%), although the blind reviewer passed 8/9 low answers versus 5/9 medium
answers. The earlier one-pair probe had shown a decrease, so the token signal is
unstable as well as non-saving in the larger canary. Packaged Codex, `agy`, and
Grok experiment scaffolds therefore keep standard at medium. Future economic
experiments must change an explicit low-tier model/effort configuration and
bind actual sanitized metered-cost or quota evidence; an identical command is
ineligible even if its route ID differs.

A separate user-approved Codex model experiment then tested a low-only
`gpt-5.6-luna` candidate against `gpt-5.6-terra`. A nine-pair public-fixture
canary was directionally favorable: Luna's externally normalized estimated API
cost was 81.98% lower despite using 46.97% more tokens, and blind quality was
8/9 versus 7/9 with no critical failure. A fresh balanced 30-pair run reduced
estimated cost by 51.52% and passed 30/30 quality checks on both arms, but its
cost and exact-quality intervals were too wide, so it remained `no-go`.

The predeclared follow-up independently regenerated 90 tasks: 72 low, nine
standard controls, and nine high controls, with 45 en/45 ko and exactly ten
tasks in each fixed category. All 180 Codex arms and 90 blind reviews
completed. Estimated API cost fell 69.02%, with a 95% savings interval of
60.57% to 77.47%, and raw tokens fell 4.89%. Both configurations passed 85/90
quality checks, but the candidate introduced two new critical failures and its
exact quality-difference interval was -7.53% to +7.53%. The result is therefore
`no-go`; no Codex route was qualified or promoted. The evaluator added
isolation flags not present in the packaged command, so the run is a model
configuration diagnostic rather than an exact route qualification. Its API
rate normalization does not measure subscription billing or quota usage.

That retained candidate next completed a fresh balanced 150-pair run. Quality
was non-inferior (143/150 versus 142/150, exact 95% difference interval -2.41%
to +3.66%) with no new critical failure. Reported cost fell 17.57%, but its
95% interval was 12.30% to 22.84%; the 15% lower-bound gate alone failed. Raw
tokens rose 7.30%. The result remains `no-go`, and any subsequent target-tier
oversampling can support only the changed low route rather than a whole-policy
or built-in promotion.

The separately predeclared 90-pair low-target qualification then oversampled
the changed route (72 low) while retaining nine standard and nine high controls
plus every language/category slice. A clean restart completed 180/180 arms;
blind quality tied 88/90 with no new candidate critical failure. Reported cost
fell 55.14% with a 95% interval of 46.28% to 64.00%, while raw tokens rose
14.59%. Every machine gate passed. This authorizes only the exact checked-in
cost-focused low-route opt-in example; Phase 4 built-in/token-efficiency
promotion remains `no-go`.

### 0.4 hardening delivery

The post-0.3 review priorities shipped in weightclass 0.4.0:

- P0: Claude triage now requests safe mode, no tools/MCP, no local setting
  sources, no persistence, and an empty private working directory. On macOS its
  exact reviewed command additionally uses a fixed `sandbox-exec` metadata and
  private-root-rename profile while the pinned private root is read/execute
  only. Linux Claude triage fails closed until an equivalent filesystem
  containment command is separately reviewed; native Claude routing is
  unaffected. The bounded POSIX runner tears down the complete process group
  and accepts only one exact lowercase tier. It observes exit without reaping
  through `waitid` or the macOS Python 3.10 kqueue fallback. Codex triage fails
  closed because its documented CLI contract does not currently provide an
  all-tools-disabled mode; native Codex routing is unaffected.
- P1: all installed-runtime policy and descriptor readers share a bounded,
  duplicate-key-safe, regular-file JSON loader. Static inputs are validated
  before transient task stdin is read. High-tier explanations distinguish
  narrow risk floors from broad complexity signals, and bounded harmful-outcome
  patterns now work across line breaks without using their multiplicity token as
  independent instability evidence.
- P2: unexpected parser states fail closed, source-vendor/provider coverage and
  reason-only fingerprint invariants are tested, release metadata is checked
  against the installed CLI, and macOS Python 3.10/3.13 process/JSON boundary
  tests block CI and publishing.

Version 0.4.0 was published to PyPI through Trusted Publishing from tag
`v0.4.0`. The canonical formula and `ictechgy/homebrew-tap` both use the
published sdist URL and verified SHA-256, and a source upgrade plus formula test
passed locally.
