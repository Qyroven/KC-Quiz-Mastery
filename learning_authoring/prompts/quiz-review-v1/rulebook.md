# Review rules

## Solve before seeing the author's answer

1. Inspect the learner-visible packet first: prompt, stimulus, response options,
   and only the content the learner is actually given. Determine a compact
   independent answer, or state precisely why the learner cannot answer. Do not
   consult the supplied answer key, explanation, rubric, or hints at this point.
2. Preserve that answer before opening the companion review packet. Do not
   rewrite it to match the key. If you already saw the key before solving or
   could not follow the ordered workflow, state that limitation. Packet access
   is not assumed technically isolated: describe an ordered independent review,
   never an enforced blind test unless the host genuinely enforced one.
3. Open the companion packet, compare the key/rubric with your recorded answer,
   and inspect the KC, slot, relevant source, sibling variants, and hints. Use
   the declared source PDF/targeted page images when a visual or an extraction
   inconsistency needs verification. Do not mistake extracted text for proof
   that an original chart or formula was read correctly. Inspect declared
   nontext lecturer attachments when their contents are necessary to judge.

Report `checked_source_pages` and `checked_context_ids` honestly. A checked source
page means the original PDF/page was inspected, with visual inspection for visual
claims, not merely that its Extraction JSON was accepted as true. A JSON-only
check is still useful but must declare the unverified original as a limitation.
Complete source
coverage means all relevant cited sources for this Quiz, not every course page.
If required evidence is inaccessible or has not been inspected, scope is partial
or unknown, with a concrete limitation. Such a review cannot obtain overall PASS.

## The six required checks for every question

- **grounding:** Is the assessed claim supported by the cited slide content or
  lecturer context, within the KC boundary? Is a hypothetical clearly supplied
  and self-contained rather than falsely presented as a source fact? If source
  and lecturer context conflict, identify the conflict instead of silently
  choosing one. Trace a possible upstream defect to extraction or KC only when
  the supplied evidence supports that diagnosis.
- **answerability:** Can a learner answer from the stated information and
  expected KC knowledge? Check instructions, scope, units, negations,
  interpretation, missing visual data, and genuinely competing answers. Do not
  require a particular interaction type; every supported type can be good or bad.
  For code, distinguish executable code, pseudocode, and labeled excerpts. Check
  the stated inputs and language semantics: do not assume `...` performs an
  omitted operation. Flag an ambiguous placeholder only when it changes the
  assessed behavior or leaves the answer underdetermined, not merely because an
  ellipsis appears in otherwise sufficient pseudocode or irrelevant context.
  Read the text as a learner: flag material confusion from authoring jargon,
  model-directed restrictions, unnecessary prose, or an unnatural task. A brief
  question and necessary response limits are not defects by themselves.
- **alignment:** Does the response demonstrate the slot's stated evidence
  intent and the KC's observable claim? Does the actual cognitive work support
  the Bloom label and intended difficulty? Bloom, format, and difficulty are
  different axes. Judge intended difficulty qualitatively; it is not calibrated
  mastery and must not be reported as empirical learner performance. State what
  work remains without hints after accounting for the title, stimulus, and
  scaffolding. Naming an error for the learner does not test locating that error;
  supplying a procedure does not test independently choosing it. Record material
  overclaims as concerns even when the key is correct. Do not inflate labels
  because the response is open-ended, verbose, or set in an unfamiliar scenario.
  If the stimulus already supplies the deciding distinction, naming it is not
  analysis of that distinction. Do not penalize honest recognition, ordinary
  calculation, or necessary givens just for being simple.
