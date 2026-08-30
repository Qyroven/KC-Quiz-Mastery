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
It performs no semantic summary. Pages without usable native text are marked for targeted visual
inspection. Do not run `agent-task extraction` in a new v3 journey; that adapter exists only to
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
# Read the emitted task package; author one fresh kc.json without editing it afterward.
<la> agent-import kc <run-or-bundle> <kc.json> --task-package <task-package.json>
<la> kc-review <run-or-bundle> --allow-proposed-extraction-demo
```

Within the task, process `inspection_batches` in order. Build a capability inventory for each,
inspect only necessary page images, then reconcile inventories across the complete source set.
Trace every positive claim to evidence cited on that KC. Use `uncovered_content` for meaningful
source material intentionally excluded or unresolved. The demo flag keeps the journey continuous;
it does not create approval.

## 4. Author Quiz, hints, and scoring

```bash
<la> agent-task quiz <run-or-bundle> --include-all-kcs
# Read the emitted task package; author one fresh quiz.json without editing it afterward.
<la> agent-import quiz <run-or-bundle> <quiz.json> --task-package <task-package.json>
<la> quiz-review <run-or-bundle>
```

Use explicit KC selection, language, or total budget only when the user supplies it. With no budget,
assessment slots and variants follow the evidence required by each KC. Process `authoring_batches`
by KC Group and maintain one ledger for evidence intent, interaction, key position, misconception,
variant justification, and hint progression.

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
