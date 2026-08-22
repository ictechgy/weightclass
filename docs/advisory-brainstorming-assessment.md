# Advisory brainstorming assessment

Decision: experiment before promotion

Brainstorming is a different product question from the current read-only
design workflow. Design compares a small, evidence-backed set of options and
returns a bounded recommendation. Brainstorming would deliberately expand the
candidate set before convergence, so its quality cannot be inferred from a
Shape-B rescue result.

The current binary Shape-B rescue design should not be reused directly. Shape B
asks whether one cheap attempt that failed a verifier can be rescued by advice
and one retry, with an escalation fallback. That binary pass/fail endpoint is
appropriate for measuring rescue cost, but it collapses brainstorming's key
outcomes: whether the candidates satisfy the brief, whether they are genuinely
different, and whether people prefer one. Reusing it would reward a single
acceptable answer, permit near-duplicates, and treat a verifier pass as a
substitute for design quality. It would also confound generation and critique
with the existing retry path.

## Proposed generator-critic experiment

Run a separate, sealed experiment outside the production workflow. A generator
produces a bounded batch of candidate directions from a task; an independent
critic scores each candidate against the same pre-registered brief and flags
unsupported claims, constraint violations, and duplicate ideas. A final
selection stage receives only the candidate records and critic results. Keep a
single-generator baseline and a generator-critic treatment, with the same task
sample, model-family boundary, token/accounting origin, and human-rating
protocol. Do not tune prompts, batch size, or the acceptance threshold after
seeing outcomes.

The experiment must pre-register at least these metrics:

- constraint compliance: the share of candidates satisfying every hard
  constraint, with critical violations reported separately;
- idea diversity: a blinded, pre-specified diversity measure plus a duplicate
  or near-duplicate rate, evaluated within each task rather than only in the
  aggregate;
- human preference: blinded pairwise or ranking preference from independent
  raters, with ties and inter-rater agreement reported.

The primary result should be the treatment-versus-baseline difference on the
pre-registered human-preference measure, subject to no material regression in
constraint compliance. Diversity is a quality dimension and a guard against
gaming the preference result; it is not permission to trade away hard
constraints. Report complete uncertainty intervals, task-level counts, and
all exclusions. A verifier may enforce the closed result shape, but it cannot
establish novelty or human preference by itself.

Until that experiment is run with enough independent tasks and a pre-registered
decision rule, brainstorming remains documentation and research only. It is
not a production workflow in this change.
