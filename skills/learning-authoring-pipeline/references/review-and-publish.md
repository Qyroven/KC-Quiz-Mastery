# Connected review portal and optional static publishing

Read this reference when building the default local portal, performing later human review or
approval, or handling an explicitly requested publication.

## Honest statuses and review boundaries

The status vocabulary is:

- Extraction: `PROPOSED`, `HUMAN_APPROVED`, or blocked/review-needed.
- Proposed-Extraction input used for the continuous KC demo: `PROPOSED_DEMO_ONLY`.
- KC: `PROPOSED` and human-review-needed.
- Quiz: `EXPERIMENTAL_UNAPPROVED` and human-review-needed.
- Quiz initial check (separate axis): `PASS`, `REVIEW`, `REJECT`, `NOT_REVIEWED`, or `STALE`.
- Mastery: `NOT_IMPLEMENTED`.

Schema validation means the JSON matches the machine contract. Geometry/form audits are diagnostic.
Neither is proof of semantic correctness or learning value. Never use `validated`, `approved`, or
`production-ready` for a model-authored artifact unless the matching human approval boundary exists.
An initial semantic PASS means no material problem was found in the inspected scope. It is not
human approval or certification of every upstream page/KC. Incomplete source, self-review, or an
explicit limitation cannot be presented as PASS. Check that the report binds the current input
hashes; modified questions, KCs, source, or context invalidate earlier review.

Hints are authored support separate from the answer explanation. The local learner preview records
only its current hint/answer display state. It does not persist learner evidence or compute mastery.
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

## Build one connected local portal

Use the installed runtime's deterministic portal builder after the run has produced Extraction,
both KC review views, and Quiz review. The source run is the single data boundary: never combine
review files from unrelated runs or use checked-in demo content as a fallback.

Inspect the builder's current help because flags may evolve, choose a fresh output directory, and
build:

```bash
<la> portal-build --help
<la> portal-build <run-dir> --output-dir <fresh-portal-dir>
```

The CLI resolves the current run's review artifacts; do not pass paths from a prior run or rewrite
their content. The portal must connect the journey from current run data and its generated manifest:

```text
PDF/source identity
  -> Extraction review: PROPOSED (or verified HUMAN_APPROVED on a later rebuild)
  -> KC reviews: PROPOSED; upstream PROPOSED_DEMO_ONLY in the default journey
  -> Quiz review: EXPERIMENTAL_UNAPPROVED
  -> Mastery: NOT_IMPLEMENTED, displayed only as an explicit boundary
```

Inspect `<fresh-portal-dir>/showcase-manifest.json` and verify:

- `source_run`, source filename, source ID, and page count come from this run;
- every stage label matches the actual artifact metadata;
- `quiz_initial_check` matches the bound report (or clearly states missing/stale), separately from
  approval, and the Quiz view displays both hint controls and review findings;
- every entrypoint exists and opens the matching current-run review;
- the page inventory is derived from the manifest rather than a fixed count;
- no stale course title, run name, content string, page number, or KC ID is embedded in the shell;
- Mastery has no link or claim that implies implementation.

Treat the manifest as an allowlist. The local package may contain only:

- the connected static portal and selected review HTML/CSS/JS;
- rendered page images those UIs require;
- semantic JSON required by the selected UIs and declared by the builder;
- redacted metrics declared by the builder.

It must never contain:

- this Agent Skill or executable pipeline code;
- `.env*`, credentials, or Vercel secrets;
- source PDF/PPTX files;
- raw agent candidates, provider envelopes, checkpoints, response IDs, or task/prompt packages;
- private answer-material companion files from `agent-session/review-materials/`;
- reviewer identity/notes unless explicitly required;
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

## Publish to Vercel only with separate authorization

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