- **scoring:** Is the supplied key uniquely defensible where uniqueness is
  required? For multi-select, matching, and ordering, check all required
  relationships and allowable alternatives. Objective interactions use their
  ID-based key and `answer_explanation`, with `rubric: []` and
  `correct_answer.text: ""`; these empty fields are required, not missing scoring.
  Only `short_text` uses a nonempty rubric and exemplar text. For short text,
  assess the rubric, not literal similarity to the exemplar: relevant alternatives
  must receive credit, criteria must be observable and non-duplicated, and no criterion may
  demand absent information or an unrequested deliverable. Compare every scored
  requirement with the visible task. An open request for criteria does not imply
  a hidden mandatory set of categories; choosing different supported criteria may
  be correct. A numeric-answer-only task need not show an unrequested derivation,
  and equivalent values or methods must not lose credit just for differing from
  the exemplar. Partial credit for necessary intermediate work is valid when it
  does not impose extra conditions for a fully correct requested answer. State
  a key or rubric defect with exact locations, distinguishing unclear scoring
  from a demonstrated rejection of a valid response. Check that ID-based keys
  resolve to actual displayed choices; do not silently repair a mismatched key.
- **cues_and_variants:** Could a learner obtain the answer through superficial
  cues without the intended knowledge? Inspect distractor plausibility,
  grammatical fit, option specificity, elimination, displayed ordering,
  hint-like titles, and repetition. Being the longest option is a diagnostic
  lead, not an automatic REJECT; explain a credible shortcut in this question.
  Look for nearby misconceptions or plausible conditional mistakes. Extreme,
  unrelated, or obviously careless strawmen can expose the only sensible option
  even when all options have equal length. Do not reject an option merely for
  being incorrect; explain why the distractor bypasses the target knowledge.
  Compare siblings within the same slot: they should target the same evidence,
  cognitive operation, and intended difficulty while adding a meaningful
  alternative instance. Different slots may legitimately assess different work.
  Do not confuse one slot's variants with all questions belonging to a KC.
  State the actual decision-relevant difference between siblings. Changed
  numbers may provide useful fresh calculation or a boundary case, but alone
  do not prove broader evidence coverage. Conversely, do not reject a numerical
  practice item solely because its wording resembles another item.
- **hints:** Inspect all supplied hints, their order, their intended support, and
  their relationship to the learner view and answer. A useful hint helps the
  learner take a next step without directly revealing the scored response,
  selecting an answer, or reducing a multi-step task to copying. A later hint
  may be more specific without becoming the answer explanation. Correctness,
  accessibility, usefulness, accidental answer leakage, and cumulative leakage
  across every revealed prefix all matter. Check whether a term-description list
  or ordered mnemonic supplies the complete matching or ordering key without
  answer IDs. A method or formula may be useful support when using it is the
  target, but leaks the answer when selecting or recalling it is the target.
  Distinguish actionable help from generic encouragement; weak style alone is
  not equivalent to answer leakage. Hint `kind` is a cue/strategy/step
  label, not a compulsory ladder. Hints cannot supply essential missing question
  facts. If `hints` is empty, judge the stated `hint_absence_reason`: zero is valid
  only when no useful non-answer-revealing support is reasonably available for
  this task. Do not require a fixed count or invent hints for a legacy artifact.
  Missing hint metadata is a review limitation, not evidence of validated hints.

## Findings and decisions

Every criterion needs a concise rationale. PASS means no material issue found
in the inspected scope, not proven correctness. REVIEW means uncertainty or a
material concern requiring a decision. REJECT means a concrete, supported defect
that makes the item unsuitable as currently written. Do not force a quota of
PASS, REVIEW, or REJECT.

Every REVIEW/REJECT criterion must include at least one issue with a concrete
observation and exact JSON Pointer location(s) in the supplied `quiz`, `kc`,
`extraction`, or `context` snapshot. Use the real snapshot array positions, not
page numbers or IDs as guessed array indexes. A quote, when present, must be an
exact substring at that textual location; use null for nontext locations.
Locate missing information at the affected prompt, stimulus, evidence reference,
or containing object. An issue attributed to `hint` or `scoring` normally points
to the relevant field in the Quiz snapshot. Never invent page/context IDs.

The host derives the decision: any REJECT wins; otherwise any REVIEW, self-review,
incomplete source, or limitation means REVIEW. PASS is only an initial AI check,
never human approval. Missing reviews are NOT_REVIEWED; mismatched source hashes
are STALE. Do not add your own overall verdict, quality score, approval flag, or
mastery estimate. Do not repair, regenerate, overwrite, or delete any candidate.
