# Optional runtime helpers

Use these only when their existing JSON contracts and review surfaces fit the requested delivery.
They are not required to read, author, or revise a lesson. Do not discard content to satisfy them;
preserve the authored output and use an appropriate adapter/view instead.

Resolve learning-authoring from an existing matching installation or with
uv run --project <skill>/scripts/runtime learning-authoring (Python 3.12 and uv).
Below, <la> denotes that launcher. No provider credentials are needed.

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

## KC and Quiz

For multiple PDFs, import Extraction for each source, then use
<la> agent-bundle <bundle-root> <source-run-1> ... <source-run-N>. Retain source identities.
Attach optional context with agent-context <run> --context-file <file> or --context-text <text>;
see --help for multiple files. Context remains separate from Extraction.

Use agent-schema kc (or --source-bundle) and agent-schema quiz for the optional renderer's
contracts. Import the agent-authored candidates with:

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
