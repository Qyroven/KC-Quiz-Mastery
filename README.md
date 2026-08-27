# KC–Quiz–Mastery Authoring Pipeline

This repository packages a standalone learning-authoring workflow as a portable Agent Skill. A
user gives a coding agent a course PDF; the same subscribed agent session produces reviewable
Extraction, Knowledge Component (KC), and experimental Quiz artifacts. The skill does **not** call
a model-provider API.

```text
PDF
  -> proposed Extraction
  -> proposed KC through the explicit PROPOSED_DEMO_ONLY upstream boundary
  -> experimental unapproved Quiz
  -> connected local review portal
```

The default Agent Skill invocation completes that draft journey without pausing at human review
gates. It does not auto-approve anything: review surfaces remain available after the full connected
result exists. VLearn may consume approved exports later, but it does not run this pipeline. Vercel
may host only an explicitly published allowlisted static result; the Agent Skill itself is not
deployed there.

## Honest stage status

| Stage | Current status |
|---|---|
| Extractor | Implemented: source binding, structured candidate import, deterministic audit, review UI, explicit human approval |
| KC | Implemented as contract-valid proposed output with local review; there is no KC approval command yet |
| Quiz | Experimental and unapproved; structure and surface-form checks exist, but instructional quality is not solved |
| Mastery | **Not implemented** |
| VLearn importer | Not implemented |
| Connected local portal | Implemented as an allowlisted build from one run; Vercel deployment is a separate, explicit action |

A valid schema is not proof of semantic correctness, fairness, clarity, or instructional value.
Every generated artifact keeps the review-required status shown above, but those statuses do not
pause the default continuous draft journey.

## Primary workflow: use the Agent Skill

The coding agent that the user is already subscribed to is the model runtime. Local Python code
only prepares source/task packages, validates the agent's JSON, preserves the exact candidate
bytes, binds code-owned inputs, and builds review pages.

```text
attached PDF or PDF path
  -> deterministic local source preparation
  -> task package + JSON Schema
  -> current Codex/Claude subscription session writes candidate JSON
  -> deterministic contract validation and immutable candidate archive
  -> next stage through an explicit, honestly labelled upstream boundary
  -> connected local portal after Quiz
```

The skill never invokes the provider-backed `doctor`, `extract`, `kc-generate`, or
`quiz-generate` commands. Agent-session metrics therefore record `provider_api_calls: 0`; provider
token usage and dollar cost are unavailable because subscription clients do not expose those
values to this local runtime.

The full draft journey is continuous, but it is not one-click auto-approval. In a new run the skill
keeps Extraction `PROPOSED`, deliberately invokes the runtime's
`--allow-proposed-extraction-demo` boundary for KC, keeps KC `PROPOSED` with upstream status
`PROPOSED_DEMO_ONLY`, and keeps Quiz `EXPERIMENTAL_UNAPPROVED`. It then builds one connected local
portal. Human review and real Extraction approval can happen later; the tool never fabricates an
approval record.

