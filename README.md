# Learning Authoring Pipeline Agent Skill

Read one or more PDFs, including their informative visuals, into faithful Extraction, shared
Knowledge Components, and Quiz with hints and scoring. Optional lecturer notes/context join at KC
with separate provenance. The active coding agent authors all three stages; tools support its work.

Version **4.3.0** retains multimodal Extraction and its source-grounded `page_note`. It sharpens
Leaf KC boundaries using contrasting learner needs, tests defensible alternative answers and
partial-credit cases, and keeps intent, scoring, hints and level aligned after revisions. Agent-written
helpers must not choose pedagogical defaults or manufacture semantic-check success. The optional
review adapter also supports numeric responses and slot-bound matching parts. Examples guide
decisions; they do not prescribe lesson topics, counts, schemas or response types.

The skill sets learning and delivery criteria, not a fixed tool sequence. It does not prescribe a
model, native-text-only reading, page/KC/question quota, or a candidate-attempt cap. It never calls a
model-provider API. A particular host's tools and file access still determine what it can read;
unreadable content must be reported rather than invented.

## Install

Clone the repository, then copy the skill into the desired agent's discovery directory:

    git clone https://github.com/Qyroven/KC-Quiz-Mastery.git
    cd KC-Quiz-Mastery
    python3 skills/learning-authoring-pipeline/scripts/install_skill.py codex
    python3 skills/learning-authoring-pipeline/scripts/install_skill.py claude

To replace both existing installations:

    python3 skills/learning-authoring-pipeline/scripts/install_skill.py both --replace

Backups stay outside skill discovery. The installer needs Python 3; it does not install or launch
a runtime. For another Agent Skills-compatible host, copy the learning-authoring-pipeline folder
into that host's skill directory. Invocation syntax and available tools vary by host.

Invoke the skill and supply the PDFs plus optional notes and the requested outputs. For example:

> Use $learning-authoring-pipeline to read all supplied documents and produce separate Extraction,
> KC Group/Leaf, and Quiz JSON with hints and answers/rubrics. Preserve sources and revisions,
> disclose gaps, and provide a connected local review portal.

## Package

One canonical skill lives in skills/learning-authoring-pipeline:

- SKILL.md routes the task; references/session-workflow.md contains the authoring criteria and
  worked contrasts, read by the agent before authoring or reviewing.
- references/runtime-helpers.md describes the existing connected review UI and optional commands.
  Requested portals reuse the source-image/Extraction-JSON, KC and Quiz views. Data binding may be
  adapted without dropping authored fields or weakening the task to fit a renderer.
- Publishing and Teacher/Student references apply only to those requested tasks. They are not extra
  steps that every authoring run must perform.

scripts/runtime contains optional existing source/contract/review helpers, their assets and
technical tests. Using them requires Python 3.12 and uv; using the skill's instructions does not.
They prepare raw readings, not semantic Extraction, and preserve authored revisions. Their renderer
contracts are not a universal limit on question types or source content. Do not drop information
to fit a helper. Generated runs, credentials, local environments and caches are excluded.
The authoring entry point is `learning-authoring`; separate product exports use
`learning-authoring-product`. Both are installed by the optional runtime, not required by the skill.

## Verify helpers

    cd skills/learning-authoring-pipeline/scripts/runtime
    uv sync --extra dev
    uv run pytest -q
    uv run ruff check .

These tests check software behavior, not pedagogical quality. Author self-checks, independent review,
human approval, and learner validation are distinct. No claim that the skill beats a baseline
follows from installation, schema validity, provenance, or passing technical tests.
For a with/without-skill comparison, isolate the baseline from this skill and verify actual session
tool logs. Not typing the skill name does not prove it was unused: hosts may select it automatically.
Keep input, model/settings and requested deliverables comparable; do not treat folder names as proof.
