# Authoring outcomes

The active agent reads, authors and revises. Choose tools and working units for source complexity
and host capacity; the following outcomes do not impose a tool sequence, model or runtime.
The fictional worked contrasts are decision examples, not lesson templates, gold benchmarks,
required fields, counts or question-type distributions. Apply their principles to the actual input.

## Extraction: preserve the source before organizing learning

Read every page of every supplied PDF, including informative visuals at readable resolution.
Native text, OCR and thumbnails help inspection; none proves that visual meaning was understood.
Retain wording, language, terms, numbers, units, signs, qualifications and code whitespace, plus
table associations, diagram directions/endpoints, chart axes/legends and cross-page continuation.
Keep the source image/region reference when text alone would lose information. Do not guess hidden
values, arrows or precision. State exactly what remains unreadable, including on otherwise clear pages.

Keep raw sources and machine readings separate from agent-authored Extraction. Each source keeps
its own identity and page numbering. Account for blank/non-teaching pages without inventing lessons.
Compare each page's detailed blocks back to the source, not just its page number or text overlap.

Alongside detailed blocks, provide an agent-authored `page_note` explaining what the page communicates
and how its meaningful components connect. Ground it in the source; a layout inventory or "inspected"
flag is not an explanation. Keep uncertainty separate. A note never compensates for omitted blocks.
An existing user contract may name these fields differently; preserve their meaning, not these names.

Check structured blocks against the transcription and page note. A qualification retained in text
must not vanish from a formula/table that downstream work uses. Preserve apparent source errors and
record concerns separately. Derivations, corrections and new examples are authored additions, not
source transcription, even when correct. Authorized external checks need separate attribution.

Lecturer notes/context may be sparse, unstructured or spread across files and the user's prompt.
Keep them separately attributed at KC; they are not PDF blocks or agent-authored page notes.
Infer mapping only when supported; retain ambiguous or unmapped context rather than forcing a page.

### Worked contrast: preserve conditions and relationships

Fictional source A, page 3 says: "q = V/t, measured with the valve fully open. Repeat the
measurement if readings fluctuate." Its diagram is Tank → Meter → Outlet. A table's headers
V (L), t (min) are readable, but the time in one row is not.

Wrong: retain only `q = V/t`, guess the time, or claim a newly computed rate was printed on the slide.
An image attachment does not fix these claims. A suitable excerpt is:

```json
{
  "source": {"id": "A", "page": 3},
  "formula": {"expression": "q = V/t", "condition": "valve fully open"},
  "diagram": {"edges": [["Tank", "Meter"], ["Meter", "Outlet"]]},
  "page_note": "Volume and duration determine the measured flow rate. The meter is before the outlet; unstable measurements must be repeated.",
  "uncertainty": "The time in the second table row is unreadable; no rate is calculated for that row."
}
```

This excerpt supplements, not replaces, the source wording, table and image. Check the condition,
units and edge directions against the page. Another PDF or note cannot make the unreadable cell
a recovered fact from this source. Any new example belongs in separately marked authored material.

## Shared KC: organize independently learnable capabilities

A Leaf KC is the smallest coherent capability with its own observable learner evidence and
remediation. A Group organizes related leaves; it is not a separate mastery claim. Split independent
capabilities, not every noun or step. Merge paraphrases and inseparable parts, not merely shared topics.

Use detailed Extraction with page notes, revisiting source visuals as needed. For each leaf retain
its name, concrete knowledge, observable claim, included/excluded scope, group and supporting source
references. Cite actual supporting blocks, not nearby text. Separate pedagogical design (objectives,
misconceptions, assessment intent) from source claims. Conditions and exceptions survive grouping.

Keep the description and observable scope consistent. Material named in a description but outside
its claim is supporting context or an unmeasured capability; say which. Do not hide lost capabilities
under a broad heading. Check whether a learner could succeed at one part, fail another, and need
different practice. Preserve that diagnosable distinction without fragmenting inseparable work.

Across PDFs, merge only equivalent capabilities with source-qualified evidence. Preserve differences,
conflicts and assumptions; one source does not repair an unreadable region in another. Account for
meaningful source and lecturer-context capabilities as represented, specifically excluded or unresolved.
If KC work discovers missing source meaning, revise Extraction and recheck downstream dependencies;
do not hide the omission only in KC. Counts follow content, not pages, batches or an output-size target.

### Worked contrast: one topic does not mean one capability

Suppose supplied materials teach calculating a weighted mean and judging whether a sample supports
a population claim. A learner can calculate correctly but ignore a biased sample. The errors need
different feedback, so preserve the two capabilities rather than "Understand averages" with only a
calculation question. In contrast, multiplying by weights, summing and dividing can be inseparable
steps of the calculation; they do not automatically need separate leaves.

Another instructor's equivalent calculation example supports the same KC with a separate citation.
Different sampling assumptions stay qualified. If only calculation is asked, report "calculation
sampled; population inference unmeasured". Merely linking both KC IDs does not measure both.

## Quiz: elicit the target work

