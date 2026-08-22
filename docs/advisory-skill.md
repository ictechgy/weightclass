# Optional advisory Agent Skill

_Status: repository-only, explicit opt-in. Installing the skill does not promote advisory into the
distributed `wclass` runtime and does not change the one-child V1 boundary._

The portable [`advisory` skill bundle](../skills/advisory/SKILL.md) teaches Codex or Claude Code to
select the existing implementation, review, research, diagnosis, or design campaign; fix a
prospective verifier before dispatch; keep task content in a private temporary file; and preserve
separate sealed populations. It deliberately refuses ordinary non-advisory requests and
brainstorming.

## Prerequisite

The repository-only `wclass-advisory` command and its reviewed local profiles, prices, verifier,
and sealed campaigns must already be configured. The installer checks `PATH` and fails with
`advisory_command_unavailable` otherwise. A skill is instructions, not an advisory runtime or an
entitlement claim.

Review the routes before installing or using the skill:

```sh
wclass-advisory review
```

## Preview and install

The installer never reads task stdin, never contacts a provider, and never overwrites an existing
skill. Preview first:

```sh
python3 tools/install_advisory_skill.py --target both --dry-run
```

Then explicitly install for Codex, Claude Code, or both:

```sh
python3 tools/install_advisory_skill.py --target codex
python3 tools/install_advisory_skill.py --target claude
python3 tools/install_advisory_skill.py --target both
```

Targets follow the products' documented personal skill locations
([Codex](https://developers.openai.com/codex/skills),
[Claude Code](https://code.claude.com/docs/en/slash-commands)):

- Codex: `~/.agents/skills/advisory`
- Claude Code: `~/.claude/skills/advisory`

The installer validates the exact three-file bundle, rejects symlinks and extra files, preflights
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

This installer remains in `tools/` and the skill in `skills/`; neither is included in the wheel or
sdist while the measurement gate remains open. See the
[campaign contract](advisory-campaign.md) and
[vendor-profile setup](advisory-vendor-profiles.md).
