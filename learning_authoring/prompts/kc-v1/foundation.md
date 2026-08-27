# KC Foundation v1

Status: proposal for expert review. Scope: the meaning and structure of Knowledge
Components only. Assessment generation and mastery inference are separate cores.

## Research basis and role

- **KLI Framework — Koedinger, Corbett & Perfetti (2012):** primary construct
  basis for KC definition, knowledge forms, task condition -> KC -> response, and
  appropriate grain size.
- **Knowledge Tracing — Corbett & Anderson (1994):** supporting learner-model
  lens. A KC is a distinct latent state that may later be inferred from learner
  performance; this lens does not itself define source content or KC wording.
- **Learning Factors Analysis — Cen, Koedinger & Junker (2006):** validation
  lens. An initial KC model is a hypothesis whose split, merge, or refinement may
  later be evaluated with expert judgment and learner data.

These papers constrain the construct; they are not few-shot examples or labels.

## Canonical definition

A Knowledge Component (KC) is a learnable unit of knowledge or skill hypothesized
to be required for a related family of tasks. It is latent: source material can
support proposing a KC model, but does not turn that proposal into ground truth.

The functional lens is:

`Task condition -> relevant KC -> expected learner response`

This is a reasoning lens, not an equation, a claim that the source explicitly
states every task condition, or a rule that each page must produce a KC. It is
used to reject broad topic labels and to express why a proposed KC could support
a distinct observable learner response. Any proposed task condition must remain
consistent with the supplied source and must not require outside knowledge.

## Characteristics

A valid Leaf KC is:

- learnable rather than merely a topic label;
- sufficiently distinct to support a meaningful observable learner claim;
- connected to performance under identifiable task conditions;
- grounded in the supplied canonical extraction and, when explicitly supplied,
  separately attributed lecturer context;
- expressed at an instructionally and diagnostically useful grain size.

## Semantic forms

Use the closest reviewer-facing form: `fact`, `concept`, `distinction`,
`principle`, `procedure`, or `decision_rule`. The form describes the KC; it does
not impose a specific assessment format.

Knowledge may be verbal or non-verbal, and knowing may require explaining,
performing, or both. Do not assume that an explanation and a performance are
interchangeable evidence.

## Leaf KC and KC Group

A **Leaf KC** is a current-model unit for which the author can state a distinct
knowledge description, observable claim, boundary, and source evidence. It is
not claimed to be the universally smallest possible unit.

A **KC Group** is an organizational container created after Leaf KCs are
proposed. It supports navigation and review; it is not itself a Leaf KC.

## Granularity

Consider splitting A and B when a learner could know one without the other, they
can be learned or demonstrated separately, or their errors imply different
knowledge gaps. Consider merging when they are paraphrases, one is only an
instance of the other, or no meaningful task condition distinguishes them.
When the source cannot settle the choice, preserve the uncertainty for humans.

KC count is an outcome of this split/merge analysis, never a configured target.
Compression is not coverage: retaining a topic name while dropping its taught
mechanism, condition, contrast, exception, or operational steps loses knowledge.
After a merge, those distinct claims must remain explicit in the resulting KC's
description and boundary, or be recorded as deliberately unrepresented. This
does not require a new KC for every note or sentence.
Do not derive the count from pages, slides, blocks, headings, bullets, learning
objectives, or groups. One page may support zero, one, or several Leaf KCs; one
Leaf KC may require evidence from several pages. Stop splitting when a candidate
already represents one coherent learnable state with a distinct observable
claim and further division would not support meaningfully independent learning,
performance, or diagnosis.

Do not merge two candidates merely because the source presents them in sequence
or one is used to motivate the other. In particular, distinguish a prerequisite
representation from an operation applied to that representation when each can
support a different learner response. Conversely, a worked example that only
executes an already-defined procedure is normally evidence for that procedure,
not a new KC.

## Conceptual and representation boundaries

A title, section heading, person, tool name, isolated example, decorative image,
activity instruction, or broad topic is not automatically a KC. It becomes KC
evidence only when the source teaches learnable knowledge or skill about it.

A KC is a semantic unit, not an image, formula, table, chart, or code block.
Those PDF source elements are evidence for knowledge and remain owned by the
supplied canonical extraction. Optional lecturer notes or attachments remain
independent context, never new PDF blocks or extraction `page_note` content.
A source element must not be promoted into a separate KC solely because its
modality differs.

Source evidence supports the KC proposal. It is not evidence that a learner has
acquired the KC. Every model-produced KC remains `PROPOSED` until human review.
