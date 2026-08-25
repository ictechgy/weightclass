# Read-only advisory evidence workflows

`wclass-advisory run --workflow` keeps implementation, review, research,
diagnosis, and design in separate evaluation populations. `implementation`
retains the existing patch-and-test behavior. The other workflows require a
closed JSON result, reject every repository edit, and pass the result to the
reviewed verifier only through standard input.

The available read-only contracts are:

- `review`: summary, zero or more structured findings, and limitations;
- `research`: question, one or more evidence-backed claims, and limitations;
- `diagnosis`: symptom, one or more hypotheses, reproduction steps, and
  limitations.
- `design`: problem, principles, one or more bounded options, a recommendation,
  acceptance criteria, validation steps, and limitations. Every option includes
  evidence, strengths, risks, and affected surfaces.

`weightclass.advisory.advisory_evidence_contract` defines the exact bounded shapes. Schema
validation proves only that the result is parseable, bounded, and closed. It
does not prove that a finding is real, a source supports a claim, or a proposed
root cause is correct. A task-specific executable verifier must read the JSON
from stdin and return exit zero only when the pre-registered factual or
mechanical acceptance criteria hold. The verifier receives
`WCLASS_ADVISORY_WORKFLOW` and runs in a clean reconstructed checkout with a
scrubbed temporary HOME.

Design is evidence, not brainstorming. It is a convergent, repository-grounded
comparison of a bounded set of options; it does not add a production
generator/critic loop or persist candidate ideas. A verifier can check hard
constraints, cited repository observations, accessibility rules, or screenshot
thresholds, but schema validity cannot establish aesthetic quality or human
preference.

Example:

```sh
wclass-advisory run \
  --campaign-root /private/results \
  --vendor codex \
  --workflow review \
  --repo /path/to/clean/repo \
  --task-file /private/path/review-task.txt \
  --route-profile /private/path/claude-profile.json \
  --confirm-task-egress \
  --advise-on-failure --advisor-context prompt \
  --verify /private/path/review-verifier \
  --campaign /private/path/review-campaign.json \
  --sample-ordinal 1 \
  --out-dir /private/path/review-results
```

Profile-based evidence executors compile to Claude `plan` permission without
`Edit`, or Codex `read-only` sandboxing. Exact commands remain operator-owned,
but any filesystem change is still detected in a clean handover clone and
rejects the attempt.

The winning JSON is printed only after aggregate logging succeeds. Task,
result, advice, verifier output, paths, hashes, and source locators are never
written to the campaign log. Evidence attempts never create patch files and
all workspaces are discarded, including successful ones. A verifier is still
ordinary user-authority code rather than an OS sandbox; use a container or jail
when that boundary is required.

## Separate campaigns

Evidence campaigns use manifest schema 2 and bind `workflow` into the campaign
fingerprint and each opaque ordinal record. Existing schema-1 implementation
campaigns remain byte- and shape-compatible and implicitly mean
`workflow=implementation`. Never combine modes in one output directory or
interpret one mode's pass rate as another mode's effectiveness.
