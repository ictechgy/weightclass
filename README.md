# Subscription Agent Router

Subscription Agent Router (SAR) is a local task router for native Codex and
Claude Code workflows. It classifies a task in memory as `low`, `standard`, or
`high`, selects a deterministic route, and can run one selected vendor process
in the foreground.

## Run locally

SAR uses only the Python standard library. From the repository root, inspect a
route before running it:

```sh
printf '%s' 'Fix a spelling typo in the README.' | PYTHONPATH=src python3 -m sar route
printf '%s' 'Fix a spelling typo in the README.' | PYTHONPATH=src python3 -m sar run
```

The built-in routes are intentionally conservative:

- `low`: Codex `exec`, ephemeral session, workspace-write sandbox for small edits.
- `standard`: Codex `exec`, ephemeral session, workspace-write sandbox.
- `high`: Claude in print mode with `high` effort, manual permissions, and no
  session persistence.

Classification is local and deterministic. Security, authentication,
authorization, data, migration, concurrency, performance, production, and
architecture signals route to `high`. Short typo, spelling, formatting, and
rename tasks route to `low`; other valid tasks route to `standard`. Unknown or
oversized task input fails closed.

The native Codex and Claude CLIs must already be installed and authenticated.
SAR does not inspect or change their authentication or subscription state.

## Override the routes

Use `sar route --policy policy.json` or `sar run --policy policy.json` to use a
reviewed local policy. Routes are considered in listed order, so the first
matching `tier` is selected. Configure model labels and vendor-specific effort
arguments only with labels you know are available to you.

```json
{
  "routes": [
    {
      "id": "codex-low",
      "vendor": "codex",
      "tier": "low",
      "command": ["codex", "exec", "--model", "your-low-model-label", "-"]
    },
    {
      "id": "claude-high",
      "vendor": "claude",
      "tier": "high",
      "command": ["claude", "--print", "--model", "your-high-model-label", "--effort", "high"]
    }
  ]
}
```

The `command` tokens and model labels are opaque policy values. SAR validates
their shape but does not assert vendor CLI semantics or subscription access.
Always run `sar route` with a representative non-sensitive task to inspect a
policy before using `sar run`.

## Security boundary and non-goals

- No persistence: SAR writes no router artifacts or vendor configuration.
- Task text is read only from standard input, held in memory to classify and
  pass to the selected child process, then discarded. SAR never logs, stores,
  echoes, or places it in diagnostics.
- SAR never reads credentials, subscription balances, pricing, cookies, or
  vendor configuration. It does not capture or process vendor output.
- Route selection is deterministic. Unsupported, malformed, or unsafe input
  fails closed with a redacted JSON diagnostic.
- `sar run` starts exactly one configured command in the foreground without a
  shell, retry, backgrounding, recovery, or process supervision.
- SAR is not an API proxy, credential manager, cloud service, subscription
  checker, or unattended multi-agent supervisor.
- Policies must be reviewed before use. Do not place secrets in a policy.

## Verify

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall -q src
```
