# Review and static publishing

Read this reference only for human review, approval, or an explicitly requested publication.

## Review gates

The honest status vocabulary is:

- Extraction: `PROPOSED`, `HUMAN_APPROVED`, or blocked/review-needed.
- KC: `PROPOSED` and human-review-needed.
- Quiz: `EXPERIMENTAL_UNAPPROVED` and human-review-needed.
- Mastery: `NOT_IMPLEMENTED`.

Schema validation means the JSON matches the machine contract. Geometry/form audits are diagnostic.
Neither is proof of semantic correctness or learning value. Never use `validated`, `approved`, or
`production-ready` for an LLM artifact unless the matching human approval boundary exists.

For extraction approval, verify the review UI belongs to the same source hash and proposed artifact
the user reviewed. Run the CLI's `approve` command only on explicit instruction. Do not silently
acknowledge warnings.

KC has no machine approval command in this version. Record the user's candidate/KC selection as
Quiz runtime configuration; do not fabricate `kc-approved.json`.

Quiz UI Approve/Edit/Reject controls may be browser-local review notes. They never authorize source
mutation or create an approved Quiz artifact.

## Build a publish-safe showcase

Publishing is optional and separately authorized. Use the repository's deterministic showcase
builder from a checkout. Inspect its help because filenames and flags may evolve:

```bash
python scripts/publish_showcase.py --help
python scripts/publish_showcase.py \
  --run-dir <run-dir> \
  --output-dir <fresh-showcase-dir> \
  --extractor-review <review-file> \
  --kc-recall-review <review-file> \
  --kc-scroll-review <review-file> \
  --quiz-review <review-file>
```

If the skill is running only through `uvx` with no repository checkout, ask the user to clone the
repository before publishing. Do not improvise a deployment script.

Inspect `<fresh-showcase-dir>/showcase-manifest.json`. Treat it as an allowlist, then verify every
listed file exists. Publish only:

- the static portal and selected review HTML/CSS/JS;
- the rendered page images those UIs require;
- semantic JSON required by the selected UIs and declared by the builder;
- redacted metrics declared by the builder.

Never publish:

- this Agent Skill or executable pipeline code;
- `.env*`, credentials, or Vercel secrets;
- source PDF/PPTX files unless the user separately authorizes that exact file;
- raw agent candidates, provider envelopes, checkpoints, response IDs, or prompt/request packages;
- reviewer identity/notes unless explicitly required;
- unrelated historical runs.

Run offline tests/lint before deployment when operating in the repository. Deploy the fresh static
directory, never the repository root or run directory. Preview first unless the user's current
request explicitly authorizes Production for the exact directory, Vercel team, project, and
environment. Verify every manifest entrypoint at the immutable deployment URL.

Return the immutable URL, project URL when available, exact included entrypoints, exclusions, and
honest stage statuses. Vercel shows generated review results; it does not host or execute the skill.