Choose the evidence target, then a task/response exposing it. An assessment slot names that bounded
target; it needs no separate planning call. Multiple targets may belong to a KC. Variants are useful
alternate instances of the same target, not new capabilities or cosmetic rewrites. Do not derive
coverage solely by iterating over KC IDs after writing questions. Compare actual scored work with
the KC capabilities and disclose what remains unmeasured.

No default question budget, per-KC multiplier, Bloom ladder or type mix applies. Respect quantities
the user supplies and explain resulting coverage tradeoffs. Do not suppress useful calculation,
visual, selection or programming tasks because a renderer makes prose easier.

Supply a clear task, all necessary givens, an answer/key or rubric, explanation and useful hints.
Provide the actual table, formula, figure or code needed; a source citation or hint cannot substitute.
Specify decisive assumptions or accept conditional answers. Source reproductions retain meaning;
adaptations and hypothetical stimuli are identified as such and must not invent missing source facts.
Retain required labels, units, arrows and conditions; verify legibility in the actual learner view.

Check leakage relative to the target, including titles, captions and accessible descriptions.
A supplied formula may support application but cannot test its recall. A source screenshot may
already contain the solution; adapt/redraw or author appropriate stimuli without removing needed
givens. Selected options need plausible near misses without length, tone, position or wording cues.
Matching/ordering must not expose the intended solution through presentation unless reading that
representation is itself the honestly stated target.

Rubrics score what the visible task requests and what its correctness requires, not extra deliverables
or preferred keywords. Accept equivalent solutions; reject consequential errors. Define concrete
partial credit where useful; binary criteria are fine for indivisible evidence. Integrated tasks
may measure related targets only if their scored evidence is separable; do not copy one score to all KCs.

Assign Bloom and estimated difficulty to each final **unhinted** item, not through constructor defaults.
Equal labels are fine when warranted; forced variety is not. If unsupported, mark the estimate unknown.
Difficulty is uncalibrated without learner data. Hints are optional: every cumulative prefix should
help while leaving target work. Necessary facts belong in the stem; completed solutions in the answer.

### Worked contrast: visible task, scoring and hint must agree

Fictional target: decide which jobs may start from dependencies and completion state. The learner
sees an authored diagram `A → C`, `B → C`, `C → D`, legend "all incoming dependencies must be
complete", and state "A complete; B, C and D not started". Ask: "Which job can start now? Explain."

- Answer: B. C still needs B; D still needs C. "Only B; C is waiting on B" is a valid concise
  alternative. Do not demand code or an optimization algorithm absent from the task.
- Near miss: "C, because A finished" ignores another prerequisite. It must lose credit for
  dependency reasoning. Actually apply the criterion to this response; do not just label it checked.
- Hint: "Check every incoming dependency against the completed set" supports a method. "Choose B"
  supplies the answer. The diagram and state belong in the initial task, not only in a hint/citation.
- Shortcut: copying A,B,C,D does not answer this state-dependent question. Asking instead to recall
  a workflow order while showing its complete ordered list would bypass that different target.
- Scope: this tests readiness, not failure recovery or resource optimization. Renaming the jobs
  cannot establish those other capabilities. Verify actual arrowheads/labels when rendered.

The check result is the valid response, rejected near miss and stated limitation, not a self-issued
PASS. This remains an author check, not independent learner validation.

## Check the final work and deliver

Try the final questions from learner-visible material without hints or source-only context.
Recompute arithmetic; test code safely where appropriate. Try a different valid response, a plausible
near miss, and a shortcut that avoids the target work. Check key/rubric alignment and cumulative hints.
When an error is found, revise the item and recheck affected content rather than merely its explanation.

Keep brief observable check results or counterexamples against final item IDs in existing check fields
or the run report: what was tested, what happened and the remaining limit. Do not expose private
reasoning, duplicate the answer key as proof, or assert self-assigned `checked`/`self_contained` flags
as semantic tests. Unperformed checks remain unverified. Schema, page counts and ID coverage do not
upgrade semantic confidence. No extra agent, mandatory judge, new report per step or self-issued score
is needed. Match confidence to actual checks, not the size of the generated bank.

Drafts may be edited. Preserve previously delivered/imported versions, with reasons and dependencies
for revisions. Checks apply to the version inspected: changed source, KC, stem, key, hint or media
requires affected checks again. Stop when resolved or when no supported progress remains; disclose
specific unresolved defects instead of looping, guessing or silently dropping content.

Deliver separate stage JSON and a concise report with coverage, actual checks and remaining gaps.
Follow a supplied user contract; otherwise choose a clear structure preserving these outcomes, not
necessarily the runtime schema. Keep originals and actual dependencies, not redundant artifacts.
For a requested portal, follow [runtime-helpers.md](runtime-helpers.md): reuse the existing review
layout, keeping source and actual Extraction JSON together, connected to KC and Quiz. Adapt the view
without rewriting authored content. Verify navigation, media and hint/answer separation. Technical
checks, author checks, independent review, human approval and learner validation are distinct.
