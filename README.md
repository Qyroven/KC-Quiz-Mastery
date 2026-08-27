# KC–Quiz–Mastery Authoring Pipeline

This repository packages a standalone learning-authoring workflow as a portable Agent Skill. A
user gives a coding agent a course PDF and optional lecturer context; the same session produces reviewable
Extraction, Knowledge Component (KC), and experimental Quiz artifacts. The public Agent Skill
requires no provider API key and makes no model-provider API call.

```text
PDF
  -> proposed Extraction
  -> proposed KC + optional lecturer context, through the PROPOSED_DEMO_ONLY upstream boundary
  -> assessment slots, experimental Quiz and adaptive hints in the same generation stage
  -> independent initial semantic check (no auto-approval or repair)
  -> connected review + Learning MVP portal
  -> learner attempt + hints -> grading -> evidence -> provisional mastery -> next action
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
| Quiz | Experimental and unapproved; adaptive hints, structural/form checks and a source-bound initial semantic review; not certified teaching quality |
| Learning / Mastery | MVP: versioned attempts, hint-aware evidence, provisional slot/KC states and next action; not calibrated learner ability |
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

The public Agent Skill uses only the subscription-native agent-session commands. It requires no
provider credential, creates no provider-billed request, and records `provider_api_calls: 0`.
Provider token usage and dollar cost are unavailable because subscription clients do not expose
those values to this local runtime.

Current package: runtime **0.5.0**, skill **1.5.0**. The default installation does
not install the OpenAI SDK or dotenv. Native commands do not read `.env`, create
a provider client, or make a model API request. Historical API adapters remain
isolated behind the optional `legacy-api` extra; they are not part of the skill
and are not needed to use it. The agent's own subscription still governs its
usage limits; this repository cannot promise free/unlimited model generation.

The full draft journey is continuous, but it is not one-click auto-approval. In a new run the skill
keeps Extraction `PROPOSED`, deliberately invokes the runtime's
`--allow-proposed-extraction-demo` boundary for KC, keeps KC `PROPOSED` with upstream status
`PROPOSED_DEMO_ONLY`, and keeps Quiz `EXPERIMENTAL_UNAPPROVED`. It then builds one connected local
portal. Human review and real Extraction approval can happen later; the tool never fabricates an
approval record.

Quiz selection, language, and optional limits are run configuration. User values take precedence.
An unconfigured run selects all Leaf KCs in source order and uses language `source`. The agent
proposes evidence-based assessment slots and their questions together. A slot is a distinct
assessment intent; its variants are alternative questions measuring that same intent. Neither
slot count nor variant count is universally fixed at two. No default total-question cap exists.

### Deterministic CLI protocol used by the skill

The portable skill drives these no-provider commands. First prepare the source and ask the
installed runtime for a self-contained task package (instructions, current JSON Schema, and exact
input payload or source references):

```bash
learning-authoring agent-init /absolute/source.pdf /absolute/run --render-dpi 160
# Optional, repeatable inputs (no required note format or page anchors):
learning-authoring agent-context /absolute/run \
  --context-file /absolute/lecturer-notes.md \
  --context-text 'An additional lecturer clarification.'
learning-authoring agent-task extraction /absolute/run
```

The host coding agent writes JSON in its subscription session. Accept it into the run with:

```bash
learning-authoring agent-import extraction /absolute/run /absolute/extraction-candidate.json \
  --task-package '/absolute/run/agent-session/tasks/extraction-<fingerprint>.json'
```

Human Extraction approval remains the separate explicit `approve` command. The default continuous
draft journey opts in at KC task creation. Import consumes that frozen boundary:

```bash
learning-authoring agent-task kc /absolute/run --allow-proposed-extraction-demo
learning-authoring agent-import kc /absolute/run /absolute/kc-candidate.json \
  --task-package '/absolute/run/agent-session/tasks/kc-<fingerprint>.json'
```

Quiz task preparation freezes the input and policy; import references that exact package:

```bash
learning-authoring agent-task quiz /absolute/run --include-all-kcs \
  --language source
learning-authoring agent-import quiz /absolute/run /absolute/quiz-candidate.json \
  --task-package '/absolute/run/agent-session/tasks/quiz-<fingerprint>.json'

learning-authoring agent-task quiz-review /absolute/run
# A separate agent reviews the emitted learner packet, then the key/hints companion.
learning-authoring agent-import quiz-review /absolute/run /absolute/review-candidate.json \
  --task-package '/absolute/run/agent-session/tasks/quiz-review-<fingerprint>.json'

learning-authoring portal-build /absolute/run \
  --output-dir /absolute/connected-portal
