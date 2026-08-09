# Protocol 2 migration guide

Protocol 2 is opt-in and additive. Existing policies with no `schema_version`, explicit native schema 1 policies, legacy `render`, delegation protocol 1, WCD1 runtimes, and V2 API routes retain their existing behavior.

## Native routes

Create schema-2 `profiles`, `execution_targets`, `routes`, `profile_grants`, and `vendor_grants`. Each eligibility selector names the exact `source_vendor`, `source_profile_id`, and tier. Each target uses a closed Codex or Claude builder and declares exact allowed model/effort pairs.

Review with `wclass route --policy native-v2.json --source-vendor codex --source-profile codex-primary`. Copy the emitted `route_fingerprint`, then run the same policy and selector with `--ack-route-fingerprint`. A missing acknowledgement stops before reading the task. With `--tier`, the task is still validated but classification is skipped.

## Delegation routes

Policy, manifest, descriptor, runtime protocol, and frame must all be version 2/WCD2. Add explicit source and destination profiles, exact model/effort pools, tasks, graph edges, typed gates and projections, structural transitions, and directional grants for every changed endpoint dimension.

Review with `wclass delegate route --source-vendor ... --source-profile ...`; execute with the same inputs plus `--confirm-trusted-delegation-runtime` and the exact `--ack-route-fingerprint`. Protocol 2 is categorically incompatible with `--require-qualified-runtime` and fails before confirmation, acknowledgement, task access, or executable inspection. Protocol 1 qualification remains unchanged.

The external runtime must accept the closed protocol-2 argv and exact WCD2 bytes: `WCD2`, a big-endian descriptor length, a big-endian task length, canonical descriptor bytes, and exact task bytes, with no trailing bytes. Do not adapt a WCD1 runtime by silently accepting both formats.

## Operational checks

- Treat every account, recipient, billing, model, effort, permission, capability, and ownership value as a caller declaration.
- Review all source/destination transitions and grants, especially same-vendor profile changes and cross-vendor changes.
- Review the exact executable and argv. Observation rejects a final symlink/nonregular/nonexecutable file but does not eliminate intermediate-symlink or post-recheck replacement races.
- Expect one foreground direct child and no retry, fallback, background supervision, descendant containment, provider HTTP, credential handling, output parsing, or task retention by weightclass.

Rollback removes only protocol-2 dispatch and protocol-2 artifacts. It does not migrate or rewrite policy files, vendor configuration, credentials, protocol-1 runtimes, or qualification records.
