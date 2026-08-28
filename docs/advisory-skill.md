# Installed advisory Agent Skill

_Status: distributed as an explicit experimental companion command. Installing
the skill does not change the one-child `wclass` boundary._

The portable [`advisory` skill bundle](../src/weightclass/advisory/skill/SKILL.md) teaches Codex or Claude Code to
select the existing implementation, review, research, diagnosis, or design campaign; fix a
prospective verifier before dispatch; keep task content in a private temporary file; and preserve
separate sealed populations. It supports reviewed schema-1 Claude/Codex/agy/Grok profiles and
schema-2 arbitrary-vendor command matrices, but it must report whether delivery is stdin, argv, or
a private file before a run. It deliberately refuses ordinary non-advisory requests and
brainstorming.

## Prerequisite

Install `weightclass`, then initialize each desired vendor once with reviewed model and effort
labels. Managed onboarding creates owner-private, task-free profiles, five sealed workflow
campaigns, and result lanes in the platform state directory. It does not read or modify vendor
authentication. A skill is instructions, not an entitlement claim.

The labels are opaque caller input, not recommendations:

```sh
wclass-advisory init --vendor claude \
  --model cheap=CHEAP --model advisor=ADVISOR --model expensive=EXPENSIVE \
  --effort cheap=low --effort advisor=high --effort expensive=high
wclass-advisory doctor --vendor claude --workflow all
wclass-advisory cli-check --vendor all
wclass-advisory review --vendor claude --workflow implementation
```

The review is task-free. An agy `{{task}}` route explicitly exposes task text to local process
inspection; a Grok `{{task_file}}` route uses an owner-only transient file outside the Git
workspace. Schema-2 profiles must
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

New campaigns may preregister one primary statistical gate with
`--gate-metric`, `--gate-target-rate-bps`, and `--gate-alpha-bps` on `init`.
All three are required together and are sealed in a separate schema-3
generation. Existing schema-1/2 populations can be copied into an empty gate
generation only with explicit `migrate-gate`; old campaign and record bytes are
never rewritten or rebound. Legacy gate analysis is exploratory only and
cannot make a promotion-eligible claim.

## Preview and install

The installer never reads task stdin or contacts a provider. Ordinary install never overwrites an
existing skill. Preview first:

```sh
wclass-advisory install-skill --target both --dry-run
```

Then explicitly install for Codex, Claude Code, or both:

```sh
wclass-advisory install-skill --target codex
wclass-advisory install-skill --target claude
wclass-advisory install-skill --target both
```

An upgrade replaces only an exact package-owned 0.16.2, 0.17.0, or 0.17.1 bundle. A modified skill,
symlink, or extra file still fails closed:

```sh
wclass-advisory install-skill --target both --upgrade --dry-run
wclass-advisory install-skill --target both --upgrade
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
