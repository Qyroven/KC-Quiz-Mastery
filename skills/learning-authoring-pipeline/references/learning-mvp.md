# Learning MVP: real actions, provisional evidence

This is a small continuation of Authoring, not another model pipeline:

For the separate Teacher/Student apps in runtime 0.6, also read
[teacher-student.md](teacher-student.md). The original combined portal below remains a local
practice/review surface; it is not the Student deployment. Shared role apps require a
course-scoped teacher grant and an explicit immutable release before Student can enroll.

```text
Frozen Quiz + hints + key/rubric + KC/slot lineage
  -> a person answers and optionally asks for authored hints
  -> exact grading or pending human rubric grading
  -> versioned evidence for that learner
  -> provisional per-slot/per-KC state
  -> review relevant knowledge or try an appropriate next question
```

The coding agent builds the UI; it does not fabricate learner sessions. No provider SDK, API key,
embedding model or hosted LLM is needed. Keep Authoring questions, hints, rubrics, context and raw
archives unchanged.

## Build and storage

Inspect installed command help, then:

```bash
<la> portal-build <run-dir> --with-learning --output-dir <fresh-portal-dir>
```

The Learning package contains only this run's frozen content, source hashes and initial-review
statuses. Node.js is a local hashing dependency, not a network service. The portal includes a
fourth active step, `Học & Mastery`.

Without a backend, learner history is local to the browser/device and labelled local. With an
authorized Supabase configuration, authenticated anonymous identities persist their own attempts
and feedback. A display name is not verified identity or a cross-device login; clearing browser
storage can lose access to that identity. Never silently fall back to local persistence when a
configured shared backend fails. Public review content includes answer material: this is formative
practice, not a secure examination.

Local writes require Web Locks on HTTPS or localhost, so two tabs cannot overwrite each other's
attempts or erase hint use. Unsupported browsers remain read-only with an explicit message.
Shared mode uses server-side locking and never trusts a browser-computed grade.

Shared setup requires the existing shared-review migrations/run registry and the Learning
migration in the repository. Export immutable learning items offline:

```bash
<la> learning-register <run-dir> <absolute-path-outside-run-and-portal.sql>
```

Apply SQL only through the authorized administrator surface. The exporter must not connect to a
database or use a service key. Publish the portal, not the SQL. Never replace registered snapshots
or historical evidence on a content change. Legacy combined-portal grading uses a staff
allowlist; the role-app migration replaces this with course-scoped teacher grants. A legacy
global grader is not automatically a teacher for every course. Typing a teacher's name grants no role.
The operator must verify the exact existing authenticated user ID before granting that role.
With no staff configured, objective questions still complete the loop; short-text remains pending.

## Grading and evidence

- Single-select, multi-select, matching and ordering use the frozen key. Validate response IDs,
  shape and completeness before grading. An invalid submission is not an incorrect answer.
- Short-text stays `pending_grade` until authorized human rubric grading. Do not substitute
  exact wording, keywords, self-rating or guessed scores for semantic evaluation.
- Record hint use before revealing the authored hint. Correct hinted work is assisted evidence,
  with no arbitrary percentage penalty per hint.
- An item whose answer was exposed by a submitted attempt is practice on retry, not new independent
  evidence. Public review access means perfect blindness cannot be claimed even on a first attempt.
- In draft/local practice, non-PASS, rejected, stale or changed-question/upstream content is not
  trusted independent evidence. A Teacher's explicit reviewed release can authorize corrected
  items separately from their original AI status; it never rewrites that AI status or raw output.
  Preserve attempts and explain exclusions instead of deleting inconvenient data.
- Keep question/KC/source/context hashes, grader method/version, timestamps, response, hint IDs
  and repeat/exposure status. Retrying the same request must not double-count it.

Mastery uses transparent `evidence-rules.v1`, not BKT/IRT, a probability or a validated ability
score. Coverage follows the actual assessment slots. Pending/no-evidence, needs-practice, assisted,
developing and demonstrated states must follow recorded first-question evidence. No fixed question
count per KC, time penalty, hint discount or feedback sentiment should manufacture mastery. Legacy
content without slots can be practiced but cannot claim complete assessment coverage.

The next action can point to relevant KC/source material, another unattempted question for the
same need, or the next unmeasured KC. If no suitable item remains, explain that more evidence or
grading is needed; do not regenerate invisibly or loop forever.

In separate role apps, an unresolved slot is matched to a fresh variant of that same slot.
If none exists, say the target lacks another suitable item; a different objective is offered
separately, not labelled remediation. Repeated/exposed question identities remain exposed across
course releases. Unpublished KC content stays hidden from Student while its unmeasured coverage
remains visible. Use evidence labels, not a mastery percentage or rank.

## Two separate feedback loops

The learner loop uses attempt evidence to choose practice/review. The system-improvement loop
stores like/dislike and explanations linked to the exact item/version and optional attempt.
Feedback changes neither a learner's score nor the question automatically.

This release collects the basis for later tracing, reviewed proposals, regression tests and
versioned publication. It does not auto-train, auto-fix content, re-run Authoring, or modify mastery
rules from live feedback.

Use named QA identities or isolated local fixtures for testing; never present test history as
learner evidence. Verify anonymous users cannot read others' answers or grade them, and that
shared edits/rejections invalidate affected evidence.
