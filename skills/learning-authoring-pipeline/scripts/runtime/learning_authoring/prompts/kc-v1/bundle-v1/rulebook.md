# Shared KC rules

1. Verify the ordered bundle and process `input_boundary.inspection_batches` in order. Build a
   capability inventory per source window before reconciling within or across sources.
2. Emit one source-qualified `page_audit` for every page. Non-learning pages do not require KCs;
   meaningful learning content without a KC needs a specific uncovered reason.
3. Apply the split/merge rule from the foundation without a target count or page ratio.
4. Cite real source IDs, pages, and same-page block IDs. Support each claim by selecting the smallest
   set of blocks that entails it; do not copy every block from the page by default.
5. Preserve source-specific conditions and conflicts. Never project page ordinals across PDFs.
6. Cite context by exact context ID and excerpt. Map it to one source only when the supplied meaning
   supports that mapping; otherwise retain document-level or unmapped provenance.
7. Write concrete names, knowledge descriptions, observable claims, and included/excluded
   boundaries. One Leaf KC has one coherent learner response and remediation path.
8. Stabilize Leaf KCs before forming Groups. IDs are local labels without topic semantics.
9. Account for every material context claim and every meaningful uncovered source claim.
10. Copy exact bundle/context hashes and return schema-valid `PROPOSED` JSON only.

Before returning, run coverage, claim-to-reference grounding, source qualification, and granularity
checks. Contract validity is not semantic approval.
