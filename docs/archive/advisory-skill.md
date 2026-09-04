# Installed advisory Agent Skill

_Status: distributed as an explicit experimental companion command. Installing
the skill does not change the one-child `wclass` boundary._

The portable [`advisory` skill bundle](../src/weightclass/advisory/skill/SKILL.md)
uses stateless `ask` for ordinary review, research, diagnosis, and design. The
host vendor's configured default model is called once in read-only mode; no
profile, campaign, verifier, digest, or task file is required. Implementation
and explicitly measured work retain the separate sealed campaign workflow.

## Prerequisite

Install `weightclass` and the desired vendor CLI. No advisory initialization is
needed for one-shot use. The tool does not read or modify vendor authentication;
the selected CLI owns its authentication and provider request.

The labels are opaque caller input, not recommendations:

```sh
printf '%s' 'Review this repository.' | wclass-advisory ask \
  --vendor claude --workflow review --stage final --context repo \
  --repo . --confirm-task-egress
```

One-shot calls can declare `manual`, `plan`, `pivot`, or `final` timing and use
task-only, tracked-diff, explicit-file, or repository context. `--preview`
shows the task-free route, delivery exposure, context, and child bound without
reading stdin or repository content. `--auto-skip-trivial` can avoid plan/final
vendor calls using the local classifier before probes, consent, or snapshot.
Review receipts retain every original
finding while adding local `file:line` triage and invocation-only semantic
groups; human output folds rejected and duplicate findings.
Diff context contains only bounded tracked `HEAD` changes and disables external
diff and text conversion; it omits untracked files. Git is resolved to an
observed absolute executable and its Git/worktree directories are explicitly
bound to the selected standard repository; linked worktrees fail closed.
Explicit files are
no-follow UTF-8 reads with 32 KiB per-file and 128 KiB aggregate limits and never
admit `.git`. These selectors narrow the prompt and working directory, but do
not confine the vendor from the host filesystem. Diff/files/repo contexts
snapshot the target worktree. Task-only context does not request repository
access, snapshot, or local file:line triage and reports that explicitly.

`--council codex,claude` is an explicit two-to-four-vendor surface. It
preflights every member before task input, runs fresh independent processes,
preserves descriptive consensus and dissent, and never selects a winner. It is
not an automatic cross-vendor route and remains `quality_verified:false`.
A complete council exits 0. A partial council preserves successful results and
fixed per-member failure codes in its schema-2 receipt, then exits 3. One
whole-council deadline defaults to the per-child timeout and can be narrowed
with `--total-timeout-seconds`.

Initialize opaque model/effort profiles only for an explicit campaign.

The one-shot preflight is task-free; the selected vendor call receives the task
only after confirmation. An agy `{{task}}` route explicitly exposes task text to local process
inspection; a Grok `{{task_file}}` route uses an inherited anonymous `/dev/fd/N`
pipe with no task pathname. Schema-2 campaign profiles must
declare exact `implementation` and `evidence` matrices for `cheap`, `advisor`, and `expensive`.
Do not invent model quality, pricing, entitlement, or subscription availability for agy or Grok;
their labels are user-selected opaque configuration.
`doctor` reports local CLI compatibility after invoking `--help`/`--version`
with a minimal environment and temporary working directory; it sends no task or
provider prompt. An optional
`provider-check --confirm-provider-egress` makes three task-free calls per
vendor, stores nothing, and never contributes a campaign sample. Calls that
use the same executable remain serial; distinct executable groups run with a
bounded concurrency of four and use separate temporary workspaces. A one-shot
custom-provider `consult` checks only the selected role before task access.
Existing agy installations use `migrate-routes --vendor agy`; existing Grok
evidence installations use `migrate-evidence --vendor grok`. Both preserve old
populations without merging records.

New campaigns may preregister one primary statistical gate by running
`migrate-gate` immediately after ordinary `init` and before dispatch. Its
`--gate-metric`, `--gate-target-rate-bps`, and `--gate-alpha-bps` flags are all
required and are sealed in a separate schema-3 generation. Existing
schema-1/2 populations use the same explicit command to start an empty gate
generation; old campaign and record bytes are never copied, rewritten, or
rebound. One state root admits exactly one primary vendor/workflow gate, so the
requested `migrate-gate` must name `--workflow` and a second primary population
is rejected rather than silently multiplying alpha. Legacy gate analysis is
exploratory only and cannot make a promotion-eligible claim.

## Preview and install

The installer never reads task stdin, contacts a provider, or discovers vendor configuration.
These commands are the sole vendor-recognized-path exception. Explicit `skill install` writes
only the exact reviewed bundle, while `skill uninstall --confirm`
removes only an exact package-owned bundle from the selected personal Skill directory. Neither
reads or rewrites other Codex or Claude configuration. Ordinary install never overwrites an existing skill. Preview
first:

```sh
wclass-advisory skill status --target both
```

Status is non-mutating and exits successfully for customized targets, listing
them under `conflicts`; install and uninstall still reject those targets.

Then explicitly install for Codex, Claude Code, or both:

```sh
wclass-advisory skill install --target codex
wclass-advisory skill install --target claude
wclass-advisory skill install --target both
```

An upgrade replaces only an exact package-owned historical bundle recorded in the reviewed
compatibility ledger. A modified skill, symlink, or extra file still fails closed:

```sh
wclass-advisory skill install --target both --upgrade --dry-run
wclass-advisory skill install --target both --upgrade
```

Targets follow the products' documented personal skill locations
([Codex](https://developers.openai.com/codex/skills),
[Claude Code](https://code.claude.com/docs/en/slash-commands)):

- Codex: `~/.agents/skills/advisory`
- Claude Code: `~/.claude/skills/advisory`

The installer validates the exact four-file bundle, rejects symlinks and extra files, preflights
every selected destination before writing either one, installs owner-only files, and treats an
exact existing copy as idempotent. A different file, directory, or symlink at either destination
returns `skill_conflict`; move or review it yourself rather than asking the installer to destroy
it.

## Invoke

Codex:

```text
$advisory design this interface change
```

Claude Code:

```text
/advisory review this change
```

Normal implicit selection is available, but the description is intentionally narrow: the user
must explicitly say advisory. Claude Code `--safe-mode` disables personal skills by design.

Each vendor/workflow has ten fixed anonymous lanes by default;
lane 0 preserves the existing campaign root and extra lanes use bounded `.lanes/lane-XX` names
without project-derived identifiers. Independent projects and vendors can therefore start
concurrently, while cheap/advisor/retry/expensive stages inside one lane remain sequential. The
command leases one free lane before any child starts. Reports validate and merge every lane before
computing promotion gates. Compare vendors only when the operator deliberately admitted the same
task to each separate campaign.

The command and exact four-file skill bundle are included in the wheel and sdist. Advisory remains
experimental and never becomes an automatic `wclass run` route. See the
[managed onboarding guide](advisory-onboarding.md),
[campaign contract](advisory-campaign.md) and
[vendor-profile setup](advisory-vendor-profiles.md).