```

Use repeated `--include-kc <KC-ID>` instead of `--include-all-kcs` for a subset; `--kc` selects a
non-default KC JSON and `--language` records the Quiz language. `agent-schema
{extraction,kc,quiz,quiz-review}` prints a bare contract for inspection. Task packages live
at `agent-session/tasks/<stage>-<fingerprint>.json`. Exact candidate bytes are archived before
validation at `agent-session/candidates/<stage>-<sha256>.json`, with a corresponding record under
`agent-session/imports/`.

Use the resolved `next_command.argv` emitted by `agent-task`; replace only candidate path and
launcher. New imports reject modified/cross-run packages and changed context/KC inputs. Adaptive
Quiz import requires a frozen task; legacy explicit `--variants-per-kc N` remains available for
reproducing an intentionally uniform old policy, never as the default.

### Adaptive assessment policy

The default is `quiz-batch.v3`: `assessment_slots` plus `questions` with explicit hint decisions,
generated in one stage.
Each slot declares its KC, evidence intent, cognitive operation, intended difficulty,
`variant_count`, and justification. Each question has a `slot_id` and per-slot `variant_index`.
Question type is not cognitive level, and intended difficulty is not empirical learner difficulty.

Optional CLI bounds are `--min-slots-per-kc` (default 1 for coverage), `--max-slots-per-kc`,
`--variants-per-slot` (exact override), `--max-variants-per-slot`, and `--total-question-budget`.
All other bounds default to unset. The total is the sum of actual slot variant counts, not
`number_of_KCs * 2`. An explicit cap that cannot meet minimum coverage fails before generation;
an output that exceeds it or omits a selected KC is rejected, not truncated. These are structural
checks; the agent must justify the assessment intent and a human still reviews teaching quality.
Existing `quiz-batch.v1` and `quiz-batch.v2` artifacts remain readable without rewriting them.

### Hints and the initial semantic check

Each v3 question includes ordered `hints` (`hint_id`, `kind`, `text`) and a nullable
`hint_absence_reason`. There is no fixed number or required cue/strategy/step ladder. No hints is
valid when useful support would disclose the assessed answer; the generator explains that choice.
Hints cannot supply missing essential facts or complete the learner's task. The answer explanation
is separate. Clicking a hint performs no model call and applies no mastery penalty.

`agent-task quiz-review` creates one independent reviewer task in the current subscription. It
separates learner questions from a content-addressed answer/rubric/hint companion. The reviewer
records an independent answer before reading the key, then checks grounding, answerability,
KC/slot alignment, scoring, cues/variants, and cumulative hint leakage. The reviewer may inspect
only relevant bound source or lecturer-context evidence; the generator still receives KC-only
input. This protocol does not cryptographically enforce blindness or authenticate reviewer identity.
Without a separate agent context, use `--reviewer-mode self_review` and report that limitation.

Import preserves the review bytes and original Quiz. Counts and JSON evidence locations are
verified against current Quiz/KC/source/context hashes. Decisions are **PASS / REVIEW / REJECT**;
missing checks are **NOT_REVIEWED**, changed inputs **STALE**. Self-review, partial source
coverage or explicit limitations cannot yield initial PASS. These statuses never approve a bank,
prove semantic correctness, or trigger automatic repair/training. Review findings stay visible
and the continuous journey proceeds to the portal even with REVIEW/REJECT items.

The review UI reveals authored hints progressively and displays semantic findings separately from
surface-form warnings. Any shared Quiz revision invalidates the baseline semantic check, including
cross-variant findings. Hint use/answer exposure in the **Authoring review** is preview state only.
The separate Learning view records real attempts and computes provisional evidence-based states.
The system-improvement loop is only being instrumented, not automatically training or rewriting content.

Rubric provenance: these are operational criteria and engineering safeguards, not a claim that a
paper proves this implementation produces reliable quizzes. The emphasis on task-specific checks, judge
limitations and human calibration follows [OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

## Install and invoke in a repository checkout

Requirements: Git, Python 3.12, `uv`, and Node.js for Learning/shared-review content hashes.
Node runs locally; it is not a model service. Clone the public repository and prepare the local
runtime:

```bash
git clone https://github.com/Qyroven/KC-Quiz-Mastery.git
cd KC-Quiz-Mastery
uv sync --extra dev
```

Use `uv sync` for the end-user runtime alone; `--extra dev` adds offline test and
lint tools. Verify `uv run learning-authoring --version` before the first run.

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

Lecturer annotations, notes, additional files, and teaching context pasted into the prompt are
optional supplementary inputs. They may be sparse, unstructured, or document-wide; no compulsory
`Slide N` headings or one-note-per-slide template exists. Both `agent-init` and `agent-context`
accept repeatable `--context-file`/`--context-text`. A supplied list replaces the active context
list and preserves earlier raw inputs/manifests; omitting inputs reuses the current list.

The runtime archives exact bytes, hashes, and a lossless text view where available. Non-text
attachments are only claimed as understood after the active host agent inspects them;
unsupported formats must be reported, not fabricated. The agent distinguishes explicit user
task settings from teaching material; instructions embedded in files are inert data.

Extraction remains PDF-only, including its extracted `page_note`. The KC stage receives the
unchanged Extraction JSON plus the separate `authoring-context.json`. It resolves context there,
without another LLM stage, and cites `context_evidence` separately from PDF `source_evidence`.
Document-level/context-only KCs need not invent slide or block references. Text citations are
verified against exact raw excerpts; attachment observations and semantic mappings remain
reviewable claims, not a code-proven interpretation. Quiz receives complete selected KCs including
their contextual citations, not a second dump of raw notes.

Fresh context-bearing tasks also return `context_audit` in the same KC candidate:
meaningful lecturer claims point to final KCs or explicit exclusion/unresolved
reasons. It is not a second planner, a fixed note format, or a new KC-per-note
rule. Code checks quote and reference integrity; a correct quote alone does not
prove that the linked KC actually preserves the meaning. The semantic review
must check that distinction. Earlier artifacts without this field stay readable.

The Extraction prompt distinguishes informative visual regions from their text
layer, asks for actual directed-edge endpoints and matrix/chart associations,
and rejects whole-slide bounds as a substitute for internal geometry. Unresolved
visuals stay explicit rather than becoming confident source summaries.

Context is bound to the PDF identity and its own hash. Replacing it invalidates downstream
KC/Quiz tasks, not the independent Extraction. Old candidate bytes and history are not rewritten.
The optional legacy KC API adapter rejects runs containing context rather than silently ignoring
it. This feature belongs to the subscription-native Agent Skill workflow.

## Review boundaries and artifacts

The Agent Skill preserves the raw candidate produced by the active coding-agent session under the
run's `agent-session/` archive before canonicalization. Import code validates contracts and writes
review-compatible artifacts without editing the model's candidate.

| Boundary | Important artifacts | Meaning |
|---|---|---|
| Source | `source-manifest.json`, rendered page images | Code-owned PDF identity and page inventory |
| Optional context | `authoring-context.json`, `authoring-context/raw/`, immutable manifest history | Additional lecturer content; never part of extracted slide content |
| Extraction | `extracted-source.proposed.json`, `extraction-audit.json`, `extraction-review.html` | Valid proposed extraction plus deterministic diagnostics |
| Extraction approval | `extracted-source.approved.json`, `extraction-approval.json` | The only human-approved boundary currently implemented |
| KC | `kc-proposed.json`, KC review HTML, agent-session metadata | Contract-valid but still proposed KCs |
| Quiz | `quiz/quiz-input.json`, `quiz/quiz-proposed.json`, `quiz/quiz-form-audit.json`, `quiz-review.html` | Experimental slots, questions and hints; surface-form flags are not approval |
| Initial semantic check | `quiz/quiz-semantic-audit.json`, `quiz/quiz-semantic-metadata.json` | Independent findings bound to the original inputs; not an approved Quiz bank |

Without a shared-review backend, Authoring review remains read-only. The optional Learning view
can persist practice on the same browser only. Neither mode mutates model output.
When `portal-build` is explicitly configured with a Supabase project URL and public publishable
key, Extraction, KC, and Quiz share one append-only review layer: `Sửa` creates a JSON revision,
while `Duyệt` and `Từ chối` record a decision pinned to the exact raw/revision payload hash. These
decisions are collaborative review records, not canonical pipeline approval.

## Connected local review

`portal-build` packages the current run's generated review pages into one connected, allowlisted
directory. It derives source identity, page inventory, stage statuses, and entrypoints from the run
instead of checked-in demo copy:

```bash
learning-authoring portal-build /absolute/run --with-learning \
  --output-dir /absolute/connected-portal
