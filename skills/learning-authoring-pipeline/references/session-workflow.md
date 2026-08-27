# Continuous subscription-session workflow

This reference governs local authoring. It never authorizes a provider API call or a deployment.

## 1. Resolve the runtime, run identity, and settings

Choose one CLI launcher using the order in `SKILL.md`, then inspect its capabilities:

```bash
<la> --help
<la> agent-init --help
<la> agent-schema --help
<la> agent-task --help
<la> agent-import --help
<la> portal-build --help
<la> status --help
```

`<la>` is the resolved launcher prefix, not a literal command. Examples include
`learning-authoring`, `.venv/bin/learning-authoring`, or the complete `uvx --from ...
learning-authoring` prefix.

Use an absolute source path and an absolute, source-specific run directory. Inspect the worktree
when operating in a checkout. Existing user changes belong to the user; do not reset or overwrite
them. Run `status` when resuming. If the source hash or run identity conflicts, choose a new run
directory.

Resolve the Quiz run settings once, before producing the Quiz task. User-supplied settings take
precedence. If none are supplied, the quick-demo defaults are:

```text
KC selection: every Leaf KC in source order (`--include-all-kcs`)
language: source (follow the selected KCs' dominant language)
variants per KC: 2
```

An explicit KC subset replaces the all-KC default. Never invent content-specific KC IDs. Repeat the
same selection, language, variants, and KC path in the emitted Quiz import command.

The expected subscription-native command family is:

```text
agent-init <pdf> <run-dir>
agent-schema {extraction,kc,quiz}
agent-task <stage> <run-dir> [stage configuration]
agent-import <stage> <run-dir> <candidate-json> [stage configuration]
portal-build <run-dir> [--output-dir <portal-dir>]
```

Command flags are versioned runtime details. Get them from `--help`; never guess a missing flag or
substitute an API command. `agent-task` must say that it writes a self-contained task package for
the current subscription session. `portal-build` must be available for the default connected
journey. Otherwise stop on version drift.

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

## 4. Produce and import proposed Extraction

Generate the Extraction task package:

```bash
<la> agent-task extraction <run-dir>
```

It is written as `<run-dir>/agent-session/tasks/extraction-<fingerprint>.json`. Inspect that exact
file and use only its declared `instructions`, `input_boundary`, `candidate_contract`, and rendered
page references. The active coding agent creates one JSON candidate at a new path under the run's
`agent-session` workspace.

Extraction rules:

- Cover every source page dynamically; never assume a page count.
- Preserve source-visible semantics, relationships, chart/table evidence, and normalized geometry
  required by the emitted schema.
- Use the PDF as the primary extraction input. Page-image records are locators for targeted visual
  inspection, not an instruction to load every PNG into context. Inspect only a specific page image
  needed for an unresolved visual/geometry question, one page at a time. Do not forward page images
  to KC or Quiz.
- Do not add the code-owned source descriptor to the candidate when the schema excludes it;
  `agent-import` binds source identity deterministically.

Replace `<candidate-json>` in the task package's `next_command.argv` with the new candidate path and
run the resulting command through the resolved `<la>` launcher. This is equivalent to:

```bash
<la> agent-import extraction <run-dir> <candidate-json>
```

The importer must archive the exact candidate bytes as
`<run-dir>/agent-session/candidates/extraction-<sha256>.json`, validate the contract, bind source
identity, write a proposed Extraction, and build its review page. Its import record belongs under
`<run-dir>/agent-session/imports/`. If validation fails, report the structured contract error. Make
at most one fresh retry for this stage; that retry creates a new candidate file and never patches or
overwrites the failed candidate. If the retry also fails, stop and report both archived attempts.

On success, record Extraction as `PROPOSED`. Do not call `approve`, ask for review, or pause. Proceed
immediately to KC through the explicit proposed-demo boundary.

## 5. Produce and import proposed KC through the demo-only boundary

For a new continuous draft journey, generate KC from the proposed Extraction with the conspicuous
runtime opt-in:

