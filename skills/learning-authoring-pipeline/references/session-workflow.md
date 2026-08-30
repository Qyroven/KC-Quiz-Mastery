# Session workflow

This is the operational path for the subscription-native skill. `<la>` means the single
`learning-authoring` launcher resolved at the start.

## 1. Select inputs

Accept one or more PDFs, zero or more lecturer-context files, and explicit user constraints such as
language or question budget. Context may be sparse or unstructured. Do not require one note per
slide and do not infer cross-PDF page mappings.

For every PDF, use a fresh run directory:

```bash
<la> source-preflight <pdf> <source-run>
<la> agent-init <pdf> <source-run>
```

`agent-init` may copy/hash the PDF, extract local text, and render review images. It does not call a
model. Add single-source context only when this is genuinely a one-PDF journey:

```bash
<la> agent-context <source-run> [--context-file <file>] [--context-text <text>]
```

## 2. Extract each PDF

For each source run:

```bash
<la> agent-task extraction <source-run>
# Host agent reads the package and writes a fresh candidate JSON.
<la> agent-import extraction <source-run> <candidate.json> --task-package <task-package.json>
<la> review <source-run>
```

Before writing the candidate, work through contiguous source sections (or manageable adjacent page
windows when no section break exists) and keep a per-page coverage ledger. Reconcile those ledgers
into one final candidate, then visually anchor every returned block to its own PDF page. Normalized
bounds may be approximate, but must identify the actual content region; do not reuse a generic page
box. Inspect an isolated rendered page only when the native PDF view cannot resolve an informative
visual or relationship. Compare repeated block counts and geometry against the real layouts, and
scan text for invalid codepoints before import. Keep candidate bytes unchanged. If contract or
promotion-gate import fails, author one fresh replacement from the same frozen task. Do not patch
the first candidate. A second failure stays `REVIEW` and does not become canonical proposed output.

## 3. Create the KC source boundary

For one PDF, continue with its source run. For multiple PDFs, create one fresh bundle after all
Extraction imports exist:

```bash
<la> agent-bundle <bundle-root> <source-run-1> ... <source-run-N> \
  [--context-file <file>] [--context-text <text>]
```

Source order defines bundle identity only. It does not align page numbers or note ordinals.

## 4. Author shared KC

```bash
<la> agent-task kc <run-or-bundle> --allow-proposed-extraction-demo
# Host agent authors KC JSON from Extraction + separately bound context.
<la> agent-import kc <run-or-bundle> <kc.json> --task-package <task-package.json>
<la> kc-review <run-or-bundle> --allow-proposed-extraction-demo
```

KC must preserve source-qualified evidence, meaningful exclusions, and context provenance. The demo
flag allows an uninterrupted draft flow; it is not approval. Before grouping, inventory the
source-supported capabilities internally per source section, then reconcile the section inventories
globally. Split candidates when knowledge, observable response, or
remediation can stand independently; merge only paraphrases, supporting examples, or inseparable
parts. Re-run those tests after every merge. Before import, trace every positive statement in each
KC description, observable claim, and included boundary to evidence cited on that KC; add the real
reference, narrow the statement, or omit it. Do not use a page ratio, KC target, source-specific
keyword list, or uncited course summary.

After import, inspect `granularity_diagnostics` in `kc-run-metrics.json`. It surfaces exact review
signals such as learning pages without KC links, repeated uncovered reasons, and repeated evidence
support wording. These signals never auto-create KCs and never replace human semantic review.

## 5. Author Quiz, hints, and scoring contract

```bash
<la> agent-task quiz <run-or-bundle> --include-all-kcs
# Host agent authors slots, questions, hints, answers/rubrics.
<la> agent-import quiz <run-or-bundle> <quiz.json> --task-package <task-package.json>
```

Use explicit `--include-kc`, language, or budget flags only when the user asks. Let assessment needs
determine item type and variant count. Author in natural KC-group/source-order batches while carrying
one portfolio ledger for evidence intents, interactions, key positions, misconception families, and
hints. Assemble one final candidate. After import, inspect the deterministic form audit. Contract or
promotion-gate failure permits exactly one fresh replacement; a second failure stays `REVIEW` and is
not written as `quiz-proposed.json`.

Before importing the first candidate, solve every learner-visible item without its key. First map
each slot to the simplest interaction that preserves the complete evidence: classifications and
relationship sets can use matching, sequences can use ordering, and bounded decisions can use
selected response; reserve short text for evidence that truly requires learner-authored reasoning
or construction. If one interaction dominates the batch, re-check every use without forcing a
quota. If material
facts leave more than one defensible conclusion, supply the missing bounded facts or ask for a
conditional conclusion and score it accordingly. Make a criterion-to-prompt check for every rubric
point by locating the exact learner-visible phrase that requests it, remove answer-bearing labels or checklists from the learner view, and prevent one-to-one
matching from claiming independent evidence for a final pair obtained only by elimination. Re-check
uniform variant counts against item-specific evidence needs; do not add variants merely to break a
uniform pattern.

## 6. Initial check

```bash
<la> agent-task quiz-review <run-or-bundle> --reviewer-mode self_review
# Host agent writes one semantic-review candidate.
<la> agent-import quiz-review <run-or-bundle> <review.json> --task-package <task-package.json>
<la> quiz-review <run-or-bundle>
```

Use `independent` only when a genuinely separate context is available. Otherwise declare
`self_review`. Preserve PASS/REVIEW/REJECT honestly. Do not silently rewrite Quiz output.

## 7. Build the local portal

Single source:

```bash
<la> portal-build <run> [--with-learning]
```

Bundle:

```bash
<la> bundle-portal-build <bundle-root> --output-dir <fresh-output>
```

Verify the manifest and entrypoints. Deployment, shared database persistence, Teacher/Student apps,
and learner activity are separate, explicitly authorized operations.

## Stop conditions

Stop only for an unreadable source, invalid frozen task, exhausted one-retry contract failure,
missing filesystem permission, unsafe publication request, or user cancellation. Review-needed
content is an honest output, not a reason to abandon the end-to-end draft.
