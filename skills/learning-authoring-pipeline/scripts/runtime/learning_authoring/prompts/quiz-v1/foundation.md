# Quiz — Foundation

By default, generate an unapproved assessment-slot plan, its Quiz questions, and useful optional hints directly from the supplied Knowledge Components, together in one response. There is no separate planning generation stage or generation call when a learner requests a hint. The explicit legacy count override follows its v1 output contract instead.

Each question must let a reviewer answer one thing: would a learner who understands this KC be more likely to answer correctly than a learner who only recognizes its wording? Test the KC's essential claim or operation. Do not teach, restate, or reveal the answer in the stimulus.

Write a question a teacher could actually ask a learner. Give necessary facts and a clear task, then leave the target decision or reasoning to the learner. Generation constraints, warnings to the model, and the author's own checklist are not learner-facing content. A short, direct foundational question is preferable to a contrived scenario pretending to be difficult. Name Bloom and intended difficulty from the work left in the final, unhinted question, not from its topic, verb, length, or response format.

Choose an interaction that captures the evidence named by the KC, not merely an answer that can be keyed. If the observable claim requires the learner to explain, justify, diagnose, design, trace, calculate, or produce a bounded artifact, a bare selected response usually proves only recognition. Use a constructed or structured response when that production is the evidence; use selected response when choosing among alternatives is itself sufficient. This is a semantic decision, not a keyword mapping or an interaction-diversity quota.

Structured response is not the same as selected-response recognition. Matching can directly capture a relationship set or classification; ordering can capture a sequence or dependency; single- or multi-select can capture a bounded decision when the alternatives themselves are the evidence. Use `short_text` only when the quality of learner-authored language, reasoning, derivation, diagnosis, or design is itself necessary evidence and cannot be scored faithfully through an allowed structured interaction. Never choose prose merely because it is the safest generic container. Across a batch, repeated use of one interaction is a reason to re-check evidence-to-interaction fit, not a reason to force cosmetic diversity.

The visible task and its scoring must agree. Assess the requested evidence, not an unstated ideal answer: accept valid alternative explanations, criteria, methods, and equivalent numerical answers when they satisfy the task. Recall and ordinary calculation can be useful evidence without pretending to be analysis or transfer.

The KC is author context, not material the learner can see. A task that depends on a particular
diagram, convention, formula, table, or case must supply it in the stimulus unless recalling that
specific taught content is deliberately the target. Do not label a missing-formula memory test
as application. Define which quantity each symbol weights; make scope such as "any valid subset"
versus "the complete valid set" explicit. Neither hints nor the source reference supplies missing
initial data to the learner.

Keep a simple stimulus when sufficient. For mixed material use `kind: composite` with ordered
`blocks` of `text`, `table`, `formula`, or `image`; leave the outer legacy text/table/formula fields
empty. Image blocks use only `asset_id` from the task's `media_assets`, a descriptive `alt`, and
optional normalized top-left `crop: {x,y,w,h}` (null means the full page). Inspect that source image
before choosing a crop. Retain relevant labels, arrows, axes, legends and units without the slide's
worked answer; do not redraw unseen relationships or put the answer in alt text. An image is not
required when a faithful text/table/formula representation suffices. Verify the actual rendered
learner view after import; an incorrect or unreadable crop is not solved by a valid file hash.

A decisive answer requires decisive facts. When a scenario leaves a material
factor unknown, either provide a bounded value or ask for a conditional judgment
that identifies what follows and what remains unresolved. Never force one
categorical conclusion merely because the exemplar chose it. Likewise, do not
announce every missing criterion in the stimulus and then call copying those
labels an evaluation.

An assessment slot is one distinct, bounded piece of learner evidence or cognitive operation that can test the KC. A variant is another item for the same slot: it preserves that evidence intent, operation, and intended difficulty while changing the concrete instance. Different reasoning targets belong in different slots, not under a misleading variant label.

For a selected-response item, make every option a credible decision by a partially informed learner. A useful near miss preserves most of the case and gets one consequential condition, threshold, relationship, or exception wrong. The keyed option must not be the only answer that sounds nuanced, safe, complete, or professional. If the stem names every decisive criterion and the key simply repeats that list, the item is recognition even when its topic is complex.

Use only the frozen input. Let the KC's actual scope determine its slots and useful variants. There is no universal question count, Bloom ladder, difficulty mix, or interaction quota. Questions and slot choices are proposals for human review, never approved content. Structural validation checks identities, counts, references, and response shapes; it does not establish pedagogical validity.

A hint is optional scaffolding requested before the learner sees the answer. It helps the learner take a next step while leaving the assessed work to them. It is not the answer explanation, a hidden answer key, a fixed penalty, or proof of mastery. Choose only the useful number of hints for that particular item; an item may have no safe helpful hint. Keeping unsupported content out is more important than claiming every case can be handled.
