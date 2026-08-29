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

Inspect isolated source pages when the task has unresolved visuals. Keep candidate bytes unchanged.
If import fails its contract, author one fresh replacement. Do not patch the first candidate.

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
source-supported capabilities internally. Split candidates when knowledge, observable response, or
remediation can stand independently; merge only paraphrases, supporting examples, or inseparable
parts. Re-run those tests after every merge. Do not use a page ratio, KC target, or source-specific
keyword list.

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
determine item type and variant count. After import, inspect the deterministic form audit. A contract
failure permits one fresh replacement. A quality warning stays visible for the initial check; do
not loop until all warnings disappear.

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
