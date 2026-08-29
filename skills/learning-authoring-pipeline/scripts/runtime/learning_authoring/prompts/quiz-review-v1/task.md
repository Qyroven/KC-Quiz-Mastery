# Task

Review every actual question in the host-issued frozen Quiz snapshot exactly
once using the foundation, rulebook, and output schema. There is no fixed question
count, KC count, page list, domain, or required distribution of verdicts.

Follow the learner-packet-before-companion sequence. Retain each compact
independent answer and then check it against the supplied key, rubric, source,
and hints. Do not use generator reasoning or a form-check warning as proof that
an answer is correct or that a semantic defect exists.

For each item, compare the work actually requested with its key and Bloom label;
for `short_text`, also check what the rubric demands. Check a valid alternative
answer, plausible distractor mistakes, and cumulative hint leakage. For code tasks, resolve executable versus
pseudocode assumptions before judging the answer. Report concrete mismatches,
not a preference for harder questions, more variants, or a particular format.

Read `input_boundary.learner_questions` first, using the supplied source/KC as
the course knowledge when needed. Preserve the compact answers before opening
the companion at `input_boundary.answer_material.path`; verify its declared
hash. Use that companion only after solving to inspect keys, rubrics, and hints.
The separate `kc`, `assessment_slots`, `extraction`, `context`, and
`source_locators` fields provide the bound course context and source locations.

Copy `input_boundary.expected_source_ref` as `source_ref` exactly. Copy the frozen
`input_boundary.reviewer_mode` into `reviewer.mode` and supply your actual reviewer label and
model (null if unknown). The mode is host-bound; label and model are self-reported.
Inspect only the relevant declared sources; report coverage and limitations
honestly. Preserve
every question's `question_id`, `kc_id`, and nullable `slot_id`; legacy questions
without slots remain without slots. Complete all six criterion objects even
when a criterion is inapplicable, explaining why, rather than silently skipping it.

Write the requested JSON artifact only, without fences or surrounding prose.
Use the learner/reviewer language requested in the task; preserve schema enums
and identifiers exactly. Keep rationales and observations concise and verifiable.
No provider API call, candidate edit, auto-repair, approval, training, or deployment
is part of this review task.
