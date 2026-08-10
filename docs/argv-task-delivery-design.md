# Design: argv task delivery and additional native vendors

_Status: implemented. Written 2026-08-10._

## Problem

weightclass delivers the task to the selected child on standard input, always.
`claude` and `codex` both read a prompt from stdin, so the two built-in vendors
happen to fit. Other agent CLIs do not.

Measured on 2026-08-10 against installed builds:

| CLI | non-interactive entry | task source | verdict |
| --- | --- | --- | --- |
| `claude` | `--print` | stdin | works today |
| `codex` | `exec … -` | stdin | works today |
| `agy` | `--print <PROMPT>` | argv only | unusable today |
| `grok` | `-p/--single <PROMPT>` | argv or `--prompt-file` | unusable today |
| `gemini` | `-p/--prompt <PROMPT>` | argv, appends stdin | out of scope by request |

`agy --print ""` returns `Error: empty prompt`, and `grok -p ""` returns
`Error: --single: prompt is empty`; neither consumed the piped task. This is not
a configuration gap. A policy cannot express it either, because a policy
`command` is a fixed argv with no slot for the task.

`kimi`, `qwen`, and `deepseek` are not installed on the development machine.
Their invocation cannot be verified, so they are not part of this design.

## Goals

- `wclass run --source-vendor agy` and `--source-vendor grok` work with no
  policy file.
- A policy may place the task at an explicit position in its own `command`.
- A policy may name any vendor, so an agent this package ships no command for is
  still usable by whoever has it installed.
- Review output and route fingerprints continue to contain no task content.
- Existing policies and the existing stdin path change in no observable way.

## Non-goals

- Auto-selecting a vendor when none is named.
- A triage adapter for the new vendors. `--ask-vendor` stays unavailable for
  them, for the same reason the qualification registry stays empty: an
  unreviewed adapter that sends untrusted task text to a tool under unknown
  permissions is worse than no adapter.
- A **built-in command** for any CLI that cannot be exercised locally. Those
  vendors are reachable by policy, where the person who has the tool writes the
  argv. This package never guesses another program's flags.
- Any change to delegation protocol 1 or 2, or to native schema 2. Both keep
  their closed vendor vocabularies.

## Design

### The task placeholder

A route command may contain the literal token `{{task}}` exactly once:

```json
{
  "id": "agy-high",
  "vendor": "agy",
  "tier": "high",
  "command": ["agy", "--print", "{{task}}", "--effort", "high",
              "--mode", "accept-edits"]
}
```

Rules, enforced when the policy is parsed:

- The token must be an entire argv element. `--prompt={{task}}` is rejected, so
  there is never a question of how the task and a flag were joined.
- At most one element may be the token. Zero means stdin delivery, unchanged.
- Two or more is invalid input; a task delivered twice has no defined meaning.
- `{{task}}` is reserved. A command that needs to pass that exact literal to its
  program cannot express it, and no escape is provided. No known agent CLI takes
  such an argument, and an escape mechanism would add a parsing rule that every
  future reader has to learn for a case nobody has.

A command containing the token uses **argv delivery**. Its child receives empty
standard input. A command without it uses **stdin delivery** and behaves exactly
as it does today.

### Substitution

Substitution happens once, immediately before spawn, replacing the token element
with the validated task string. The task is used as read, not the normalized
form used for classification, matching what the stdin path already delivers.

Argv delivery additionally rejects a task containing `U+0000`. `execve` cannot
carry it, and the existing task validation does not exclude it because stdin
delivery can.

### Review and fingerprint

Both operate on the command **before** substitution, so both are task-free:

- `wclass route` prints `["agy", "--print", "{{task}}", …]`. The reviewer sees
  where the task goes without seeing a task.
- The fingerprint hashes the same unsubstituted command. Two different tasks at
  the same tier produce the same fingerprint, so one review still binds many
  runs — the property the current design deliberately protects by refusing to
  hash task content.

No fingerprint code changes. `native_route_fingerprint` already hashes
`route.command`, which still holds the placeholder.

The review descriptor gains `"task_delivery": "argv"` when the placeholder is
present, so a reviewer cannot miss that this route puts the task on the command
line.

### What review binds, honestly

For an argv-delivery route, the review binds the shape of the command, not the
exact string that reaches `execve`. One element is filled at run time. This is a
real narrowing of the existing guarantee and belongs in the security
documentation next to the executable-replacement residual, not buried.

### Built-in routes

`SUPPORTED_VENDORS` is replaced by two separate things it was conflating:

- `BUILT_IN_VENDORS` — the vendors this package ships a command for. It grows
  from `{claude, codex}` to `{claude, codex, agy, grok}`.
- `validate_vendor_label` — a format check on the label itself. Any printable,
  whitespace-free identifier of at most 64 bytes is a valid vendor.

The label was never a list of tools this package knows. `select_tier_route`
compares it as a string and `native_route_fingerprint` hashes it as a string;
neither holds vendor-specific knowledge. Closing it only ever gated which
containment boundaries a user was allowed to name.

