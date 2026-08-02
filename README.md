# weightclass

**weightclass** is a local, policy-driven router for Codex and Claude Code
workflows. It classifies a task in memory as `low`, `standard`, or `high`,
chooses a deterministic model-and-effort route, and can start one selected
vendor process in the foreground.

By default, a request stays with its explicit source vendor. Cross-vendor
routing is available only through a reviewed policy opt-in. An optional V2
route can start a separately installed API runtime after explicit review and
egress acknowledgement; weightclass never reads API credentials or makes
provider network requests itself.

## Install

weightclass has no runtime dependencies beyond Python 3.10 or later. Until a
package registry release is published, install it from a local checkout:

```sh
git clone https://github.com/ictechgy/weightclass.git
cd weightclass
python3 -m pip install .
```

This installs the `wclass` command. The native Codex and Claude CLIs must
already be installed and authenticated; weightclass never reads or changes
their authentication or subscription state.

For reviewable native Codex and Claude Code invocation examples, see
[Native integrations](docs/integrations.md).

## Run locally

Inspect a route before running it:

```sh
printf '%s' 'Fix a spelling typo in the README.' | wclass route --source-vendor codex
printf '%s' 'Fix a spelling typo in the README.' | wclass run --source-vendor codex
```

The built-in routes are intentionally conservative:

- Codex: `low`, `standard`, and `high` all use an ephemeral `exec` session in
  a workspace-write sandbox.
- Claude: `low`, `standard`, and `high` use print mode, manual permissions,
  no session persistence, and efforts `low`, `medium`, and `high`.

`--source-vendor` is required when weightclass is called from a Codex or Claude
integration. With the default policy, `--source-vendor codex` selects only
Codex routes and `--source-vendor claude` selects only Claude routes.
weightclass is a standalone process, so it does not try to infer its parent
application.

Classification is local and deterministic. Security, authentication,
authorization, data, migration, concurrency, performance, production, and
architecture signals route to `high`. Short typo, spelling, formatting, and
rename tasks route to `low`; other valid tasks route to `standard`. Unknown or
oversized task input fails closed.

## Override the routes

Use `wclass route --policy policy.json` or `wclass run --policy policy.json` to
use a reviewed local policy. Routes are considered in listed order, so the
first matching `tier` is selected. Add `--source-vendor codex` or
`--source-vendor claude` when invoking it from that vendor. Configure model
labels and vendor-specific effort arguments only with labels you know are
available to you.

```json
{
  "allow_mixed_vendors": false,
  "routes": [
    {
      "id": "codex-low",
      "vendor": "codex",
      "tier": "low",
      "model": "your-low-model-label",
      "command": ["codex", "exec", "--model", "your-low-model-label", "-"]
    },
    {
      "id": "claude-high",
      "vendor": "claude",
      "tier": "high",
      "model": "your-high-model-label",
      "command": ["claude", "--print", "--model", "your-high-model-label", "--effort", "high"]
    }
  ]
}
```

The `command` tokens and model labels are opaque policy values. weightclass
validates their shape but does not assert vendor CLI semantics or subscription
access. When a selected route declares `model`, `wclass route` includes that
model label in its output. Always run `wclass route` with a representative
non-sensitive task to inspect a policy before using `wclass run`.

Set `"allow_mixed_vendors": true` only when you intentionally want a Codex
request to select a Claude route, or the reverse. When it is `false` or absent,
the source-vendor filter is applied before tier and model selection.

## V2 API routing through an external runtime

V2 adds declarative API-route selection without turning weightclass into an API
client. weightclass does not read API keys, inspect authentication, or make
network requests. Instead, you provide an already-installed, trusted runtime
at an absolute, executable path. That runtime is responsible for provider
credentials, HTTP, billing, and any provider output.

Use a V2 policy only for API routes; unlike the V1 legacy policy, it cannot
contain arbitrary command arrays. A route is eligible only for its declared
source vendors. `codex` maps to the OpenAI provider family and `claude` maps to
the Anthropic provider family; `allow_cross_provider` must be `true` before a
route can cross those families.

```json
{
  "schema_version": 2,
  "allow_cross_provider": false,
  "allow_api": true,
  "routes": [
    {
      "id": "openai-high-api",
      "tier": "high",
      "eligible_source_vendors": ["codex"],
      "provider": "openai",
      "transport": "api",
      "model": "your-openai-model-label",
      "effort": "high",
      "intended_recipient": "OpenAI API",
      "intended_billing_boundary": "your OpenAI API account"
    }
  ]
}
```

First review the selected destination and copy the returned fingerprint.
weightclass reports the intended recipient and billing boundary from the
reviewed policy; it does not verify either claim.

```sh
printf '%s' 'Review this authorization change.' | \
  wclass v2 route \
  --policy api-policy.json --source-vendor codex \
  --api-runtime /absolute/path/to/weightclass-runtime
```

Starting an API route requires both an explicit egress confirmation and the
exact fingerprint from that review. weightclass recomputes the route before
spawning the runtime, so a change to the selected model, effort, source,
destination, runtime path, or API/cross-provider permission invalidates the
acknowledgement.

```sh
printf '%s' 'Review this authorization change.' | \
  wclass v2 run \
  --policy api-policy.json --source-vendor codex \
  --api-runtime /absolute/path/to/weightclass-runtime \
  --confirm-api-egress --ack-route-fingerprint 'sha256:copied-from-route'
```

For a selected V2 route, weightclass invokes exactly this fixed protocol,
without a shell, and passes the task only on standard input:

```text
/absolute/path/to/weightclass-runtime --provider PROVIDER --model MODEL --effort EFFORT
```

Do not put API keys, tokens, task text, or personal information in the policy,
route metadata, or command line. V2 does not provide retries, failover,
credential management, background execution, or a bundled provider runtime.

## Security boundary and non-goals

- No persistence: weightclass writes no router artifacts or vendor
  configuration.
- Task text is read only from standard input, held in memory to classify and
  pass to the selected child process, then discarded. weightclass never logs,
  stores, echoes, or places it in diagnostics.
- weightclass never reads credentials, subscription balances, pricing, cookies,
  or vendor configuration. It does not capture or process vendor output. V2
  does not issue provider HTTP requests; a separately installed runtime may do
  so only after the explicit acknowledgement described above.
- Route selection is deterministic. Unsupported, malformed, or unsafe input
  fails closed with a redacted JSON diagnostic.
- weightclass does not infer source vendor, model availability, subscription
  tier, or remaining usage. Supply the source vendor explicitly and declare
  model labels in reviewed policy when model routing is required.
- `wclass run` starts exactly one configured command in the foreground without
  a shell, retry, backgrounding, recovery, or process supervision.
- weightclass is not an API proxy, credential manager, cloud service,
  subscription checker, bundled provider runtime, or unattended multi-agent
  supervisor.
- Policies must be reviewed before use. Do not place secrets in a policy.

## Development verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 -m compileall -q src
```

## License

[MIT](LICENSE)
