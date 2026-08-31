# Connected review portal and optional static publishing

Read this reference when using the optional runtime portal, performing later human review or
approval, or handling an explicitly requested publication.

For separate Teacher/Student deployments, read [teacher-student.md](teacher-student.md).
The role-app backend adds course-scoped authorization and an explicit immutable lesson release.
The combined portal described here remains available for local review; do not deploy its full
answer-bearing bundle as the Student app.

## Honest statuses and review boundaries

The status vocabulary is:

- Extraction: `PROPOSED`, `HUMAN_APPROVED`, or blocked/review-needed.
- Proposed-Extraction input used for the continuous KC demo: `PROPOSED_DEMO_ONLY`.
- KC: `PROPOSED` and human-review-needed.
- Quiz: `EXPERIMENTAL_UNAPPROVED` and human-review-needed.
- Quiz semantic review (optional separate axis): `PASS`, `REVIEW`, `REJECT`, `NOT_REVIEWED`, or
  `STALE`. The default agent-led authoring flow leaves this `NOT_REVIEWED`.
- Learning/Mastery: `PROVISIONAL_EVIDENCE_MVP` when built with `--with-learning`, otherwise
  `NOT_ENABLED`. This does not certify the course or a learner's competency.

Schema validation means the JSON matches the machine contract. Geometry/form audits are diagnostic.
Neither is proof of semantic correctness or learning value. Never use `validated`, `approved`, or
`production-ready` for a model-authored artifact unless the matching human approval boundary exists.
An independently produced semantic PASS means only that no material problem was found in its
inspected scope. It is not human approval or certification of every upstream page/KC. Self-review,
incomplete source, or an explicit limitation cannot be presented as PASS. Check that the report
binds current input hashes; modified questions, KCs, source, or context invalidate earlier review.

Hints are authored support separate from the answer explanation. The Authoring reviewer preview
records only current hint/answer display state. Durable attempts belong to the separate Learning
view; see [learning-mvp.md](learning-mvp.md). Do not conflate those modes.
Human edits remain separate revisions; they invalidate the original semantic status until reviewed
again. In particular, a shared-review approval is not an automatic rerun of the semantic check.

The default continuous draft journey does not pause at these review boundaries and does not call
`approve`. The statuses stay visible so a reviewer can inspect the results after the whole connected
journey exists.

For a later Extraction approval, verify the review UI belongs to the same source hash and proposed
artifact the user reviewed. Run the CLI's `approve` command only on explicit instruction with the
real reviewer name. Do not silently acknowledge warnings. Rebuild the portal after approval so its
status comes from the verified approval pair rather than UI text.

KC has no machine approval command in this version. A user's candidate/KC selection is Quiz runtime
configuration; do not fabricate `kc-approved.json`.

An explicitly configured shared-review backend may persist reviewer-authored revisions and
Approve/Edit/Reject events for Extraction, KC, and Quiz. Raw candidates remain immutable, and
these collaborative events never authorize source mutation or create a canonical approved
pipeline artifact.

The optional product runtime can publish an immutable Learning release from explicitly selected current approved
KC/question revisions. This is a separate human decision, not a replacement AI check or an edit
of the original candidate. Missing/unpublished assessment coverage must remain visible.

## Build the matching connected local portal

If using the bundled renderer, use its matching portal builder after the authoring root has
produced Extraction, KC, and Quiz artifacts. A single-source run or exact source bundle is one data
boundary: never combine review files from unrelated runs/bundles or use checked-in demo content as
a fallback.

Inspect the builder's current help because flags may evolve, choose a fresh output directory, and
build:

```bash
<la> portal-build --help
<la> portal-build <run-dir> --with-learning --output-dir <fresh-portal-dir>
<la> bundle-portal-build --help
<la> bundle-portal-build <bundle-root> --output-dir <fresh-portal-dir>
```

Use `portal-build` for one source; add `--with-learning` only when learning features are requested. Use `bundle-portal-build` for an exact source
bundle; its current surface is connected read-only Authoring review, not the Learning MVP. The CLI
resolves current artifacts; do not pass paths from a prior run/bundle, rewrite candidate content,
or mix unrelated one-source portals. The portal must connect the journey from current data and
its generated manifest:

```text
one PDF/source identity or PDF 1..N/source-bundle identity
  -> agent-authored Extraction review(s): PROPOSED (or verified HUMAN_APPROVED on a later rebuild)
  -> shared KC review: PROPOSED; upstream PROPOSED_DEMO_ONLY in the default journey
  -> Quiz review: EXPERIMENTAL_UNAPPROVED
  -> when enabled by the selected builder:
     Learning attempts/hints -> grading -> evidence -> provisional mastery -> next action
```

Inspect `<fresh-portal-dir>/showcase-manifest.json` for one source or
`<fresh-portal-dir>/bundle-portal-manifest.json` for a bundle and verify:

- source-run or source-bundle hash, every source filename/ID, and per-source page count come from
  this exact authoring root;
- every stage label matches the actual artifact metadata;
- when the selected manifest exposes `quiz_initial_check`, it matches the bound report (or clearly
  states missing/stale), separately from approval; the Quiz view never implies a missing check;
- every entrypoint exists and opens the matching current-run review;
- the page inventory is derived from the manifest rather than a fixed count;
- no stale course title, run name, content string, page number, or KC ID is embedded in the shell;
- when Learning is enabled, it starts with no fabricated attempts or mastery and its configured
  storage mode is clear.

Treat the manifest as an allowlist. The local package may contain only:

- the connected static portal and selected review HTML/CSS/JS;
- rendered page images those UIs require;
- semantic JSON required by the selected UIs and declared by the builder;
- the Learning UI and version-pinned Quiz/KC data used for practice (not a private examination);
- redacted metrics declared by the builder.

It must never contain:

- this Agent Skill or executable pipeline code;
- `.env*`, credentials, or Vercel secrets;
- source PDF/PPTX files;
- raw agent candidates, provider envelopes, checkpoints, response IDs, or task/prompt packages;
- private answer-material companion files from `agent-session/review-materials/`;
- reviewer identity/notes unless explicitly required;
- learner response history, feedback, private staff lists, or SQL registration files;
- unrelated historical runs.

Serve the fresh directory locally if the user wants to inspect it immediately. Return the portal
path, manifest path, and stage entrypoints. Local portal construction is not Vercel publication.

If the user explicitly requests collaborative review, `portal-build` may receive one exact
Supabase project URL and its public publishable/legacy anon browser key. Never accept or publish a
service-role key. The review UI may use silent Supabase Anonymous Auth and ask only for a display
name; RLS must deny unauthenticated writes and every edit/decision must be append-only and pinned
to the exact payload hash or revision it reviewed. Register every reviewable item with its immutable
baseline hash before publication. Revoke direct event inserts and route writes through a server-side
transaction that validates target existence, stage payload shape, payload size, reviewer rate, and
the latest expected revision. Once a target has review history, changed output requires a new run ID.
Name-only anonymous review is suitable for a link shared with known collaborators; require an
additional CAPTCHA or invite-token boundary before treating it as unrestricted public write access.

With the Teacher/Student migration installed, course-scoped teacher authorization is required
even for shared review writes and review history. Anonymous sign-in plus a display name alone
does not grant edit, approval, publication, grading or private learner-history access.

## Publish to Vercel only with separate authorization

For shared Learning, also apply the Learning migration and register immutable snapshots using the
offline `learning-authoring-product export-learning-registration` command. A browser key does not
confer staff grading permission.
Do not infer a trusted grader from a display name or expose learner responses to public reviewers.
Read `learning-mvp.md` before enabling this backend. Updating the app never authorizes overwriting
historical attempts.

Do not infer deployment permission from a request to run the pipeline, build the connected local
portal, or review locally. Publish only when the user explicitly requests Vercel publication and
the intended account/team, project, and environment are authorized. If the target is ambiguous,
finish the local portal and ask for the missing deployment choice instead of selecting one.

Run offline tests/lint before deployment when operating in the repository. Deploy the fresh portal
directory, never the repository root or run directory. Prefer Preview unless the current request
explicitly authorizes Production for the exact target. Verify every manifest entrypoint at the
immutable deployment URL.

Return the immutable URL, project URL when available, exact included entrypoints, exclusions, and
honest stage statuses. Vercel shows the generated connected result; it does not host or execute the
skill.