Opening it is what makes this design usable for agents nobody here can verify.
Someone who has `qwen` installed writes a three-line route using the `{{task}}`
slot and it works, with no code change and without that CLI being installed on a
maintainer's machine to measure. That matters: an agentic CLI takes broad local
access and sends telemetry, and installing one purely to read its flags is a
real cost that should not be a precondition for supporting it.

The cost is stated rather than hidden: `--source-vendor` can no longer reject a
typo. `--source-vendor codx` was an argparse error and now selects nothing and
exits `unsupported_route`. That is the price of not maintaining a registry of
every agent that exists.

Six new default routes, three per vendor, following the existing tier pattern.
Exact flags are confirmed against the installed CLI before the change lands:

- `agy --print {{task}} --effort <low|medium|high> --mode accept-edits`
- `grok -p {{task}} --reasoning-effort <…> --permission-mode <…>`

`agy` maps cleanly: its `--effort` vocabulary is already `low|medium|high`, and
`--mode accept-edits` is the counterpart of `--permission-mode acceptEdits`.
`grok` exposes `--reasoning-effort`, `--permission-mode`, and `--sandbox`; the
precise values are settled empirically during implementation, not guessed here.

### Consequences elsewhere

Two places assume every supported vendor is `claude` or `codex` and must be
handled or they fail at run time rather than closed:

1. `v2.select_api_route` reads `SOURCE_PROVIDER[source_vendor]`
   (`src/weightclass/v2.py:167`) after admitting anything in
   `SUPPORTED_VENDORS`. With a new vendor this raises `KeyError` instead of a
   diagnostic. The V2 API path needs its own vendor set — the vendors whose
   provider and billing boundary are known — and must reject the rest as
   `unsupported_route`. The V2 API path is deliberately not extended: it exists
   to route to a paid provider endpoint, and a vendor with no known provider
   cannot be contained by `allow_cross_provider`.

2. `triage_descriptor` must answer for every supported vendor;
   `tests/test_triage.py::test_every_supported_vendor_has_a_reviewable_triage_descriptor`
   asserts it. `agy` and `grok` get `TRIAGE_UNAVAILABLE_REASONS` entries so the
   descriptor reports them unavailable with a stated reason rather than raising.

## Security

**Command lines are world-readable.** Any user on the machine can read another
process's argv through `ps`. Argv delivery therefore exposes task text to local
users for the lifetime of the child. On a single-user laptop this is
inconsequential; on a shared host it is not.

This is a residual the user accepts by writing `{{task}}` into a policy, or by
choosing an `agy`/`grok` built-in route. It is stated in `README.md` next to the
route documentation and in `docs/protocol-v2-security.md` next to the other
residuals. weightclass does not attempt to hide it, because it cannot: the
exposure is inherent to how these CLIs accept a prompt.

The no-persistence rule is unaffected. The task is never written to a file,
never logged, never hashed, and never included in review output or diagnostics.
`--prompt-file`-style delivery is rejected by this design for that reason.

## Compatibility

Additive for the placeholder: a policy with no `{{task}}` token behaves
identically, byte for byte, and the stdin path is untouched.

The vendor label is not additive in the same way. `SUPPORTED_VENDORS` is
**replaced**, not grown, by `BUILT_IN_VENDORS` plus `validate_vendor_label`, a
format check that carries no vendor vocabulary. Nothing previously accepted is
now rejected, but `--source-vendor` does more than widen: a typo that used to
fail at parse time against the closed `choices` list (`invalid_input`, exit 2)
now passes the format check and fails later, at route selection
(`unsupported_route`, exit 3) — the same cost already named in "Built-in
routes" above.

Under the `0.x` policy in `RELEASING.md` this is a minor bump: new command-line
surface, no removal.

## Testing

- Placeholder parsing: zero, one, and two occurrences; substring use rejected;
  the token as a bare element accepted.
- Delivery: an argv-delivery route receives the task in the expected argv
  position and receives empty stdin; a stdin-delivery route is unchanged.
- Task content stays out of review output and out of the fingerprint: two
  different tasks at one tier produce one fingerprint, and neither task appears
  in `route` output.
- `U+0000` in a task is refused before spawn for argv delivery.
- `v2 route`/`v2 run` with `agy` or `grok` exits `invalid_input` (exit 2), not
  a traceback. The V2 API subcommands keep closed argparse `choices` on
  `--source-vendor` (`API_SOURCE_VENDORS`, still just `{claude, codex}`), so an
  unlisted vendor is rejected at parse time and never reaches route selection.
  This is a stricter, earlier gate than the native path's `unsupported_route`,
  not a weaker one.
- `triage_descriptor` answers for all four vendors.
- Every new built-in route is exercised against a fake executable that records
  its argv and stdin, in the pattern the existing native tests use.

Live vendor calls are not part of the automated suite, matching the existing
practice of never invoking a real vendor from tests.

## Open items

- The exact `grok` flag values for permission mode and sandbox are confirmed
  during implementation against the installed build.
- `kimi`, `qwen`, and `deepseek` are added only once a machine has them and the
  invocation is measured the same way `agy` and `grok` were here.
