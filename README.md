# KC–Quiz–Mastery

Portable, subscription-native learning authoring from course sources to reviewable learning
artifacts. The active coding agent authors semantic output; local code prepares sources, freezes
task packages, validates contracts, preserves raw candidates, and builds review surfaces.

No provider API key is required or used by the Agent Skill.

## What the core skill does

```text
PDF 1..N
  -> one independent Extraction per PDF
  -> one shared Knowledge Component set
  -> Quiz variants + hints + answer/rubric
  -> one initial quality check
  -> connected local review portal
```

Optional lecturer notes or other context join at KC, never at Extraction. Multiple PDFs stay
source-qualified; page numbers from different files are never merged by coincidence.

The continuous draft flow does not invent approval:

- Extraction: `PROPOSED`
- KC: `PROPOSED` over an explicit demo-only upstream boundary
- Quiz: `EXPERIMENTAL_UNAPPROVED`
- Initial check: `PASS`, `REVIEW`, or `REJECT`; not human certification

## Repository layout

```text
learning_authoring/
  agent_session.py       frozen task/import protocol
  contracts.py           Extraction contracts
  kc*.py                 KC contracts, prompts, review
  quiz*.py               Quiz contracts, prompts, checks, review
  source*.py             PDF and multi-source boundaries
  product/               optional portal, Learning, Teacher/Student packaging
  legacy_api/            historical adapters; excluded from the public runtime path
  cli.py                  subscription-native authoring CLI
  product_cli.py          optional product/export CLI
skills/
  learning-authoring-pipeline/   one canonical portable skill
tests/                           deterministic contracts and UI safety checks
```

Generated runs, portals, build products, caches, and local credentials do not belong in the
repository.

## Install

Requirements: Python 3.12 and `uv`. Node.js is needed only for exact browser-hash/product export
checks.

```bash
git clone https://github.com/Qyroven/KC-Quiz-Mastery.git
cd KC-Quiz-Mastery
uv sync --extra dev
uv run learning-authoring --help
```

The public runtime has no OpenAI SDK, dotenv, or provider credential dependency.

Install the skill for personal discovery:

```bash
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py codex
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py claude
uv run python skills/learning-authoring-pipeline/scripts/install_skill.py both --replace
```

The installer copies the one canonical package from `skills/learning-authoring-pipeline/` into the
selected client's personal skill directory. The repository intentionally contains no duplicate
Codex/Claude discovery symlinks.

## Run the core protocol manually

Normally invoke `$learning-authoring-pipeline`. These commands document the deterministic boundary
used by the skill.

### 1. Prepare and Extract each PDF

```bash
learning-authoring source-preflight /absolute/source.pdf /absolute/source-run
learning-authoring agent-init /absolute/source.pdf /absolute/source-run
learning-authoring agent-task extraction /absolute/source-run
# The active coding agent writes extraction-candidate.json from the task package.
learning-authoring agent-import extraction /absolute/source-run \
  /absolute/extraction-candidate.json --task-package /absolute/extraction-task.json
learning-authoring review /absolute/source-run
```

Repeat independently for every PDF.

### 2. Bind optional context and multiple sources

One source:

```bash
learning-authoring agent-context /absolute/source-run \
  --context-file /absolute/lecturer-notes.md \
  --context-text 'Optional lecturer clarification.'
```

Multiple sources:

```bash
learning-authoring agent-bundle /absolute/bundle-root \
  /absolute/source-run-1 /absolute/source-run-2 \
  --context-file /absolute/lecturer-notes.md
```

The number of PDFs and context files is not fixed.

### 3. KC

```bash
learning-authoring agent-task kc /absolute/authoring-root \
  --allow-proposed-extraction-demo
learning-authoring agent-import kc /absolute/authoring-root /absolute/kc.json \
  --task-package /absolute/kc-task.json
learning-authoring kc-review /absolute/authoring-root \
  --allow-proposed-extraction-demo
```

### 4. Quiz, hints, and initial check

```bash
learning-authoring agent-task quiz /absolute/authoring-root --include-all-kcs
learning-authoring agent-import quiz /absolute/authoring-root /absolute/quiz.json \
  --task-package /absolute/quiz-task.json

learning-authoring agent-task quiz-review /absolute/authoring-root \
  --reviewer-mode self_review
learning-authoring agent-import quiz-review /absolute/authoring-root /absolute/review.json \
  --task-package /absolute/review-task.json
learning-authoring quiz-review /absolute/authoring-root
```

Default selection is all Leaf KCs. Assessment needs determine slot and variant counts; there is no
universal two-questions-per-KC rule, forced Bloom ladder, or default total cap. Explicit user
budgets must not silently omit KCs.

### 5. Connected local portal

```bash
# One source
learning-authoring portal-build /absolute/run --with-learning

# Multiple sources
learning-authoring bundle-portal-build /absolute/bundle-root \
  --output-dir /absolute/fresh-portal
```

These commands build local static output. They do not deploy it.

## Optional product layer

Teacher/Student packaging, shared persistence, database registration, learner evidence, and
provisional mastery are downstream product features—not part of semantic generation.

They use a separate executable:

```bash
learning-authoring-product build-role-apps /absolute/run /absolute/apps --local-preview
learning-authoring-product export-authoring-registration /absolute/run /absolute/authoring.sql
learning-authoring-product export-learning-registration /absolute/run /absolute/learning.sql
```

Building files does not grant a teacher role, publish a lesson, create learner activity, or write
to a database. Shared product setup requires explicit authorization and backend enforcement.

## Quality and safety boundaries

- A valid schema is not proof of semantic correctness or teaching quality.
- Generated candidates are immutable. A failed candidate is archived; it is never hand-patched.
- The skill allows at most one fresh replacement after a contract failure.
- Initial review is one check, not an A/B laboratory or repeated multi-agent voting system.
- Hints support the learner without supplying the answer.
- Short-text mastery evidence requires rubric grading by authorized staff.
- Learner evidence and system-quality feedback are separate loops.
- Deployment is always an explicit action over an allowlisted generated directory.

## Verify

```bash
uv run pytest -q
uv run ruff check .
git diff --check
```

For full operating details, read the canonical
[skill](skills/learning-authoring-pipeline/SKILL.md) and its routed references.
