# Native integrations

weightclass is a local router, not a modification to Codex or Claude Code. It
does not install hooks, edit global configuration, infer the calling vendor, or
reuse an existing interactive agent session. Use these reviewable commands from
the project directory after installing `weightclass`.

## Shared safe workflow

1. Pass the task on standard input and review the selected route first.
2. Run the route only after the rendered command and vendor are acceptable. The
   command is the whole decision: the model and effort arguments appear in it.
3. Keep a Codex-originated task on Codex and a Claude-originated task on Claude
   unless a reviewed policy explicitly enables cross-vendor routing.

Do not put task text, credentials, tokens, or personal information on a command
line, in a policy, or in vendor-global configuration.

## Codex

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass route --source-vendor codex
```

After reviewing the JSON route descriptor, run the selected Codex workflow:

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass run --source-vendor codex
```

With the built-in policy, this starts exactly one foreground `codex exec`
process. It does not change the configuration or context of an already-running
Codex session. To select a model, pass a reviewed local policy via `--policy`;
model labels remain your opaque configuration.

## Claude Code

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass route --source-vendor claude
```

After reviewing the JSON route descriptor, run the selected Claude workflow:

```sh
printf '%s' 'Add a focused unit test for this formatter.' | \
  wclass run --source-vendor claude
```

With the built-in policy, this starts exactly one foreground `claude --print`
process with `acceptEdits` permissions and no session persistence. Print mode is
non-interactive, so a permission mode that prompts a human refuses every edit
while still exiting `0`; review the rendered command before running it. It does
not change
the configuration or context of an already-running Claude Code session. Use a
reviewed local policy for model selection; weightclass never probes model
availability, subscription access, or remaining usage.

## Policy review

Both integrations accept the same local policy format:

```sh
printf '%s' 'Review this authorization change.' | \
  wclass route --source-vendor codex --policy policy.json
```

`allow_mixed_vendors` is `false` by default. Set it to `true` only in a
reviewed policy when intentionally allowing a task to leave its source vendor.
For API routes, use `wclass v2 route` followed by the explicit egress
acknowledgement described in the main README; native integration commands never
read API credentials.
