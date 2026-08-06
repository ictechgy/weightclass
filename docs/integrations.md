# Native integrations

weightclass is a local router, not a modification to Codex or Claude Code. It
does not install hooks, edit global configuration, infer the calling vendor, or
reuse an existing interactive agent session. Use these reviewable commands from
the project directory after installing `weightclass`.

## Shared safe workflow

1. Pass the task on standard input and review the selected route first, then
   pass its `route_fingerprint` to `run` so the selection you read is the
   selection that executes.
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
while still exiting `0`. `route` and `run` read the policy separately, so pass
`run` the `route_fingerprint` from the review to bind the two together; without
it the run re-selects whatever the policy says at that moment. It does not
change
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
The optional `posture` is `balanced` by default. A reviewed `cautious` posture
raises ambiguous local `standard` decisions to `high` while retaining the same
source-vendor filter and opaque policy commands. The route review displays the
explicit posture and static reason code, and its fingerprint binds the posture.
For API routes, use `wclass v2 route` followed by the explicit egress
acknowledgement described in the main README; native integration commands never
read API credentials.

## Offline Claude and Codex role review

`wclass delegate route` compiles the same planner/worker/reviewer contract for
Claude-family and Codex-family profiles while keeping every role with the
explicit source vendor:

```sh
wclass delegate route \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor claude \
  --tier standard
```

The route command reads no task standard input and does not inspect or execute
the runtime path. Its `declared_enforcement` descriptor reports only that the
policy and offline capability declaration are schema-compatible. It is not a
runtime handshake, proof of delegation, or semantic-authorship claim.

After review, P0.5 can start one user-supplied trusted runtime:

```sh
printf '%s' 'Apply the reviewed change.' | \
  wclass delegate run \
  --policy delegation-policy.json \
  --runtime-manifest runtime-manifest.json \
  --delegation-runtime /absolute/reviewed/runtime \
  --source-vendor claude \
  --tier standard \
  --confirm-trusted-delegation-runtime \
  --ack-route-fingerprint 'sha256:copied-from-route'
```

The runtime receives the exact canonical descriptor and transient task through
the WCD1 stdin frame and owns all Claude/Codex process creation, authentication,
network, billing, permissions, review, integration, descendant cleanup, and
output. weightclass bundles no runtime and cannot detect a dishonest zero exit.

For the P1 gate, add `--require-qualified-runtime` to both the review and run
commands. Production consults only the registry shipped inside the weightclass
package; it accepts no environment, CLI, or user-registry override. The current
registry is empty, so this mode intentionally returns `unsupported_route` for
every adapter. If a reviewed package record is added later, the route binds its
build/platform/protocol/adapter/vendor identity, exact artifact size and
SHA-256, suite revision, and conformance-evidence digest. Run verifies those
artifact bytes before reading task stdin. The later path-based spawn still has
a documented hash-to-spawn replacement race.

`wclass delegate qualification-candidate` only validates a complete task-free
evidence document and hashes a local executable for package-maintainer review.
It neither updates the registry nor proves that the supplied evidence was
independently collected.

See [the delegation roadmap](delegation-roadmap.md) for the strict schema and
the exact-artifact qualification gate. Do not put
task content, credentials, recipient data, or billing identifiers into a role
profile, runtime manifest, or conformance evidence document.
