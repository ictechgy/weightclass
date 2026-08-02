# Subscription Agent Router

Subscription Agent Router (SAR) is a local, decision-only command renderer for
native Codex and Claude Code workflows. It reads a reviewed local policy and a
redacted request descriptor, selects the first exact match, then prints a JSON
command array for the user to inspect and run manually.

SAR does not execute the rendered command.

## Run locally

SAR uses only the Python standard library. From the repository root:

```sh
PYTHONPATH=src python3 -m sar --policy policy.json --descriptor descriptor.json
```

`policy.json` is trusted local configuration. Routes are considered in listed
order, so the first exact `vendor` and `workflow` match is selected.

```json
{
  "routes": [
    {
      "id": "codex-review",
      "vendor": "codex",
      "workflow": "review",
      "command": ["codex", "your-native-workflow", "--model", "preferred-label"]
    }
  ]
}
```

`descriptor.json` intentionally accepts only route-selection metadata:

```json
{
  "vendor": "codex",
  "workflow": "review"
}
```

The `command` tokens and model labels are opaque policy values. SAR validates
their shape but does not assert that they are valid vendor CLI arguments; review
the rendered output before using it.

## Security boundary and non-goals

- No persistence: SAR reads the two explicitly supplied JSON files and writes
  no router artifacts or vendor configuration.
- No credentials, subscription balances, pricing, cookies, task bodies, or raw
  vendor output are read, stored, or emitted by SAR.
- Route selection is exact and deterministic. Unsupported or malformed input
  fails closed with a redacted JSON diagnostic.
- SAR is not an API proxy, credential manager, cloud service, subscription
  checker, vendor-command executor, or unattended process supervisor.
- Policies must be reviewed before use. Do not place secrets or task content in
  either input file.

## Verify

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall -q src
```
