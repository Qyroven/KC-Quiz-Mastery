# One journey, two role apps

Use the existing completed run unchanged. Do not regenerate Extraction, KC or Quiz merely to
add these views. The standalone apps share a backend, not a browser identity or a writable
global content object. No model-provider API is involved.

## Build

Inspect `learning-authoring-product --help` first. Its product/export commands are offline.

```text
learning-authoring-product build-role-apps <run> <fresh-output> --local-preview
```

This creates `teacher/` and `student/`. Local Student preview may include frozen answer material
for deterministic grading and is visibly unapproved/local. Its manifest has
`deploy_allowed: false`; do not publish it. Teacher management in preview is read-only, not a
simulation of actual authorized approvals or learner records.

For shared use, replace `--local-preview` with the exact authorized Supabase project's
`--review-supabase-url` and `--review-supabase-publishable-key`. The Student bundle contains no
question bank, keys, teacher controls or private learner data. It loads the published learner
packet after creating a name-labelled anonymous session. Never silently fall back to local data
if shared storage or authentication fails.

The Teacher bundle retains static authoring review pages, including answer material. Backend
authorization protects edits, publication, grading and private learner history. Static preview
files are not a private examination boundary: protect the Teacher deployment separately if
answer confidentiality is required. Two URLs alone do not establish backend authorization.

## Backend setup and permission

The ordered migrations through `202608280002_teacher_student.sql`, immutable review targets,
learning items and the authoring package must be installed through an authorized operator.
Export the last package with:

```text
learning-authoring-product export-authoring-registration <run> <new-private-path.sql>
```

That SQL requires the existing review/learning baseline and never grants a role or publishes a
lesson. Keep SQL outside run/app directories and never expose a service key. Do not reapply old
seed or insert-only registration to an existing run.

Teacher grants are scoped to a course/root run in `learning_course_teachers`. Verify the exact
existing authenticated user ID and obtain authorization before granting. A typed teacher name,
global legacy grader role, or visitor to the Teacher URL is insufficient. When no authorized
teacher exists, show the setup requirement; never create synthetic approvals to demonstrate the
flow. Student identities cannot read other learners' histories or use teacher RPCs.

## Review, release, learning

Teacher reviews the current KC and question revisions, then explicitly selects reviewed items
for a release. Display omissions and unmeasured assessment slots rather than claiming a complete
course if only some questions were selected. A release freezes the source/KC/slot/question/hint/
rubric versions and the exact approval provenance. A stale review fingerprint must require a
refresh, not overwrite a concurrent edit.

Student explicitly chooses a published version. Later edits and new releases do not silently
change the version being studied or rewrite past responses. Questions receive server grading;
authored hints are recorded before reveal. Short-text remains pending until a course-authorized
teacher applies the frozen rubric. The learner can see the awarded criteria and teacher comment.

An explicit human-reviewed release may make a corrected item suitable for practice without
rewriting its original AI-check status to PASS. Show those two decisions separately. Authoring
raw output stays unchanged. Partial/assisted/pending evidence is not independent success.

Teacher can inspect each authorized learner's actual attempts, hints, rubric grades, per-KC
evidence and suggested next action. Student sees readable knowledge goals and their own states,
never technical editing/approval controls. Use “Chưa đo”, “Cần ôn”, and “Đã có bằng chứng độc lập”
with separate pending, assisted and partial qualifiers; do not show a mastery percentage or rank.

Recommendations target an unresolved assessment slot and explain the supporting attempt. If
no fresh variant exists, say so; another objective can be offered separately, not falsely called
remediation of the original gap. No graph prerequisites, forgetting schedule, automatic question
generation or calibrated expertise claim is implied by this MVP.

Feedback remains a separate versioned improvement input. A like/dislike never changes a grade,
mastery state, source artifact or prompt automatically. Publish the two generated app directories
only after explicit authorization for each deployment target and backend change.
