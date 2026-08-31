# KC rules

1. Read the complete source set and retain capabilities with their evidence. Suggested inspection
   batches are reading aids; choose the sequence and size that preserve understanding.
2. Account for every page in `page_audit`. Classify non-learning pages honestly. A learning page
   without a KC needs a specific `uncovered_content` reason.
3. Test each candidate with the split/merge rule in the foundation. Never optimize toward a KC
   count or a page-to-KC ratio.
4. Write concrete names, descriptions, and observable claims in the learner-facing language. The
   observable claim states what evidence a learner could produce, not a vague verb such as
   "understand" by itself.
5. Keep `included` and `excluded` boundaries specific. Do not copy one generic boundary across KCs.
6. Cite only real page/block IDs from the frozen Extraction. For each claim, cite the smallest set
   of blocks that entails it. Do not copy every page block by default. `supports` states the exact
   claim those blocks entail; nearby or same-topic text is not a substitute.
7. For supplementary context, cite the exact context item and excerpt. Account for every material
   context claim as represented, excluded, unresolved, or not assessable. Context never becomes PDF evidence.
   Never invent a page map.
8. Reconcile source sections without losing their specific content. Merge true duplicates across
   PDFs; retain conflicts, source-specific variants, and uncertainty explicitly.
9. Use stable local IDs to keep references unambiguous. IDs do not determine KC scope or count.
10. Return schema-valid JSON only. All outputs remain `PROPOSED`; contract validity is not semantic
    approval.

Before returning, perform three checks:

- **Coverage:** every meaningful source capability is represented or specifically uncovered.
- **Grounding:** every positive sentence is supported by its own cited evidence.
- **Granularity:** each Leaf KC has one coherent learner response and one coherent remediation path.

Compare each description with its observable claim and scope. Naming a capability in background
text does not make it assessed. Preserve independently learnable capabilities or account for them
specifically; do not mark a page's whole knowledge covered merely because one KC cites that page.

Apply claim-level closure: every positive sentence must resolve to an evidence record cited on that
same KC. Run a final claim-to-reference pass; never import a useful conclusion from an uncited
summary page.
