# Session workflow

`<la>` means one resolved `learning-authoring` launcher. Keep raw stage outputs unchanged.

## 1. Initialize every PDF

Use one fresh subrun per PDF:

```bash
<la> source-preflight <pdf> <source-run>
<la> agent-init <pdf> <source-run> [--context-file <file>] [--context-text <text>]
```

`agent-init` hashes/copies the PDF, renders pages, extracts native text plus character-derived
geometry, writes the proposed Extraction, builds its audit, and creates `extraction-review.html`.
It performs no semantic summary. Layout text preserves indentation and horizontal gaps where
geometry supports them; it is not a code/table interpretation. Visual flags are triage hints, not
proof that other pages contain no meaningful graphics. Do not run `agent-task extraction` in a new
v3 journey; that adapter exists only to
open historical v2 task artifacts.

Context is stored separately. Sparse notes are valid; do not require one note per page.

## 2. Select the KC root

For one PDF, use its source run. For multiple PDFs, place all source subruns inside a fresh bundle
root and freeze their order:

```bash
<la> agent-bundle <bundle-root> <source-run-1> ... <source-run-N> \
  [--context-file <file>] [--context-text <text>]
```

Order establishes identity, not page alignment or semantic priority.

## 3. Author shared KC

```bash
<la> agent-task kc <run-or-bundle> --allow-proposed-extraction-demo
<la> agent-read <task-package.json>
<la> agent-read <task-package.json> --batch <batch-id>
# Read the emitted instructions and each indexed source/context batch; author fresh kc.json.
<la> agent-import kc <run-or-bundle> <kc.json> --task-package <task-package.json>
<la> kc-review <run-or-bundle> --allow-proposed-extraction-demo
```

Within the task, read `inspection_batches` in order. Preserve capabilities and their evidence in
the working inventory before reconciling the complete source set. Read indexed lecturer-context
items separately with `--context-id`. Inspect page images where layout matters; native text alone
cannot confirm a table, diagram or code layout.
Trace every positive claim to evidence cited on that KC. Use `uncovered_content` for meaningful
source material intentionally excluded or unresolved. The demo flag keeps the journey continuous;
it does not create approval.

## 4. Author Quiz, hints, and scoring

```bash
<la> agent-task quiz <run-or-bundle> --include-all-kcs
<la> agent-read <task-package.json>
<la> agent-read <task-package.json> --batch <batch-id>
# --batch can repeat for a coherent case spanning groups. Author fresh quiz.json.
<la> agent-import quiz <run-or-bundle> <quiz.json> --task-package <task-package.json>
<la> quiz-review <run-or-bundle>
```

Use explicit KC selection, language, or total budget only when the user supplies it. With no budget,
assessment slots and variants follow the evidence required by each KC. Process `authoring_batches`
by KC Group. Retain evidence intents across batches and reconcile them against KC boundaries;
question presence is not full capability coverage. Slot and item counts may differ for integrated
constructed-response questions, whose rubric criteria are separately bound to their slots.

The task's `media_assets` lists the selected KCs' cited PDF pages. Inspect `image_ref` relative to
the run root when visual content is needed. A quiz may combine text, table, formula and image blocks
in one stimulus; image blocks reference a catalog `asset_id`, not an invented path. The runtime
checks source hashes and embeds selected PNGs/crops in the portal. Verify that essential context
and labels survive the crop and that the learner can solve the question before opening hints.
Existing simple stimuli remain valid. This adds no planning stage or fixed media/question quota.

Import performs schema, lineage, and deterministic form checks. These checks may recommend a fresh
candidate for mechanical defects; they never assert that a question is correct or pedagogically
valuable. The default semantic state is `NOT_REVIEWED`, not a self-issued PASS.

## 5. Build the connected portal

Single source:

```bash
<la> portal-build <run>
```

Bundle:

```bash
<la> bundle-portal-build <bundle-root> --output-dir <fresh-output>
```

Verify entrypoints and manifest. Do not deploy, register a database, publish a lesson, or create
Teacher/Student apps unless the user explicitly asks.

## Stop conditions

Stop for an unreadable source, invalid frozen task, missing permission, unsafe publication request,
or user cancellation. Semantic uncertainty becomes visible `REVIEW`/`NOT_REVIEWED` output; it is
not a reason to fabricate certainty or abandon the draft flow.
