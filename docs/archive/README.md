# Archived advisory documents

These documents described the explicit experimental `wclass-advisory`
companion and the proposals that were going to be measured through it. Every
one of them was **last shipped in 0.31.1; the companion was removed in 0.32.0.**

They are audit history, not continuation context. Do not start new work from
these pages, and do not treat a command, flag, state path, or document link they
describe as something the shipped tool still provides.

- [`advisor-arm-design.md`](advisor-arm-design.md) — an unimplemented proposal
  to reimplement the vendor advisor pattern locally, buying expensive guidance
  instead of expensive output.
- [`advisory-brainstorming-assessment.md`](advisory-brainstorming-assessment.md)
  — the decision to keep brainstorming as an experiment rather than promote it
  into the read-only design workflow.
- [`advisory-campaign.md`](advisory-campaign.md) — the sealed, task-free
  campaign contract: pre-registered gates, aggregate-only records, and the
  population rules evidence had to satisfy.
- [`advisory-evidence-workflows.md`](advisory-evidence-workflows.md) — the
  read-only `review`, `research`, `diagnosis`, and `design` workflows and their
  closed JSON result contract.
- [`advisory-experiments.md`](advisory-experiments.md) — the offline analyzer
  for pre-registered aggregate evidence records, and its closed schema-1 input
  format.
- [`advisory-next-experiments.md`](advisory-next-experiments.md) — the task-free
  operating plan for the next sealed populations, kept separate from the
  existing Shape-B campaigns.
- [`advisory-onboarding.md`](advisory-onboarding.md) — managed onboarding: how
  `init` laid out profile, campaign, verifier-wrapper, price-table, and result
  paths under a managed state root.
- [`advisory-product-roadmap-v2.md`](advisory-product-roadmap-v2.md) — the
  post-0.18.0 implementation contract for the companion, which explicitly did
  not change core classification.
- [`advisory-skill.md`](advisory-skill.md) — the packaged `advisory` Agent Skill
  bundle and its explicit opt-in installation and upgrade rules.
- [`advisory-vendor-profiles.md`](advisory-vendor-profiles.md) — the
  deterministic built-in and exact-command vendor profiles, their task-delivery
  review, and their egress confirmations.
- [`measuring-p-at-work.md`](measuring-p-at-work.md) — how to measure, on real
  work, how often a cheap route fails verification, and what changes when the
  vendors are billed by API key rather than by subscription.
- [`speculative-cheap-route-design.md`](speculative-cheap-route-design.md) — an
  unimplemented proposal to run the cheap route first and escalate when a verify
  command fails.

## Cost-recommendation documents

These described `wclass recommend` and `wclass review-cost-profile`, removed in
0.32.0 together with vendor triage, `wclass render`, and
`run --suggest-escalation`.

- [`cost-recommendation.md`](cost-recommendation.md) — the evidence-gated
  `recommend` layer: the opaque cost profile and qualification card it consumed,
  its fixed quality and uncertainty gates, canonical fingerprints, and the
  end-to-end workflow it prescribed. Last shipped in 0.31.1.

## Delegation and API-routing documents

These described `wclass delegate` and `wclass v2`, removed in 0.32.0.

Each line names what the document described and the last release that shipped
that surface.

- [`completion-audit-v2.md`](completion-audit-v2.md) — requirement-to-test
  completion map for the protocol-2 delegation and native routing brief. Last
  shipped in 0.31.1.
- [`delegation-qualification-oracles.md`](delegation-qualification-oracles.md) —
  the decision oracles behind the `delegate` conformance suite and its
  intentionally empty qualification registry. Last shipped in 0.31.1.
- [`delegation-roadmap.md`](delegation-roadmap.md) — the WCD1 role-delegation
  protocol, its policy schema, and the qualified-runtime plan. Last shipped in
  0.31.1.
- [`protocol-v2-migration.md`](protocol-v2-migration.md) — migration guide from
  protocol 1 to the WCD2 delegation protocol. Last shipped in 0.31.1.
- [`protocol-v2-security.md`](protocol-v2-security.md) — the security boundary
  and residual risks of the WCD2 delegation protocol. Last shipped in 0.31.1.
- [`protocol-v2-specification.md`](protocol-v2-specification.md) — the WCD2
  frame, descriptor, and permission-projection specification. Last shipped in
  0.31.1.
