# Task

Read the frozen input index, then the relevant KC-group batches with `agent-read`. Read adjacent
batches together when a case integrates their capabilities; a batch is a reading aid, not a quota.
Keep concise evidence intents across batches so reconciliation does not collapse distinct targets
or repeat the same question. Use worked examples for the decision they illustrate, not their topic,
counts, IDs, option positions or vocabulary.

For each target, draft the learner task and a defensible answer together. Decide the response type
from the work required, then record the slot and useful variants. Assign Bloom and difficulty after
checking what the learner must actually do. Do not fill a prescribed ladder or interaction mix.

Before serializing a candidate, try concrete answers against each question:

- Solve from the learner view alone, without source-only context, hints or the key. Recompute
  quantitative givens and verify the final keyed IDs after options are serialized.
- Try a valid answer unlike the exemplar and a plausible near miss. Apply each rubric criterion:
  the former must receive its deserved credit and the latter must lose credit for its actual defect.
- Try to answer without the target knowledge using length, tone, position, repeated wording or
  elimination. If that succeeds, change the item, not merely its explanation.
- Follow hints in order and check that they help without completing the target work.

Resolve failures in the draft. A statement that these checks passed is not evidence that they did;
retain the concrete counterexample when a defect remains and report the limitation. No separate
self-issued semantic score or PASS artifact is required.

Reconcile the assembled bank with the selected KC boundaries: distinguish targets tested by the
items from knowledge still unmeasured. Check sibling variants and portfolio-wide shortcuts, then
return the contract-valid JSON. Code checks references, shapes and counts; it cannot decide whether
the intended capability is actually elicited. Verify the rendered learner view after import.