```bash
<la> agent-task kc <run-dir> --allow-proposed-extraction-demo
```

This flag changes only the permitted input boundary. It does not create approval. The task package
must identify upstream Extraction as `PROPOSED_DEMO_ONLY`, and its emitted `next_command.argv` must
carry the same flag. Use that exact emitted command after creating one new KC candidate; its shape
is equivalent to:

```bash
<la> agent-import kc <run-dir> <candidate-json> \
  --allow-proposed-extraction-demo
```

If a resumed run already contains a complete, valid human Extraction approval pair, let the runtime
use `HUMAN_APPROVED` instead and follow the task package's emitted command. Never create or infer an
approval record to reach that state.

KC input is the canonical Extraction JSON plus the emitted KC foundation, rulebook, task, and output
schema. Do not supply the PDF, rendered PNGs, old provider responses, or Quiz artifacts. The importer
must archive the exact candidate bytes, validate evidence references against Extraction, and build
the source-first KC review views.

On success, record KC as `PROPOSED` and its upstream status as `PROPOSED_DEMO_ONLY` for the default
journey. The review views are connected artifacts for the final portal, not pause points. Continue
immediately to Quiz.

## 6. Produce and import experimental Quiz

Freeze the resolved run settings and generate the Quiz task. For the unconfigured quick demo:

```bash
<la> agent-task quiz <run-dir> \
  --include-all-kcs \
  --variants-per-kc 2 \
  --language source
```

For a user-selected subset, replace `--include-all-kcs` with one or more repeated
`--include-kc <KC-ID>` values. `--kc <kc-json>` selects a non-default KC candidate when the user
supplies one. Selection, language, and variants are configuration, not content rules.

Quiz input is KC-only: selected Leaf KCs, referenced KC Groups, and the frozen runtime settings. Do
not add Extraction JSON, the PDF, PNGs, old quizzes, validator decisions, or assessment-planner
artifacts unless the repository's current task package explicitly changes the product contract.

The active coding agent creates a new Quiz candidate without editing earlier output. Replace
`<candidate-json>` in the emitted `next_command.argv` and run it through `<la>` exactly as emitted.
It repeats the identical KC path, selection, variants, and language used to prepare the task.

A successful import proves contract compatibility only. Surface-form checks can flag obvious cues;
they do not prove correctness, clarity, fairness, or pedagogical value. Record Quiz as
`EXPERIMENTAL_UNAPPROVED`. Do not pause for browser review.

## 7. Build the connected local portal

After Quiz import succeeds, follow [review-and-publish.md](review-and-publish.md) and invoke the
installed runtime's deterministic builder:

```bash
<la> portal-build <run-dir> --output-dir <fresh-portal-dir>
```

This command must derive the source title, page count, stage statuses, and review entrypoints from
the exact run. Do not reuse a checked-in snapshot or hard-coded demo copy. `portal-build` is part of
the installed CLI, so a personal skill or `uvx` journey does not require a repository checkout.

Building this local directory is part of the default journey. Deploying it is not. If the installed
runtime lacks `portal-build`, report a runtime-version mismatch rather than improvising a portal or
silently substituting stale files.

## 8. Report the completed draft journey

Report:

- source filename/hash and run directory;
- Extraction's actual status (`PROPOSED` for the default new journey), KC `PROPOSED` with its real upstream status, Quiz
  `EXPERIMENTAL_UNAPPROVED`, and Mastery `NOT_IMPLEMENTED`;
- the frozen KC selection, language, and variants per KC;
- candidate archive paths and confirmation that exact bytes were preserved;
- the connected local portal directory, manifest, and entrypoints;
- `execution_mode: agent_subscription_session` and `provider_api_calls: 0`;
- local preparation/import timings when available;
- model tokens/cost as unavailable unless the host session supplies authoritative figures.

Do not describe schema-valid, form-checked, proposed, or reviewed output as pedagogically validated
or production-approved. If Vercel publication was not explicitly requested and authorized, say it
was not performed; this does not make the local draft journey incomplete.
