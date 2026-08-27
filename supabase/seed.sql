insert into public.review_runs (
  id,
  source_id,
  source_filename,
  source_sha256,
  is_public,
  review_open,
  metadata
)
values (
  'day01-llm-foundation-agent-v1',
  'sha256:200102b98e030130',
  'day01-llm-foundation.pdf',
  '200102b98e030130340461201e2aaf3d3955d0e39eed9eadd5bf8a25da24474d',
  true,
  true,
  '{"pipeline":"PDF -> Extraction -> KC -> Quiz","page_count":78,"leaf_kc_count":41,"quiz_question_count":82}'::jsonb
)
on conflict (id) do update set
  source_id = excluded.source_id,
  source_filename = excluded.source_filename,
  source_sha256 = excluded.source_sha256,
  is_public = excluded.is_public,
  review_open = excluded.review_open,
  metadata = excluded.metadata;
