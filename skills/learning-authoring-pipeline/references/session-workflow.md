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

If the source appears wrong, preserve its statement and record the issue separately. Do not
replace it with background knowledge. External checking, if authorized, stays separately cited.
Sparse or unstructured notes are accepted as additional context at KC, not injected into PDF
blocks. Infer note-to-source mapping only when supported; otherwise retain unmapped context.

## Shared KC: organize independently learnable capabilities

A Leaf KC is the smallest coherent capability with its own observable learner evidence and
remediation. Split independent capabilities, not every noun, number, or sentence. Merge paraphrases
and inseparable parts; repeated subject matter alone does not justify merging. A Group organizes
related Leaf KCs and is not itself a separate mastery claim.

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
not masquerade as source observations. Keep the inference being tested out of the givens.

Selected responses need plausible alternatives without length, tone, position or wording giveaways.
Constructed responses need criteria that accept valid equivalent answers and reject consequential
errors, not mandatory keywords or extra deliverables absent from the question. Integrated tasks
may cover related targets only when their scored evidence is separable. Do not force calculation,
visual identification, or programming into prose just because a renderer supports prose.

Bloom and estimated difficulty describe work left in the **unhinted** question, not its format.
Difficulty is not calibrated without learner data. Hints are optional: each should enable a next
step while leaving target work to do, including when earlier hints have already been seen. Required
facts belong in the question; completed solutions belong in the answer explanation.

Before delivery, actually try the questions from the learner-visible material without hints or
source-only context. This is an author self-check, not a claim of blinded independence. Recompute
arithmetic, test code safely where applicable, try competing answers and different valid solutions,
and check rubric/hint alignment. Record concrete remaining defects, not a self-awarded PASS.
Compare the bank with KC scope and check that variants add useful evidence rather than cosmetic
rewording. Do not add another agent or mandatory judge stage to perform these authoring checks.

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
