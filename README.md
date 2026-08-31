# Learning Authoring Pipeline Agent Skill

One portable Agent Skill that turns one or more course PDFs plus optional lecturer context into
deterministic Extraction, shared Knowledge Components, Quiz variants with hints and scoring,
deterministic findings, and a connected local portal.

The active coding agent authors semantic output. The bundled deterministic runtime prepares
sources, freezes task packages, validates contracts, preserves raw candidates, and renders review
surfaces. It does not call a model-provider API.

## Repository layout

```text
skills/
  learning-authoring-pipeline/
    SKILL.md
    agents/openai.yaml
    references/
      session-workflow.md
      review-and-publish.md
      learning-mvp.md
      teacher-student.md
    scripts/
      install_skill.py
      runtime/                 deterministic harness bundled with the skill
```

The repository has one canonical skill package. Runtime code, prompt packages, review assets,
database contracts, and regression tests live under `scripts/runtime/` because they support that
skill; they are not separate root-level products. Generated runs, credentials, caches, local
environments, and deployed output are excluded.

## Install

Requirements: Python 3.12 and `uv`.

```bash
git clone https://github.com/Qyroven/KC-Quiz-Mastery.git
cd KC-Quiz-Mastery
python3 skills/learning-authoring-pipeline/scripts/install_skill.py codex
python3 skills/learning-authoring-pipeline/scripts/install_skill.py claude
```

To replace an older personal installation while keeping a recoverable backup outside skill
discovery:

```bash
python3 skills/learning-authoring-pipeline/scripts/install_skill.py both --replace
```

The installed skill contains its own runtime but omits tests, caches, build output, and local
virtual environments. Invoke `$learning-authoring-pipeline` in a coding agent and attach one or more
PDFs plus any optional notes or context.

## Authoring flow

```text
PDF 1..N
  -> deterministic native-text/geometry Extraction per PDF
  -> one shared Knowledge Component set
  -> evidence-based Quiz slots and variants
  -> hints + answer/rubric
  -> deterministic checks (semantic state remains NOT_REVIEWED)
  -> connected local review portal
```

Optional context joins at KC with separate provenance; it never becomes slide geometry. Counts are
derived from source-supported capabilities and assessment needs rather than fixed page, KC, Bloom,
or question quotas. Candidates stay immutable and review states remain honest.

Quiz stimuli can combine text, tables, formulas and source-bound page images/crops. The local portal
embeds the selected PNGs, so a source citation is not mistaken for a figure the learner can see.
File/contract checks do not establish visual meaning, question quality or learner mastery.

## Develop and verify

```bash
cd skills/learning-authoring-pipeline/scripts/runtime
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run learning-authoring --help
```

Read [`SKILL.md`](skills/learning-authoring-pipeline/SKILL.md) for the operating boundary and its
routed references for detailed workflows.
