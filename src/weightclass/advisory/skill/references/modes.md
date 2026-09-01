# Advisory workflow selection

Choose the narrowest mode matching the requested deliverable.

`review`, `research`, `diagnosis`, and `design` use stateless `ask` by default.
They need a prospective verifier only when the user explicitly requests a
measured campaign. `implementation` remains campaign-only because it can produce
a patch and therefore needs prospectively fixed acceptance.

| Mode | Use for | Prospective verifier should establish |
| --- | --- | --- |
| `implementation` | Source or documentation changes that produce a patch | Focused behavior fails at baseline and passes after the patch; unrelated checks remain healthy |
| `review` | Evidence-backed findings and counterevidence | Seeded cases or known controls are classified correctly; unsupported findings are rejected |
| `research` | Repository/document evidence synthesis | Required claims cite fixed sources and satisfy a pre-registered factual rubric |
| `diagnosis` | Reproduction and root-cause hypotheses | The reproduction is real and the claimed cause explains or predicts the observed behavior |
| `design` | Convergent, repository-grounded options and recommendation | Hard constraints, cited observations, accessibility or compatibility checks, and measurable acceptance criteria hold |

Evidence modes are read-only. Claude executors use `dontAsk` with only Read,
Glob, and Grep plus a task-free JSON Schema; their advisor remains in plan
mode. Codex executors use a read-only sandbox. Any tracked, untracked, ignored, or
known agent-scaffolding write rejects the attempt. The runner supplies the
selected workflow schema to the provider and then enforces that exact mode
again in its local byte-bounded parser.

The Skill manifest's `schema_version` identifies the bundle manifest format;
it is independent of campaign, evidence, native-routing, and protocol schemas.

Schema validity proves bounded structure only. For design, aesthetic quality
and human preference remain outside a mechanical verifier. For research, this
mode examines the repository and supplied sources; it does not itself grant
live-web access.

Choose one invocation stage separately from the workflow: `plan` before
substantial work, `pivot` for a stuck approach, `final` before acceptance, or
`manual` for an explicit standalone question. Choose the narrowest sufficient
context independently: task only, bounded tracked diff, explicit UTF-8 files,
or the read-only repository. A multi-vendor council is never implicit; it
requires an explicit request and two to four named vendors.

## Campaign path

Use a campaign only for implementation or explicit measurement. The clean
baseline must commit the workflow verifier under `.weightclass`: `verify` for
implementation or `verify-review`, `verify-research`, `verify-diagnosis`, and
`verify-design` for evidence workflows. The complete `.weightclass` directory
is a protected verifier zone during candidate evaluation; keep every trusted
verifier helper and fixture there. Candidate-owned dependencies outside that
zone are not protected acceptance criteria.

`campaign verifier scaffold` creates a reject-all template. Customize it to
return 42 for the task-free baseline probe, 0 only for an acceptable candidate,
and another nonzero status for infrastructure failure. Commit the verifier zone
and run `campaign verifier check`.

If setup is missing, ask for the caller's opaque cheap/advisor/expensive model
and effort labels, obtain permission to persist task-free configuration, then
run `campaign init`; never inspect vendor configuration. `campaign check`
validates every requested workflow route and reports the managed wrapper SHA and
whether it matches the installed package.

Run tasks only through `campaign run --confirm-task-egress` stdin. Custom
providers also require explicit provider egress and task-free conformance.
Never wrap the sealed cheap/advisor/retry/expensive sequence in another retry.
Gate population rule v2 excludes infrastructure failures from every attempted
role; historical v1 gates retain their sealed rule.

Keep workflows/vendors in separate populations and one primary preregistered
gate per state root. Never rewrite, merge, resize, reseal, or repair old records.
Re-run the exact migration after partial setup. `managed_runner_version_changed`
and `managed_setup_busy` are not campaign samples.

## Brainstorming boundary

Brainstorming is not a production workflow. Its desired endpoint is divergent
idea quality rather than binary rescue. A future experiment needs a sealed
generator-critic comparison with pre-registered constraint compliance, idea
diversity and duplicate rate, and blinded human preference.