Quiz selection, language, and variants are run configuration. User values take precedence. An
unconfigured quick demo selects all Leaf KCs in source order, uses language `source` (the selected
KCs' dominant language), and generates 2 variants per KC.

### Deterministic CLI protocol used by the skill

The portable skill drives these no-provider commands. First prepare the source and ask the
installed runtime for a self-contained task package (instructions, current JSON Schema, and exact
input payload or source references):

```bash
learning-authoring agent-init /absolute/source.pdf /absolute/run --render-dpi 160
learning-authoring agent-task extraction /absolute/run
```

The host coding agent writes JSON in its subscription session. Accept it into the run with:

```bash
learning-authoring agent-import extraction /absolute/run /absolute/extraction-candidate.json
```

Human Extraction approval remains the separate explicit `approve` command. The default continuous
draft journey uses the same conspicuous demo-only opt-in at KC task creation and import:

```bash
learning-authoring agent-task kc /absolute/run --allow-proposed-extraction-demo
learning-authoring agent-import kc /absolute/run /absolute/kc-candidate.json \
  --allow-proposed-extraction-demo
```

Quiz task preparation and import must repeat the same runtime selection and variant depth:

```bash
learning-authoring agent-task quiz /absolute/run --include-all-kcs \
  --variants-per-kc 2 --language source
learning-authoring agent-import quiz /absolute/run /absolute/quiz-candidate.json \
  --include-all-kcs --variants-per-kc 2 --language source

learning-authoring portal-build /absolute/run \
  --output-dir /absolute/connected-portal
```

Use repeated `--include-kc <KC-ID>` instead of `--include-all-kcs` for a subset; `--kc` selects a
non-default KC JSON and `--language` records the Quiz language. `agent-schema
{extraction,kc,quiz}` prints a bare contract when a task package is not needed. Task packages live
at `agent-session/tasks/<stage>-<fingerprint>.json`. Exact candidate bytes are archived before
validation at `agent-session/candidates/<stage>-<sha256>.json`, with a corresponding record under
`agent-session/imports/`.

## Install and invoke in a repository checkout

Requirements: Git, Python 3.12, and `uv`. Clone the public repository and prepare the local
runtime:

```bash
git clone https://github.com/Qyroven/KC-Quiz-Mastery.git
cd KC-Quiz-Mastery
uv sync --extra dev
```

The repository contains the canonical skill at
`skills/learning-authoring-pipeline/` and repo-local discovery entries for both supported clients.

### Codex

Open Codex in the repository, attach the PDF (or give its path), then either:

- open `/skills` and choose **Learning Authoring Pipeline**; or
- mention `$learning-authoring-pipeline` in the request.

Example:

```text
$learning-authoring-pipeline process the attached course PDF in one continuous draft journey and build the connected local portal without pausing for review.
```

### Claude Code

Open Claude Code in the repository, attach or reference the PDF, then invoke:

```text
/learning-authoring-pipeline process this course continuously through Quiz and build the connected local portal
```

The invocation spelling is client-specific even though both clients use the same skill package.

## Install the skill for personal discovery

Repo-local discovery is enough when working inside this checkout. To make the skill appear from
other projects on the same machine, use the installer:

```bash
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py codex
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py claude
# or install both
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py both
# upgrade an existing personal installation
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py both --replace
```

The installer copies the package into `~/.agents/skills/` for Codex and/or `~/.claude/skills/` for
Claude Code. It refuses to overwrite an existing installation unless `--replace` is supplied. The
checked-in skill remains the canonical source. When a local checkout is available, the skill uses
that runtime. A personal installation can bootstrap the published CLI from this repository with:

```bash
uvx --from git+https://github.com/Qyroven/KC-Quiz-Mastery.git learning-authoring --help
```

That command installs/runs deterministic pipeline code; it does not make a provider-model API
request. On another machine, clone the repository (recommended for auditability) or install the
skill there after the repository is published.

## Input scope

PDF is the canonical MVP input. For `.pptx` or another slide format, normalize it to PDF first so
source identity, page count, rendering, review, and evidence references all use one stable
boundary. If LibreOffice is available, a typical conversion is:

```bash
soffice --headless --convert-to pdf --outdir ./converted ./course.pptx
```

Conversion is an input-preparation step, not native PPTX support. Always inspect the converted PDF
before authoring.

## Review boundaries and artifacts

The Agent Skill preserves the raw candidate produced by the active coding-agent session under the
run's `agent-session/` archive before canonicalization. Import code validates contracts and writes
review-compatible artifacts without editing the model's candidate.

| Boundary | Important artifacts | Meaning |
|---|---|---|
| Source | `source-manifest.json`, rendered page images | Code-owned PDF identity and page inventory |
| Extraction | `extracted-source.proposed.json`, `extraction-audit.json`, `extraction-review.html` | Valid proposed extraction plus deterministic diagnostics |
| Extraction approval | `extracted-source.approved.json`, `extraction-approval.json` | The only human-approved boundary currently implemented |
| KC | `kc-proposed.json`, KC review HTML, agent-session metadata | Contract-valid but still proposed KCs |
| Quiz | `quiz/quiz-input.json`, `quiz/quiz-proposed.json`, `quiz/quiz-form-audit.json`, `quiz-review.html` | Experimental Quiz output and surface-form flags; not an approved Quiz bank |

Local Quiz Approve/Edit/Reject controls are review notes stored by the browser. They do not mutate
the model output or create an approved Quiz artifact.

## Optional legacy provider-API mode

The repository retains older OpenAI-compatible provider commands for regression and comparison.
They are **not** used by the Agent Skill and are not required for the subscription-session path.
Only configure this mode when intentionally making provider-billed requests.

```bash
cp .env.example .env
# add credentials locally; never commit .env

uv run learning-authoring --env-file .env doctor
uv run learning-authoring --env-file .env extract /path/to/source.pdf /path/to/run
uv run learning-authoring --env-file .env kc-generate /path/to/run
uv run learning-authoring --env-file .env quiz-generate /path/to/run \
  --include-all-kcs --variants-per-kc 2
```

Provider API responses, checkpoints, prompt packages, request previews, tokens, and reported cost
belong to this optional legacy path. Their presence in historical ignored run directories does not
mean the Agent Skill made an API call.

## Connected local review

`portal-build` packages the current run's generated review pages into one connected, allowlisted
directory. It derives source identity, page inventory, stage statuses, and entrypoints from the run
instead of checked-in demo copy:

```bash
learning-authoring portal-build /absolute/run \
  --output-dir /absolute/connected-portal
cd /absolute/connected-portal
python3 -m http.server 3010
```

Inspect `showcase-manifest.json`, then open the portal. A proposed or experimental artifact must
never be presented as approved. Mastery remains an explicit `NOT_IMPLEMENTED` boundary, not a
working component.

## Vercel is an optional static result layer

The default skill invocation builds the connected local portal but does not deploy it. Publish only
after the user explicitly requests Vercel and authorizes the exact account/team, project, and
environment. Deploy the fresh output directory, never the repository root or run directory.

Repository developers can invoke the same builder through `uv`:

```bash
uv run learning-authoring portal-build /absolute/run \
  --output-dir /absolute/connected-portal
```

Only the generated connected portal directory is intended for static hosting. The builder copies
allowlisted review HTML, the exact page images declared by the run, and a showcase manifest. It
does not copy:

- the Agent Skill or Python runtime;
- `.env` files or credentials;
- source PDFs;
- raw provider envelopes or agent candidates;
- request previews or prompt packages;
- unrelated run directories.

The deployed portal is a read-only result for PM review. It does not run Extraction/KC/Quiz and it
cannot invoke the skill. Its stage labels remain honest: Extraction is `HUMAN_APPROVED` only when
the approval pair verifies; otherwise it is `PROPOSED`. KC is `PROPOSED`, Quiz is
`EXPERIMENTAL_UNAPPROVED`, and Mastery is `NOT_IMPLEMENTED`.

## Development checks

Tests and lint are offline and must not call a model:

```bash
uv run pytest -q
uv run ruff check .
```

Before publishing a connected portal, inspect the source run, the generated showcase manifest, and
every file in its fresh output directory. Before publishing the repository, inspect staged files
for credentials and generated run data.
