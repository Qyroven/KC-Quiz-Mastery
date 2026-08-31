# Task

Create one complete proposed KC set from the frozen Extraction and optional lecturer context.

Use `agent-read` for the frozen source/context index and each inspection batch, rather than loading
the full source JSON into context. Process one batch at a time, then reconcile its inventories into shared KC Groups
and Leaf KCs. Use the worked examples only for the split/merge and provenance patterns; all names,
counts, IDs, page references, and lesson facts must come from the frozen input.

Inspect the rendered page when layout affects a code block, table or visual relationship. Native
text and triage flags are not proof that visual meaning was inspected. Record unresolved ambiguity
instead of completing a familiar pattern from prior knowledge.

Copy `input_boundary.expected_source_ref` exactly into `source_ref`. Preserve all source and context
identifiers. Produce the exact candidate schema as JSON with no fences or surrounding prose.
