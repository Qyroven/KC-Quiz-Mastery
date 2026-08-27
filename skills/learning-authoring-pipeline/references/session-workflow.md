# Subscription-session workflow

This reference governs local authoring. It never authorizes a provider API call.

## 1. Resolve the runtime and run identity

Choose a CLI launcher using the order in `SKILL.md`, then inspect its capabilities:

```bash
<la> --help
<la> agent-init --help
<la> agent-schema --help
<la> agent-task --help
<la> agent-import --help
<la> status --help
```

`<la>` is the resolved launcher prefix, not a literal command. Examples include
`learning-authoring`, `.venv/bin/learning-authoring`, or the complete `uvx --from ...
learning-authoring` prefix.

Use an absolute source path and an absolute, source-specific run directory. Inspect the worktree
when operating in a checkout. Existing user changes belong to the user; do not reset or overwrite
them. Run `status` when resuming. If the source hash or run identity conflicts, choose a new run
directory.

The expected subscription-native command family is:

```text
agent-init <pdf> <run-dir>
agent-schema {extraction,kc,quiz}
agent-task <stage> <run-dir> [stage configuration]
agent-import <stage> <run-dir> <candidate-json> [stage configuration]
```

Command flags are versioned runtime details. Get them from `--help`; never guess a missing flag or
substitute an API command. `agent-task` must say that it writes a self-contained task package for
the current subscription session. Otherwise stop on version drift.

## 2. Normalize a slide deck when needed

PDF is the canonical input. For a PPTX, make a new PDF beside the run workspace without replacing
the original. Prefer a local `soffice --headless --convert-to pdf` or equivalent deterministic
converter. Verify the result exists, is readable, and has at least one page before `agent-init`.
If no converter is available, ask the user for a PDF export.

## 3. Prepare the source

```bash
<la> agent-init <source.pdf> <run-dir>
<la> status <run-dir>
```

This stage may copy/hash the PDF, extract local text, and render review images. It must report
`execution_mode: agent_subscription_session`, `provider_api_calls: 0`, and
`generation_performed: false`. If it reports provider activity, stop.

## 4. Produce and import extraction

Generate the extraction task package:

```bash
<la> agent-task extraction <run-dir>
```

It is written as `<run-dir>/agent-session/tasks/extraction-<fingerprint>.json`. Inspect that exact
file and use only its declared `instructions`, `input_boundary`, `candidate_contract`, and rendered
page references. The active coding agent then creates one JSON candidate at a new path under the
run's `agent-session` workspace.

Important extraction rules:

- Cover every source page dynamically; never assume a page count.
- Preserve source-visible semantics, relationships, chart/table evidence, and normalized geometry
  required by the emitted schema.
- Use the PDF as the primary extraction input. Page-image records are locators for targeted visual
  inspection, not an instruction to load every PNG into context. Inspect only the specific page
  image needed for an unresolved visual/geometry question, one page at a time. Do not forward page
  images to KC or Quiz.
- Do not add the code-owned source descriptor to the candidate when the schema excludes it;
  `agent-import` binds source identity deterministically.

Replace `<candidate-json>` in the task package's `next_command.argv` with the new candidate path
and run the resulting command through the resolved `<la>` launcher. This is equivalent to:

```bash
<la> agent-import extraction <run-dir> <candidate-json>
```

The importer must archive the exact candidate bytes as
`<run-dir>/agent-session/candidates/extraction-<sha256>.json`, validate the contract, bind source
identity, write a proposed extraction, and build the review page. Its import record belongs under
`<run-dir>/agent-session/imports/`. If validation fails, report the structured errors and generate
a new candidate; never patch the old file.

Stop at the extraction review gate. Continue only after the user reviews the page and explicitly
authorizes approval. Approval uses the runtime's `approve` command and a real reviewer name. Use an
acknowledgement flag only when the reviewer explicitly accepts remaining warnings.

An explicit demo-only route exists for KC review before extraction approval. Use it only when the
user asks to demonstrate before approval, pass `--allow-proposed-extraction-demo` to `agent-task
kc`, the emitted `agent-import kc`, and `kc-review`, and keep every downstream label as
unreviewed/proposed-derived. Never create an approval artifact for this route.

## 5. Produce and import Knowledge Components

After approved extraction, generate:

```bash
<la> agent-task kc <run-dir>
```

KC input is the canonical extraction JSON plus the emitted KC foundation, rulebook, task, and
output schema. Do not supply the PDF, rendered PNGs, old provider responses, or Quiz artifacts.

The active coding agent writes one new KC candidate. Replace `<candidate-json>` in the emitted
`next_command.argv` and run it through `<la>`; equivalently:

```bash
<la> agent-import kc <run-dir> <candidate-json>
```

The importer must archive the exact bytes, validate evidence references against extraction, and
build source-first KC review pages. KC is still `PROPOSED` after successful import.

Stop for review. Ask the user which KC candidate is accepted for the experiment, which Leaf KC IDs
to include (or explicit all-KC selection), the desired variants per KC, and language. Do not infer
these choices from a past run.

## 6. Produce and import experimental Quiz

Generate `agent-task quiz` with exactly the reviewed KC candidate and user-selected runtime
configuration. Choose one selection form:

```bash
<la> agent-task quiz <run-dir> \
  --kc <kc-json> \
  --include-kc <KC-ID> [--include-kc <KC-ID> ...] \
  --variants-per-kc <n> \
  --language <language>

<la> agent-task quiz <run-dir> \
  --kc <kc-json> \
  --include-all-kcs \
  --variants-per-kc <n> \
  --language <language>
```

Omit `--kc` only when intentionally using the run's canonical `kc-proposed.json`. Quiz input is
KC-only: selected Leaf KCs, referenced KC Groups, and runtime config. Do not add extraction JSON,
the PDF, PNGs, old quizzes, validator decisions, or assessment-planner artifacts unless the
repository's current task package explicitly changes the product contract.

The active coding agent creates a new Quiz candidate without editing earlier output. Replace
`<candidate-json>` in the emitted `next_command.argv` and run that exact configuration through
`<la>`. It invokes `agent-import quiz` with the identical KC selection, variants-per-KC, and
language values used to prepare the task. A successful import proves contract compatibility only.
Surface-form checks can flag obvious cues; they do not prove correctness, clarity, fairness, or
pedagogical value.

Build/open the canonical Quiz review and stop. Status is `EXPERIMENTAL_UNAPPROVED`; browser review
notes do not mutate candidate JSON or create approval.

## 7. Report the run

Report:

- source filename/hash and run directory;
- stage statuses and review entrypoints;
- candidate archive paths and whether exact bytes were preserved;
- `execution_mode: agent_subscription_session` and `provider_api_calls: 0`;
- local preparation/import timings when available;
- model tokens/cost as unavailable unless the host session supplies authoritative figures;
- Quiz limitations and Mastery as `NOT_IMPLEMENTED`.

Do not describe schema-valid, form-checked, proposed, or reviewed output as pedagogically validated
or production-approved.