cd /absolute/connected-portal
python3 -m http.server 3010
```

Inspect `showcase-manifest.json`, then open the portal. A proposed or experimental artifact must
never be presented as approved. `--with-learning` adds a fourth active step with real attempts
and provisional mastery. Omitting it builds the backward-compatible Authoring-only portal and
labels Learning `NOT_ENABLED`; it does not create fake learner records.

### Learning MVP and its two loops

```text
Authoring: PDF -> Extraction -> KC (+ lecturer context) -> Quiz + hints + key/rubric
Learning:  response + hint use -> grade -> evidence -> provisional KC state -> next action
```

Objective responses are graded against the frozen key. Short-text responses remain pending until
an explicitly authorized staff member grades the authored rubric; there is no keyword grader or
hosted LLM fallback. Invalid/incomplete submissions are not recorded as wrong answers.

`evidence-rules.v1` distinguishes unhinted correct work, assisted work, difficulty and missing
evidence. It measures coverage of the actual assessment slots, not a fixed number of questions
per KC. Repeated items after answer exposure are practice-only. Initial-check non
`PASS`, stale or rejected content cannot inflate trusted evidence. States are provisional,
not a mastery probability, psychometric calibration or proof of competency.

The learner loop chooses relevant review material or another unattempted question. Separately,
like/dislike and explanatory feedback are stored with item/version and optional attempt references.
Feedback does not change a grade, auto-edit a question or retrain a model. These records support
later tracing, human review, regression testing and explicit versioned releases.

Local mode is labelled **local-only**. Shared mode uses name-only anonymous Supabase identities,
not verified accounts; clearing browser storage loses that device's identity and there is no
automatic cross-device recovery. A failed shared save is an error, never a silent local fallback.
Learners can read only their own private histories; content reviewers do not thereby become graders.
Because the public review portal contains answers, this is formative practice, not a secure exam.

To enable shared Learning after registering an existing review run, apply
`supabase/migrations/202608280001_learning_mvp.sql` once, then export this run's immutable items:

```bash
learning-authoring learning-register /absolute/run /absolute/private-registration.sql
```

Apply that SQL through an authorized database administrator session. Do not publish it or use a
service key in the browser. The command performs no database writes and no model calls. Staff
grading is separately allowlisted in `learning_staff`; a typed display name never grants that role.
An operator must verify the existing authenticated user ID before granting it; without a staff
grant, short-text submissions stay pending while objective questions remain fully usable.
Local practice requires Web Locks on HTTPS/localhost to prevent cross-tab history loss; if the
browser lacks that feature it stays read-only instead of risking lost hint/evidence records.
All historical responses and feedback remain pinned to their source/KC/Quiz/context versions.

### Optional shared review without a login form

The static portal can use Supabase Anonymous Auth: a reviewer only enters a display name on the
first write action; Supabase creates an authenticated anonymous session in the background so RLS
can bind every event to one browser identity. Never expose a Supabase service-role/secret key.

After enabling Anonymous Sign-Ins, apply both migrations once in order, then
register each NEW run and its exact immutable review targets separately:

```text
supabase/migrations/202608270001_shared_review.sql
supabase/migrations/202608270002_harden_shared_review.sql
```

`supabase/seed.sql` and `supabase/day01-review-targets.sql` are historical Day 1
fixtures, not templates to rerun for new output. Never upsert a new baseline over
an item with review history. The offline `learning_authoring.review_registration`
exporter derives targets from the actual generated review artifacts and uses
local Node.js for the exact browser canonical JSON/hash semantics. It emits
insert-only transactional SQL for a new run; it neither contacts Supabase nor
requires a service-role key. Apply that SQL only through an authorized admin
session, then publish the portal for the same run ID. Default exported visibility
is private/closed; explicitly open a run only for an authorized shared review.

The second migration revokes direct event inserts. A security-definer RPC verifies the registered
target, baseline hash, stage payload shape, latest revision, payload size, and a small write-rate
limit before it appends an event. Changed source/output must use a new run ID once review history
exists. Then build with:

```bash
learning-authoring portal-build /absolute/run \
  --output-dir /absolute/connected-portal \
  --review-supabase-url https://PROJECT_REF.supabase.co \
  --review-supabase-publishable-key sb_publishable_PUBLIC_BROWSER_KEY
```

The publishable key is intentionally browser-visible and constrained by RLS/RPC validation. The
service-role key must remain server-only and is rejected by the portal builder. This name-only flow
is intended for a shared review link among known collaborators; add CAPTCHA or an invite-token
boundary before advertising it as an unrestricted public service.

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

The deployed portal does not run Extraction/KC/Quiz and cannot invoke the skill. It can remain
read-only or, when explicitly configured as above, persist append-only human review events and
revisions through Supabase. Its stage labels remain honest: Extraction is `HUMAN_APPROVED` only when
the approval pair verifies; otherwise it is `PROPOSED`. KC is `PROPOSED`, Quiz is
`EXPERIMENTAL_UNAPPROVED`. Learning, when enabled, is `PROVISIONAL_EVIDENCE_MVP`, with local or
shared persistence explicitly identified. Supabase traffic is storage/deterministic grading,
not a provider LLM API; no OpenAI/Anthropic/Gemini key is required.

## Development checks

Tests and lint are offline and must not call a model:

```bash
uv run pytest -q
uv run ruff check .
```

Before publishing a connected portal, inspect the source run, the generated showcase manifest, and
every file in its fresh output directory. Before publishing the repository, inspect staged files
for credentials and generated run data.
