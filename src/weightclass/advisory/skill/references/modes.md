# Advisory workflow selection

Choose the narrowest mode matching the requested deliverable.

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
exact closed result schema for the selected mode.

Schema validity proves bounded structure only. For design, aesthetic quality
and human preference remain outside a mechanical verifier. For research, this
mode examines the repository and supplied sources; it does not itself grant
live-web access.

## Brainstorming boundary

Brainstorming is not a production workflow. Its desired endpoint is divergent
idea quality rather than binary rescue. A future experiment needs a sealed
generator-critic comparison with pre-registered constraint compliance, idea
diversity and duplicate rate, and blinded human preference.
