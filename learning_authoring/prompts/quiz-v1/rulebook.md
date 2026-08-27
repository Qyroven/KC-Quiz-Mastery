# Quiz — Rulebook

1. Use the selected `leaf_kcs`, their `kc_groups`, and runtime policy only. Do not invent a new learning objective or require outside facts.
2. Respect each KC's `knowledge_description`, `observable_claim`, and `assessment_boundary`. Cite only evidence references already present in that KC.
3. Produce exactly `runtime.variants_per_kc` standalone questions for every selected KC. Variants must differ in the learner reasoning required—not just names, numbers, wording, option order, or surface context.
4. Give each question one bounded task. Include only information needed to solve it; do not state the rule application, classification, conclusion, or answer in the stimulus.
5. Choose the simplest interaction that validly elicits the target evidence. Do not force interaction diversity when it weakens the question.
6. For selected response, first identify plausible mistakes a partially informed learner could make. Turn those mistakes into options with parallel grammar, specificity, qualification, tone, and approximate length.
7. The correct choice must not be the only nuanced, safe, qualified, detailed, or obviously professional choice. Do not use absurd, absolute, or careless distractors that can be rejected without knowing the KC.
8. A question must have one defensible answer under the exact facts shown. Remove ambiguity rather than explaining it away in the answer explanation.
9. For matching, serialize right-side options in a non-aligned order and add an extra plausible right-side option when the construct allows it. For ordering, serialize learner-visible options in a scrambled order.
10. Use short text only when producing an explanation, diagnosis, calculation, or decision rationale is itself the evidence. Keep its rubric observable and bounded.
11. Keep the question concise. Difficulty must come from applying or distinguishing the KC, not from long prose, hidden assumptions, trivia, or linguistic tricks.
12. Follow the output schema exactly. Do not emit planning commentary, quality claims, approval decisions, or fields that belong to another interaction.
13. Write learner-facing content in `runtime.language`. When it is `source`, follow the dominant language of the selected KCs while preserving source technical terms and acronyms.
