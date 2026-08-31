# Authoring outcomes

Use the active agent's reading, visual reasoning, coding, and editing capabilities. The stages
below describe what must survive into the deliverables, not a fixed execution protocol. Work in
sections when useful; section size and tool choice follow source complexity and host capacity.

## Extraction: preserve the source before organizing learning

Read every page of every supplied document. Inspect visual representations at sufficient
resolution to read meaningful labels and relationships, even on pages with abundant native text.
Use PDF reading, text extraction, rendering, zooming, OCR, or scripts as appropriate. A thumbnail
may help navigation but does not prove its fine details were read.

Retain the source's language, terms, numbers, units, signs, conditions, code indentation, and
meaningful structure: table cell associations, diagram direction/endpoints, chart axes/legends,
formula structure, grouping and continuation. Preserve visible content instead of replacing it
with a lesson summary. Keep a source image or region reference where a text representation alone
would lose information. Do not invent precision, hidden values, or spatial relationships.

Keep raw files and machine readings separate from agent-authored Extraction. Identify each source
and its own page numbers. Each page must be accounted for, including blank or non-teaching pages;
each unresolved region must say what could not be read. A page list is navigation/accountability,
not proof of semantic completeness. Compare the extracted content back to the actual source.

Alongside the detailed content, give each page an agent-authored `page_note`: what this page
communicates and how its text, visuals, conditions, or examples connect. Ground it in the extracted
content and source page, not prior knowledge. A list of objects on the page or a statement that
it was inspected is not this explanation. Keep reading problems and uncertainty distinct from
the meaning recovered. For a title, blank, or non-teaching page, state that role without inventing
a lesson. No fixed length or number of takeaways is needed. An existing user contract may name
this field differently, but the semantic explanation must remain identifiable.

The page note is neither a substitute for detailed blocks nor a lecturer annotation. Preserve
the details needed to verify it; do not hide source content only inside the note. A complete list
of pages or notes must not be reported as proof that no information was lost.

If the source appears wrong, preserve its statement and record the issue separately. Do not
replace it with background knowledge. External checking, if authorized, stays separately cited.
Sparse or unstructured notes are accepted as additional context at KC, not injected into PDF
blocks. Infer note-to-source mapping only when supported; otherwise retain unmapped context.

## Shared KC: organize independently learnable capabilities

A Leaf KC is the smallest coherent capability with its own observable learner evidence and
remediation. Split independent capabilities, not every noun, number, or sentence. Merge paraphrases
and inseparable parts; repeated subject matter alone does not justify merging. A Group organizes
related Leaf KCs and is not itself a separate mastery claim.

Use both detailed Extraction and page notes, with source inspection wherever needed; do not build
KCs from summaries alone. Check boundaries by asking whether a learner could demonstrate one part
but fail another and need different feedback or practice. If so, preserve those separately
diagnosable capabilities. More leaves are not automatically better: necessary steps of one task
and examples supporting one principle need not become separate KCs.

For each Leaf KC retain its name, concrete knowledge description, observable claim, included and
excluded scope, group, and source-qualified evidence. Distinguish pedagogical design (objectives,
misconceptions, assessment intent) from claims actually made by the sources. Every content claim
needs supporting evidence, including material conditions and exceptions.

Across PDFs, keep the distinct source identities, contexts, and disagreements. Another source may
support a shared KC but does not repair an unreadable figure in the first source. Account for
meaningful source and lecturer-context content as represented, excluded with a reason, or unresolved.
If KC work exposes an Extraction omission, return to that source and revise Extraction; do not hide
the missing source meaning only inside a KC. Do not compress the inventory to fit a schema or count.

## Quiz: elicit the target work

Choose the evidence target first, then an appropriate response and useful variants. An assessment
slot describes that target; it need not be produced by a separate planning step. No default total
question budget, per-KC multiplier, Bloom ladder, or interaction mix is required. Respect quantities
only when the user supplies them, and disclose any resulting unmeasured targets.

