# Optional runtime helpers

The review layout below is the default for a requested local portal. The executable helpers are
optional: use them when their contracts fit the authored data. They are not a prerequisite to read,
author or revise a lesson. Do not discard content to satisfy them; adapt the view instead.

## Reuse the existing review layout

Keep the connected Extraction → KC → Quiz navigation. Do not replace the review with summary cards
or redesign it on each run unless the user asks. The maintained views are:

- [Extraction](../scripts/runtime/learning_authoring/review.py): page list, zoomable source image,
  and the actual page JSON side by side, including detailed blocks, page note and uncertainty.
- [KC](../scripts/runtime/learning_authoring/kc_review.py): source-linked Recall/Scroll views with
  Group/Leaf content and scope, not an editor for Extraction masquerading as KC editing.
- [Quiz](../scripts/runtime/learning_authoring/quiz_review.py): question list, learner/reviewer modes,
  item-specific cognitive level/difficulty, actual stimuli, hints, answers and scoring.
- [Connected shell](../scripts/runtime/learning_authoring/showcase_assets/index.html): navigation
  across those views. Its product/publishing backend is not required for a read-only local review.

Reuse these layouts/assets directly where compatible. For another schema or interaction, change
the data binding or necessary response widget while retaining the review layout and original JSON.
Do not rename an authored figure into a source screenshot or force a numeric/visual task into prose
to fit a contract. A local view is not a lesson rewrite, an approval action or a new product deployment.
Check that displayed/copied JSON equals the delivered record; a model's schema defaults are not raw
authored fields. Keep source-qualified navigation for multiple PDFs and separately attributed context.

## Optional commands

Resolve learning-authoring from an existing matching installation or with
uv run --project <skill>/scripts/runtime learning-authoring (Python 3.12 and uv).
Below, <la> denotes that launcher. No provider credentials are needed.
The separate product launcher is `uv run --project <skill>/scripts/runtime learning-authoring-product`;
use it only for requested product exports or Teacher/Student work, not ordinary authoring.

## Source assets and agent-authored Extraction

    <la> agent-init <pdf> <source-run>
    <la> agent-schema extraction
    <la> agent-import extraction <source-run> <agent-authored-extraction.json>

agent-init copies/hashes the PDF, renders pages, and saves native-source.raw.json as an optional
machine reading. It does **not** write a proposed Extraction or claim the visuals were understood.
The agent reads the source and supplies Extraction. Helpers never author that semantic content.
The existing `page_note` object carries the page's meaning in `summary`/`explanation`, supported
by `evidence_block_ids`; reading problems belong in `uncertainties`. Keep the detailed blocks too.
Schema validation can check those references, not whether the explanation captures the slide.

To retain the exact context used before authoring, optionally create agent-task <stage> <run>
and pass its path as --task-package on import. Without it, import records a delivery-time binding
to current inputs, not evidence that the agent read a generated prompt. agent-read can display
that package or reading subsets; its batches are suggestions, not mandatory working units.
`agent-schema` prints a serialization contract only; it does not load worked examples or perform
authoring/review. The worked contrasts in session-workflow.md apply whether or not helpers are used.

## KC and Quiz

For multiple PDFs, import Extraction for each source, then use
<la> agent-bundle <bundle-root> <source-run-1> ... <source-run-N>. Retain source identities.
Attach optional context with agent-context <run> --context-file <file> or --context-text <text>;
see --help for multiple files. Context remains separate from Extraction.

Use agent-schema kc (or --source-bundle) and agent-schema quiz for the optional renderer's contracts.
For newly authored items, `questions[].assessment` records `cognitive_operation`, `intended_difficulty`
and `rationale` for that final unhinted item. Its labels override the slot's planning labels in the
authoring review; variants need not share difficulty. Old records without this object retain their
slot-level labels, explicitly identified as such, and no fields are written into their raw JSON.
The three difficulty levels are easy, medium and hard; unknown records an unsupported estimate.
Explain it in the rationale. No label is empirical calibration. Put an optional practice sequence's
roles/order and relationships in existing report notes; this helper does not run a learner scheduler.
`--total-question-budget` remains an upper limit. An exact requested total or distribution also
requires a final count check by the agent; a successful import alone does not establish compliance.

The adapter supports `numeric_input` with an explicit `correct_answer.numeric` object: `value`,
nonnegative `absolute_tolerance`, and `unit` (empty for dimensionless answers). Its other answer
fields and rubric stay empty. State any required precision in the question; do not invent a tolerance
or demand a derivation when the target is the numerical result. The preview shows the unit without
the key until the reviewer opens the answer.

An integrated item can bind separately scored parts to multiple assessment slots: `short_text`
uses `rubric[].slot_id`; `matching` uses `correct_answer.mappings[].slot_id`. Every linked slot needs
its own part and source evidence. A single scalar or selected answer cannot be credited to several
independent capabilities. Numeric and integrated items are supported for authoring/review; the
existing Learning export refuses them until its scorer supports their evidence semantics. Do not
rewrite such items merely to bypass that export boundary.

Import the agent-authored candidates with:

    <la> agent-import kc <run> <kc.json> --allow-proposed-extraction-demo
    <la> agent-import quiz <run> <quiz.json> --include-all-kcs

The demo flag keeps drafts distinct from human approval. Use explicit KC selection or quantity
flags only when requested. This adapter's image catalog supports source pages and crops, not
arbitrary newly authored images. Do not relabel an authored diagram as a source page to import it.
When a task needs a redrawn/new figure or an unsupported interaction, keep the authored content
and provide an appropriate adapter/view with the actual asset. Do not use an answer-revealing
source screenshot, weaken the question, or drop the asset just to fit this helper. Supported
interaction types and media formats are renderer capabilities, not limits on agent authoring.

Imports preserve raw candidate bytes and earlier stage outputs. Valid revisions are not blocked
by an earlier valid schema or a fixed candidate count. A supplied stale task still fails source
integrity checks: rebuild its binding after revising upstream material. Check revision-state.json
for downstream outputs needing recheck. When a source Extraction changes, re-run agent-bundle;
re-supply context inputs if that bundle had notes, then recheck KC/Quiz against the new binding.
Unchanged notes are not silently reassigned to a different bundle. Form/geometry findings remain
warnings, not semantic approval.

## Review delivery

Use portal-build <run> for a single source or
bundle-portal-build <bundle-root> --output-dir <output> for a bundle. A stale dependency must be
reconciled before rebuilding a current portal. Verify navigation, visible question data, media and
hint/answer separation. Do not publish anything without the user's request.
