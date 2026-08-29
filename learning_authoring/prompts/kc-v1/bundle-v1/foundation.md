# Source-Bundle KC Foundation v1

Status: proposal for expert review. This package applies the shared Knowledge
Component construct to an ordered, nonempty collection of independently
extracted PDFs. Assessment generation and mastery inference remain separate
stages.

## Shared KC construct

A Knowledge Component (KC) is a learnable unit of knowledge or skill
hypothesized to be required for a related family of tasks. It is latent: source
material can support a KC proposal, but neither one source nor agreement across
sources makes that proposal ground truth.

Use this reasoning lens:

`Task condition -> relevant KC -> expected learner response`

The task condition and response must stay within the supplied Extractions and
separately attributed lecturer context. They are authoring hypotheses, not
permission to import outside knowledge.

A valid Leaf KC is learnable, supports a distinct observable learner claim, is
grounded in supplied evidence, and has a useful instructional and diagnostic
grain size. Use the closest semantic form: `fact`, `concept`, `distinction`,
`principle`, `procedure`, or `decision_rule`. A KC Group is only a navigation
container formed after Leaf KCs are stable. A Leaf KC represents one coherent
observable capability, not a source section or a convenient bundle of all
claims that happen to share a topic.

## Granularity across sources

Split candidates when a learner could know one without the other, demonstrate
them independently, or produce diagnostically different errors. Merge only
paraphrases, instances, or inseparable parts of one knowledge state. A shared
KC may span sources only when every contributing claim retains explicit,
source-qualified evidence. Preserve source-specific conditions, exceptions,
and disagreements instead of flattening them into a generic topic.

Shared vocabulary, adjacency, one diagram, one workflow, or recurrence across
sources does not itself justify a merge. If candidates require meaningfully
different learner responses or different remediation after an error, retain
them separately unless the supplied sources make them inseparable.

KC count is an outcome of eligibility, split, merge, and deduplication. Never
derive it from the number of PDFs, pages, blocks, headings, bullets, objectives,
or groups. Any source page may support zero, one, or several Leaf KCs; a Leaf KC
may use evidence from any number of supplied sources.

## Evidence and context boundaries

Each PDF remains an immutable canonical `extracted-source.v2` artifact.
Source evidence points to the exact `source_id`, page, and existing same-page
block IDs. Extraction content, geometry, relations, assets, warnings, and page
notes are not rewritten into KC output.

Optional `authoring-context.v1` is a separate lineage axis bound to the whole
bundle. It can clarify emphasis or scope but never becomes a PDF block or an
Extraction correction. A context page link names exactly one source only when
the supplied meaning supports that link. An ordinal in a note is never projected
onto the same page number in multiple PDFs.

A title, tool name, isolated example, decorative asset, activity instruction,
or broad topic is not automatically a KC. Every generated KC remains
`PROPOSED` until human review, and source evidence is not evidence that a learner
has acquired the KC.
