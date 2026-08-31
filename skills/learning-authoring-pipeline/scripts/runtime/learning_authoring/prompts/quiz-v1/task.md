# Task

Read the complete selected KC content and its source evidence using suitable tools. The optional
`agent-read` index offers KC-group batches; combine or divide reading units as needed. A batch
is a reading aid, not a quota.
Keep concise evidence intents across batches so reconciliation does not collapse distinct targets
or repeat the same question. Use worked examples for the decision they illustrate, not their topic,
counts, IDs, option positions or vocabulary.

For each target, draft the learner task and a defensible answer together. Decide the response type
from the work required, then record the slot and useful variants. Assign Bloom and difficulty after
checking what the learner must actually do. Do not fill a prescribed ladder or interaction mix.
Do not assign type, variant count, Bloom or difficulty through unexamined constructor defaults, or
derive assessment coverage from a loop over KC IDs. Equal choices are acceptable when warranted.
For new items, write `assessment` with the final item's `cognitive_operation`, `intended_difficulty`
and a concrete `rationale`; the slot's labels are planning context, not inherited item labels.
When a demanding goal needs preparation, author a useful practice sequence without forcing every
KC into it. Identify preparatory versus independent items and recommended relationships/order in
existing report notes. This is content authoring, not a new scheduler or mandatory generation stage.

Before delivering a candidate, try concrete answers against each question. These are author
self-checks, not a claim of independent or blinded review, and they apply to the final revision:

- Solve from the learner view alone, without source-only context, hints or the key. Recompute
  quantitative givens and verify the final keyed IDs after options are serialized.
- Try the strongest defensible competing answer, not only a renamed exemplar or obviously wrong
  response. Missing conditions may make it valid even when the key excludes it. Also score a
  near-correct response with a consequential error. Apply the criteria: award valid work its deserved
  credit and withhold credit for the actual defect, not for different wording.
- Try to answer without the target knowledge using length, tone, position, repeated wording or
  elimination. Check whether copying the stimulus supplies the requested inference. If a shortcut
  bypasses the intended work, change the item, not merely its explanation; necessary givens and
  honestly scoped recognition tasks are not defects by themselves.
- Follow hints in order; identify the concrete work still unresolved after each prefix.
- Inspect earlier questions and explanations in any proposed practice sequence. If they supply
  the same case's answer, treat the later item as supported practice or replace it with a fresh
  self-contained instance before claiming an independent check.

Resolve failures in the draft. A statement that these checks passed is not evidence that they did;
retain the concrete counterexample when a defect remains and report the limitation. No separate
self-issued semantic score or PASS artifact is required.
Keep brief observable check results against final item IDs in the existing report/check notes:
what response or operation was tested and what happened. A copy of the answer key or an assertion
of self-assigned `checked` flags is not proof. Do not expose private reasoning. If the contract has
no check field, use the run report without changing its schema. After edits, redo affected checks;
unperformed checks remain unverified instead of inheriting another item's status. Never generate
semantic success by attaching a stock assurance to each hint or by checking only that fields exist.
After a change reconcile boundary, visible task, scoring, hints, intent, Bloom/difficulty and its
justification together; narrowing the intent alone does not update the other claims.

Reconcile the assembled bank with the selected KC boundaries: distinguish targets tested by the
items from knowledge still unmeasured; a cited KC ID does not mean its full scope is assessed.
Check sibling variants and portfolio-wide shortcuts, then
return the contract-valid JSON. Code checks references, shapes and counts; it cannot decide whether
the intended capability is actually elicited. Verify the rendered learner view after import.
Reconcile any exact requested total and distribution against unique final question IDs and their
item-level labels. The runtime's `total_question_budget` checks only an upper limit; it does not
prove an exact total was met. Report shortages/conflicts instead of padding or silently lowering
the request. Hints and rubric criteria do not count as separate questions.