Provide a clear task, all necessary givens, the answer or rubric, an explanation, and helpful hints
when possible. Include the actual figure/table/code/formula when the task depends on it. A source
reference is not learner-visible content. Invented scenarios must supply their assumptions and must
not masquerade as source observations. State what the learner must decide, produce, or explain in
ordinary language. Supply conditions that determine the answer, or accept an appropriately
conditional answer. Keep the inference being tested out of the givens.

Choose stimuli for the assessment target, not for convenience of extraction. A source image may
already contain the answer. Reuse it when appropriate; otherwise adapt/redraw its presentation or
create a clearly identified hypothetical figure, table, code sample, or dataset. An adaptation
keeps source facts and relationships faithful; a new scenario states its own assumptions and is
not claimed as an observation from the slide. Do not invent missing source content to redraw it.
Retain needed labels, units, arrows and conditions; leave only the intended unknown for the learner.
There is no required tool or asset format. Deliver the actual stimulus and its provenance, and
verify that it renders legibly in the learner view. A filename or citation alone is insufficient.

Judge leakage against the target: giving a formula can support applying it, but cannot test its
recall; asking learners to copy a labeled relationship does not demonstrate independently inferring
it. Check titles, captions and accessible descriptions too, while preserving equivalent necessary
information for learners using them. Do not fix leakage by removing facts needed to solve the task.

Selected responses need plausible alternatives without length, tone, position or wording giveaways.
Constructed responses need criteria that accept valid equivalent answers and reject consequential
errors, not mandatory keywords or extra deliverables absent from the question. Integrated tasks
may cover related targets only when their scored evidence is separable. Do not force calculation,
visual identification, or programming into prose just because a renderer supports prose.

For each scored criterion, make clear which observable work earns credit and which consequential
error does not. Where partial credit is useful, describe the actual incomplete work that earns it;
generic "partly correct" or "right direction" labels alone cannot guide scoring. Binary criteria
are valid when no meaningful partial evidence exists. Keep the rubric proportional to the task.

Bloom and estimated difficulty describe work left in the **unhinted** question, not its format.
Difficulty is not calibrated without learner data. Hints are optional: each should enable a next
step while leaving target work to do, including when earlier hints have already been seen. Required
facts belong in the question; completed solutions belong in the answer explanation.

Before delivery, actually try the questions from the learner-visible material without hints or
source-only context. This is an author self-check, not a claim of blinded independence. Recompute
arithmetic, test code safely where applicable, try competing answers and different valid solutions,
and check rubric/hint alignment. Also try bypassing the target work by copying supplied material
or using option cues. Record concrete remaining defects, not a self-awarded PASS. Compare the bank
with the actual KC capabilities: a referenced KC ID does not mean its whole scope was tested.
Distinguish scored targets from unmeasured knowledge; check that variants add useful evidence
rather than cosmetic rewording. Do not add another agent or mandatory judge stage for these checks.

## Revisions and delivery

Drafts can be edited. Once delivered/imported, preserve their bytes and make a new revision with
the reason for change. Track which source/KC version each downstream output uses; changed upstream
material makes affected outputs need rechecking, not silent reuse. Correcting a schema error is
not the only reason a revision is legitimate. Stop revising when checks are satisfied or when no
further supported progress can be made; disclose unresolved defects instead of looping or guessing.

Deliver separate JSON for Extraction, KC, and Quiz plus a concise run report. Use an existing user
contract when provided; otherwise choose a clear structure retaining the content above. Logical
links and source identity are required; the optional runtime's serialization is not universal.
Keep originals and actual dependencies, not an additional report for every procedural step.

If requested, provide a connected local portal: source → Extraction → KC → Quiz. Verify its actual
learner view, media, hint/answer separation, and navigation. An unsupported interaction must remain
explicit or get a suitable view, not be dropped or converted to a weaker task. Report technical
checks separately from author self-checks, human approval, and empirical learner validation.
