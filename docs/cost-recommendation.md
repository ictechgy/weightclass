# Cost-aware recommendations

`wclass recommend` is an offline, non-executing advisory command. It compares
one packaged same-vendor candidate with that vendor's built-in route for the
task tier, then either recommends the candidate or explicitly abstains. It
does not start a vendor, retry, fall back, change built-ins, persist a
preference, inspect credentials, fetch prices, or claim knowledge of a bill.

The objective is **externally estimated cost per completed outcome**. A user or
evaluator supplies integer cost units under one reviewed measurement contract.
Those units should include every authorized attempt and rework needed to reach
the frozen completion rule. weightclass validates and binds the documents, but
does not verify their measurements or infer provider pricing.

Fixed-price subscription quota is a different objective. A lower quota draw can
increase remaining capacity without lowering the monthly bill. Use
`tests/eval/provider_usage_benchmark.py` for that comparison; its
`subscription_quota` result is explicitly `capacity_only` and cannot authorize
this cost recommendation path. The same adapter accepts sanitized
`metered_cost` units from a provider export, but it does not parse or verify the
raw export. Raw billing exports may contain account identifiers or other
sensitive metadata and must stay outside the repository and outside
weightclass; normalize them externally to bounded integer units after removing
task data and account identifiers.

## Workflow

1. Review the built-in baseline and packaged candidate for the same tier:

   ```sh
   task='Fix a spelling typo.'
   printf '%s' "$task" | wclass route --source-vendor claude --tier low
   printf '%s' "$task" | wclass route --preset claude-model-override --tier low
   ```

2. Put those exact route fingerprints and externally supplied cost units in a
   cost profile. Validate it and obtain its canonical fingerprint:

   ```sh
   wclass review-cost-profile --cost-profile cost-profile.json
   ```

   This command does not read task input or start a vendor.

3. Create a qualification card that binds the profile fingerprint, both route
   fingerprints, vendor, tier, measurement contract, and aggregate evaluation
   gates.

4. Request advice:

   ```sh
   printf '%s' "$task" | wclass recommend \
     --preset claude-model-override \
     --cost-profile cost-profile.json \
     --qualification-card qualification-card.json \
     --tier low
   ```

   Exit `0` with `"decision": "recommend"` means the exact candidate passed
   the local document checks. Exit `0` with `"decision": "abstain"` is also a
   valid result: the evidence is missing, stale, weak, mismatched, or shows no
   cost advantage. Malformed documents exit `2` with only
   `{"error": "invalid_input"}`.

5. If you decide to execute the recommendation, use the ordinary reviewed run
   flow and acknowledge the candidate's exact `route_fingerprint`. The
   recommendation fingerprint is evidence binding, not execution authority:

   ```sh
   printf '%s' "$task" | wclass run \
     --preset claude-model-override \
     --tier low \
     --ack-route-fingerprint 'sha256:REVIEWED_CANDIDATE_FINGERPRINT'
   ```

## Cost profile schema

The profile is a strict version-1 JSON object. Unknown fields, duplicate keys,
non-integer costs, and more than 128 route entries are rejected.

```json
{
  "schema_version": 1,
  "profile_id": "team-cost-v1",
  "measurement_contract_id": "reviewed-cost-units-v1",
  "unit": "reviewed-cost-unit",
  "identifiers_not_task_derived": true,
  "pricing_inferred": false,
  "actual_billing_claimed": false,
  "routes": [
    {
      "route_fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "expected_completed_cost_units": 100
    },
    {
      "route_fingerprint": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "expected_completed_cost_units": 40
    }
  ]
}
```

`profile_id`, `measurement_contract_id`, and `unit` are bounded opaque labels;
weightclass assigns them no provider semantics. `pricing_inferred` and
`actual_billing_claimed` must be `false`. Route fingerprints must be unique.
`identifiers_not_task_derived` must be `true`; it is the author's assertion
that none of the opaque labels encodes prompt material.
The review output includes `values_verified_by_router: false` to distinguish
schema validation from economic verification.

## Qualification card schema

The card is also strict version-1 JSON. Replace every fingerprint with the
exact value from the corresponding review. The `cost_profile_fingerprint`
comes from `review-cost-profile`.

```json
{
  "schema_version": 1,
  "card_id": "claude-low-qualified-v1",
  "identifiers_not_task_derived": true,
  "cost_profile_fingerprint": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "measurement_contract_id": "reviewed-cost-units-v1",
  "vendor": "claude",
  "tier": "low",
  "baseline_route_fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "candidate_route_fingerprint": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "status": "qualified",
  "sample_size": 90,
  "cost_savings_lower_bound_basis_points": 4628,
  "cost_savings_ci_width_basis_points": 1772,
  "quality_delta_lower_bound_basis_points": -402,
  "quality_noninferiority_margin_basis_points": 500,
  "new_critical_failures": 0,
  "all_attempts_included": true,
  "independent_quality_review": true,
  "covered_languages": ["en", "ko"],
  "covered_categories": [
    "concurrency",
    "data-integrity",
    "destructive-work",
    "migration",
    "performance",
    "privacy",
    "reliability",
    "routine",
    "security"
  ],
  "covered_tiers": ["low", "standard", "high"],
  "valid_until": "9999-12-31"
}
```

A recommendation requires all of these conditions:

- status is `qualified`, the card is not expired, and all profile, route,
  vendor, tier, and measurement bindings match;
- at least 30 paired outcomes;
- estimated-cost savings lower bound at least 15%, with confidence-interval
  width at most 20%;
- quality lower bound within a margin no greater than 5%;
- zero new critical failures;
- all attempts included and independent quality review asserted; and
- exact coverage of both languages, all nine fixed categories, and all three
  tiers.

The candidate command must also differ from the baseline command. A different
route ID or fingerprint around byte-identical executable arguments is not an
economic candidate and returns `abstain` with
`candidate_route_unchanged`.

These are conservative machine floors, not a claim that the evaluator's
assertions are true. The receipt says `assertions_verified_by_router: false`.
The full canonical qualification-card fingerprint is included in the receipt
and in the recommendation fingerprint, so changing any gate or binding changes
the recommendation identity.

## Provider boundaries

The initial command compares only a packaged preset with the built-in baseline
for the same vendor and tier. Cross-vendor selection is not supported.

- Claude and Codex expose reviewed tier model and effort override surfaces.
- Grok exposes reviewed tier model overrides; its packaged effort values remain
  opaque and are not user-overridden by this surface.
- `agy` exposes reviewed effort routing but no qualified model override.
- Claude and Codex deliver the task on stdin. `agy` and Grok retain
  `task_delivery: argv`, including the documented local process-inspection
  exposure if the candidate is later executed.

Only the exact Claude low candidate currently has repository-recorded
promotion-grade estimated-cost evidence. A user-supplied qualification card can
describe another exact route, but weightclass does not transfer Claude evidence
to Codex, Grok, or `agy`, and does not verify the external assertion.

Both input documents are read before task stdin. They must be bounded regular
files owned by the current user or root and must not be world-writable. The
receipt contains commands, fingerprints, fixed aggregate fields, and opaque
document labels, but never task text or a task hash.
