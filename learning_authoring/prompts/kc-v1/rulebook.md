# KC Rulebook v1 — Canonical Extraction Input

This rulebook turns one complete canonical `extracted-source.v2` JSON
artifact into a human-reviewable Proposed KC Set. It operationalizes the KC
Foundation; it does not redefine the construct or repeat extraction.

## Provenance of the rules

- KC Foundation v1 supplies the construct and granularity principles.
- Cognitive Task Analysis supplies the task/condition/knowledge decomposition
  lens.
- Learning Factors Analysis supplies the split/merge/refine hypothesis lens.
- Human-machine student-model discovery motivates preserving ambiguity for
  expert review and later learner-data validation.
- Product requirements add full-page accountability, source grounding,
  traceability, extraction immutability, and no forced KC count.

## Input boundary

The only source input is the complete canonical extraction JSON supplied in the
task package. Its upstream status may be human-approved or explicitly marked
`PROPOSED_DEMO_ONLY`; that status changes downstream review labels, not the
semantic transformation. Use every relevant field in the JSON, including source identity,
ordered pages, semantic blocks, content, regions, asset references, relations,
page notes, cross-page relations, warnings, and uncertainties.

No PDF, rendered page image, text audit, source manifest, raw provider response,
previous KC output, or external source is supplied or permitted. Never infer
that a local `asset_refs` string means the underlying file was attached. Do not
request, assume, or invent source content that is absent from the JSON.

The supplied extraction is immutable. Never rewrite its blocks, content,
coordinates, relations, warnings, source identity, or page count. Cite it using
existing block IDs instead of copying it into a second artifact registry.

## Rules

1. **Page accountability:** emit exactly one `page_audit` entry for every page
   `1..source.page_count`, in ascending order. A page may support zero, one, or
   several Leaf KCs. `source_block_ids` records the page blocks materially
   considered for the classification or KC decision; it need not mechanically
   include purely decorative blocks.
2. **Non-KC classification:** distinguish taught knowledge from examples,
   exercises, context, cover/section dividers, and administrative or decorative
   material. Never force every page to create a KC. When a page mixes roles,
   choose the dominant classification and describe the other source-grounded
   roles in the audit summary.
3. **KC eligibility:** create a Leaf KC only for learnable knowledge or skill
   that supports a distinct observable learner claim.
4. **Source grounding:** bind every Leaf KC to one or more existing semantic
   block IDs. Each evidence record must explain what those blocks support.
   Page notes may help interpret block relationships, but they cannot replace
   block-level evidence or introduce content absent from the blocks.
5. **Evidence preservation:** do not transcribe, summarize, relocate, or
   regenerate an evidence artifact in the KC output. The supplied extraction is
   the canonical registry for content, modality, geometry, asset references,
   relations, and uncertainty. KC output stores only references to it.
6. **Evidence locality:** every evidence record declares one page and may cite
   only block IDs belonging to that page. Preserve every page that contributes
   a materially distinct definition, operation, interpretation, exception, or
   result to a multi-page KC.
7. **Atomicity and split:** split candidates when independent learning or
   performance is meaningful. Do not split merely because a page has several
   bullets, blocks, or modalities.
8. **Merge and deduplicate:** merge paraphrases and instances that do not
   justify a separate knowledge state. Keep distinct procedures or decision
   rules apart.
9. **KC expression:** write a concise name, semantic form, knowledge
   description, observable claim, and explicit included/excluded boundary.
   Preserve precise technical terms. Express `observable_claim` conditionally:
   given an identifiable task condition, state the learner response that can be
   observed. Avoid unobservable wording such as “understands” without a
   demonstrable response. The condition may be a conservative authoring
   hypothesis, but it must remain consistent with the supplied extraction and
   must not depend on outside knowledge.
10. **Grouping:** normalize Leaf KCs first, then create bottom-up Groups. Every
    Leaf KC has exactly one Group. Groups have no fixed count or size.
11. **Cross-source reconciliation:** use block relations, cross-page relations,
    page notes, warnings, and uncertainties to reconcile recurring claims.
    Record material ambiguity; never silently choose or repair a version.
12. **Uncovered content:** record learning-relevant source content not
    represented by a Leaf KC, citing existing same-page block IDs. Do not hide
    it or convert it into a coverage score.
13. **Uncertainty:** use evidence-linked warnings rather than confidence scores.
    Human review decides semantic split, merge, edit, rejection, grouping, and
    evidence changes.
14. **Reference reconciliation:** before returning, make KC, group, page,
    warning, evidence, and source-block references bidirectionally consistent.
    Never fabricate a page number or block ID.

## KC count policy

There is no configured minimum, maximum, target, ratio, or formula for KC count.
Do not derive KC count from page count, slide count, block count, bullet count,
section count, or learning-objective count. Count emerges only after eligible
candidates are split when independently learnable or diagnosable, and merged
when they are paraphrases, instances, or inseparable parts of one knowledge
state. A valid run may therefore contain substantially fewer or more KCs than
pages. Report ambiguity for human review instead of adjusting toward an
expected count.

## Identifier convention

- Leaf KC proposal IDs: `KC-001`, `KC-002`, ...
- Group proposal IDs: `KCG-001`, `KCG-002`, ...
- Source evidence IDs: `EVD-001`, `EVD-002`, ...
- IDs are local to one supplied extraction and carry no topic/domain semantics.

## Warning vocabulary

- `SOURCE_AMBIGUOUS`
- `VISUAL_UNCLEAR`
- `OUT_OF_SOURCE_REQUIRED`
- `ASSESSABILITY_AMBIGUOUS`
- `GRANULARITY_AMBIGUOUS`
- `POSSIBLE_DUPLICATE`
- `GROUPING_AMBIGUOUS`
- `UNCOVERED_CONTENT`

## Contract boundary

Code may hard-fail a missing or unauthorized extraction state, hash mismatch, malformed
JSON, invalid IDs, duplicate or broken references, page omissions, invented
block IDs, and page/block locality errors. Code must not hard-fail semantic
width, possible duplication, grouping quality, coverage percentage, model
confidence, or judge scores.
