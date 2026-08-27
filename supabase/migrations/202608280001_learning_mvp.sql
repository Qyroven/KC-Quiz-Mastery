-- Learning MVP: registered authoring snapshots, private learner evidence, and
-- deterministic / explicitly allowlisted human grading. No model-provider calls.
-- The review portal already publishes answer keys: this is not an exam boundary.
-- Register items / staff using a trusted operator connection, never a browser key.

create table public.learning_items (
  run_id text not null references public.review_runs(id) on delete restrict,
  question_id text not null check (char_length(question_id) between 1 and 160),
  question_sha256 text not null check (question_sha256 ~ '^[0-9a-f]{64}$'),
  kc_id text not null check (char_length(kc_id) between 1 and 160),
  slot_id text check (slot_id is null or char_length(slot_id) between 1 and 160),
  initial_check_status text not null check (
    initial_check_status in ('PASS', 'REVIEW', 'REJECT', 'UNCHECKED', 'STALE')
  ),
  question_payload jsonb not null check (
    jsonb_typeof(question_payload) = 'object'
    and octet_length(question_payload::text) <= 262144
  ),
  lineage jsonb not null check (
    jsonb_typeof(lineage) = 'object' and octet_length(lineage::text) <= 131072
  ),
  created_at timestamptz not null default now(),
  primary key (run_id, question_id, question_sha256)
);

create table public.learning_staff (
  user_id uuid primary key references public.reviewer_profiles(user_id) on delete restrict,
  enabled boolean not null default true,
  note text check (note is null or char_length(note) <= 2000),
  granted_at timestamptz not null default now()
);

create table public.learning_attempts (
  attempt_id uuid primary key,
  learner_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  run_id text not null,
  question_id text not null,
  question_sha256 text not null,
  kc_id text not null,
  slot_id text,
  started_at timestamptz not null default clock_timestamp(),
  submitted_at timestamptz,
  graded_at timestamptz,
  status text not null default 'in_progress'
    check (status in ('in_progress', 'pending_grade', 'graded')),
  response jsonb,
  hint_ids text[] not null default '{}',
  is_repeat boolean not null,
  score numeric,
  max_score numeric,
  correct boolean,
  grading_method text not null default 'pending'
    check (grading_method in ('exact', 'rubric_human', 'pending')),
  grading_version text not null check (grading_version in ('exact-v1', 'rubric-human-v1')),
  policy_version text not null default 'evidence-rules.v1'
    check (policy_version = 'evidence-rules.v1'),
  quality_status_at_start text not null,
  graded_by uuid references public.reviewer_profiles(user_id) on delete restrict,
  rubric_scores jsonb,
  grading_note text check (grading_note is null or char_length(grading_note) <= 2000),
  foreign key (run_id, question_id, question_sha256)
    references public.learning_items(run_id, question_id, question_sha256) on delete restrict,
  check (response is null or (jsonb_typeof(response) = 'object'
    and octet_length(response::text) <= 16384)),
  check (
    (status = 'in_progress' and response is null and submitted_at is null
      and score is null and max_score is null and correct is null and graded_at is null)
    or (status = 'pending_grade' and response is not null and submitted_at is not null
      and score is null and max_score > 0 and correct is null and graded_at is null
      and grading_method = 'pending')
    or (status = 'graded' and response is not null and submitted_at is not null
      and score is not null and max_score > 0 and score between 0 and max_score
      and correct is not null and correct = (score = max_score) and graded_at is not null
      and grading_method in ('exact', 'rubric_human'))
  )
);

create unique index learning_attempts_one_active_item
  on public.learning_attempts(learner_id, run_id, question_id)
  where status = 'in_progress';
create index learning_attempts_owner_run on public.learning_attempts(learner_id, run_id, started_at);
create index learning_attempts_grading_queue on public.learning_attempts(run_id, submitted_at)
  where status = 'pending_grade';

create table public.learning_events (
  event_id uuid primary key default gen_random_uuid(),
  learner_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  actor_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  run_id text not null,
  question_id text not null,
  question_sha256 text not null,
  attempt_id uuid references public.learning_attempts(attempt_id) on delete restrict,
  kind text not null check (kind in ('start', 'hint', 'submit', 'manual_grade', 'feedback')),
  payload jsonb not null check (
    jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 262144
  ),
  policy_version text not null default 'evidence-rules.v1'
    check (policy_version = 'evidence-rules.v1'),
  created_at timestamptz not null default clock_timestamp(),
  foreign key (run_id, question_id, question_sha256)
    references public.learning_items(run_id, question_id, question_sha256) on delete restrict
);
create index learning_events_actor_recent on public.learning_events(actor_id, created_at);
create index learning_events_owner_run on public.learning_events(learner_id, run_id, created_at);

-- Only these four response keys are accepted. Omitted keys normalize to the
-- canonical empty representation; supplied nulls, foreign IDs, and duplicates do not.
create function public.learning_normalize_response(p_question jsonb, p_response jsonb)
returns jsonb language plpgsql immutable set search_path = '' as $$
declare
  result jsonb;
  interaction text := p_question ->> 'interaction';
  value jsonb;
  ids text[];
  expected_count integer;
begin
  if p_response is null or jsonb_typeof(p_response) <> 'object'
    or octet_length(p_response::text) > 16384
    or p_response - array['selection_ids', 'ordering', 'mappings', 'text'] <> '{}'::jsonb
  then raise exception 'invalid response object or response exceeds 16 KiB'; end if;
  result := jsonb_build_object(
    'selection_ids', coalesce(p_response -> 'selection_ids', '[]'::jsonb),
    'ordering', coalesce(p_response -> 'ordering', '[]'::jsonb),
    'mappings', coalesce(p_response -> 'mappings', '[]'::jsonb),
    'text', coalesce(p_response -> 'text', '""'::jsonb)
  );
  if jsonb_typeof(result -> 'selection_ids') <> 'array'
    or jsonb_typeof(result -> 'ordering') <> 'array'
    or jsonb_typeof(result -> 'mappings') <> 'array'
    or jsonb_typeof(result -> 'text') <> 'string'
  then raise exception 'response fields have invalid types'; end if;
  if jsonb_array_length(result -> 'selection_ids') > 200
    or jsonb_array_length(result -> 'ordering') > 200
    or jsonb_array_length(result -> 'mappings') > 200
    or char_length(result ->> 'text') > 8000
  then raise exception 'response field limit exceeded'; end if;

  for value in select x.value from jsonb_array_elements(
    (result -> 'selection_ids') || (result -> 'ordering')
  ) x loop
    if jsonb_typeof(value) <> 'string' or char_length(value #>> '{}') not between 1 and 160
    then raise exception 'response option IDs must be strings'; end if;
  end loop;
  for value in select x.value from jsonb_array_elements(result -> 'mappings') x loop
    if jsonb_typeof(value) <> 'object' or not (value ?& array['left', 'right'])
      or value - array['left', 'right'] <> '{}'::jsonb
      or jsonb_typeof(value -> 'left') <> 'string' or jsonb_typeof(value -> 'right') <> 'string'
      or char_length(value ->> 'left') not between 1 and 160
      or char_length(value ->> 'right') not between 1 and 160
    then raise exception 'invalid response mapping'; end if;
  end loop;

  if interaction in ('single_select', 'multi_select') then
    if result -> 'ordering' <> '[]'::jsonb or result -> 'mappings' <> '[]'::jsonb
      or result ->> 'text' <> ''
    then raise exception 'response contains fields for another interaction'; end if;
    select array_agg(x.value) into ids from jsonb_array_elements_text(result -> 'selection_ids') x;
    if coalesce(cardinality(ids), 0) < 1 or (interaction = 'single_select' and cardinality(ids) <> 1)
      or cardinality(ids) <> (select count(distinct x) from unnest(ids) x)
      or exists (select 1 from unnest(ids) x where not exists (
        select 1 from jsonb_array_elements(p_question -> 'choice_options') o
        where o ->> 'option_id' = x
      ))
    then raise exception 'unknown, duplicate, or missing selection IDs'; end if;
  elsif interaction = 'ordering' then
    if result -> 'selection_ids' <> '[]'::jsonb or result -> 'mappings' <> '[]'::jsonb
      or result ->> 'text' <> ''
    then raise exception 'response contains fields for another interaction'; end if;
    select array_agg(x.value) into ids from jsonb_array_elements_text(result -> 'ordering') x;
    expected_count := jsonb_array_length(p_question -> 'ordering_options');
    if coalesce(cardinality(ids), 0) <> expected_count
      or cardinality(ids) <> (select count(distinct x) from unnest(ids) x)
      or exists (select 1 from unnest(ids) x where not exists (
        select 1 from jsonb_array_elements(p_question -> 'ordering_options') o
        where o ->> 'option_id' = x
      ))
    then raise exception 'ordering must contain each known option exactly once'; end if;
  elsif interaction = 'matching' then
    if result -> 'selection_ids' <> '[]'::jsonb or result -> 'ordering' <> '[]'::jsonb
      or result ->> 'text' <> ''
    then raise exception 'response contains fields for another interaction'; end if;
    expected_count := jsonb_array_length(p_question -> 'matching_left');
    if jsonb_array_length(result -> 'mappings') <> expected_count
      or (select count(distinct x ->> 'left') from jsonb_array_elements(result -> 'mappings') x) <> expected_count
      or exists (select 1 from jsonb_array_elements(result -> 'mappings') x where
        not exists (select 1 from jsonb_array_elements(p_question -> 'matching_left') o
          where o ->> 'option_id' = x ->> 'left')
        or not exists (select 1 from jsonb_array_elements(p_question -> 'matching_right') o
          where o ->> 'option_id' = x ->> 'right'))
    then raise exception 'matching must map every known left ID once to a known right ID'; end if;
    -- Multiple left options may legitimately use the same right option.
  elsif interaction = 'short_text' then
    if result -> 'selection_ids' <> '[]'::jsonb or result -> 'ordering' <> '[]'::jsonb
      or result -> 'mappings' <> '[]'::jsonb or nullif(btrim(result ->> 'text'), '') is null
    then raise exception 'short_text requires only a nonblank text response'; end if;
  else raise exception 'unsupported interaction';
  end if;
  return result;
end;
$$;

create function public.validate_learning_item()
returns trigger language plpgsql set search_path = '' as $$
declare
  field text;
  entry jsonb;
  options jsonb;
begin
  if not public.review_payload_is_valid('quiz', 'question', new.question_payload)
    or new.question_payload ->> 'question_id' is distinct from new.question_id
    or new.question_payload ->> 'kc_id' is distinct from new.kc_id
    or new.question_payload ->> 'slot_id' is distinct from new.slot_id
  then raise exception 'learning item does not match its frozen question identity'; end if;
  foreach field in array array['choice_options', 'matching_left', 'matching_right', 'ordering_options'] loop
    options := new.question_payload -> field;
    if jsonb_array_length(options) > 200
      or (select count(distinct x ->> 'option_id') from jsonb_array_elements(options) x) <> jsonb_array_length(options)
    then raise exception 'invalid registered option count or duplicate IDs'; end if;
    for entry in select x.value from jsonb_array_elements(options) x loop
      if jsonb_typeof(entry -> 'option_id') is distinct from 'string'
        or char_length(entry ->> 'option_id') not between 1 and 160
      then raise exception 'invalid registered option ID'; end if;
    end loop;
  end loop;
  perform public.learning_normalize_response(new.question_payload, new.question_payload -> 'correct_answer');
  if (new.question_payload ->> 'interaction' = 'single_select'
      and jsonb_array_length(new.question_payload -> 'choice_options') <> 4)
    or (new.question_payload ->> 'interaction' = 'multi_select'
      and (jsonb_array_length(new.question_payload -> 'choice_options') < 4
        or jsonb_array_length(new.question_payload -> 'correct_answer' -> 'selection_ids') < 2))
    or (new.question_payload ->> 'interaction' = 'ordering'
      and jsonb_array_length(new.question_payload -> 'ordering_options') < 3)
    or (new.question_payload ->> 'interaction' = 'matching'
      and (jsonb_array_length(new.question_payload -> 'matching_left') < 3
        or jsonb_array_length(new.question_payload -> 'matching_right') < 3))
  then raise exception 'registered question has an invalid interaction shape'; end if;
  if new.question_payload ->> 'interaction' = 'short_text' then
    if jsonb_array_length(new.question_payload -> 'rubric') not between 1 and 100
    then raise exception 'short_text requires a bounded nonempty rubric'; end if;
    for entry in select x.value from jsonb_array_elements(new.question_payload -> 'rubric') x loop
      if jsonb_typeof(entry -> 'points') is distinct from 'number'
        or jsonb_typeof(entry -> 'criterion') is distinct from 'string'
        or nullif(btrim(entry ->> 'criterion'), '') is null
      then raise exception 'invalid rubric criterion'; end if;
      if (entry ->> 'points')::numeric not between 1 and 1000000
        or trunc((entry ->> 'points')::numeric) <> (entry ->> 'points')::numeric
      then raise exception 'rubric points must be bounded positive integers'; end if;
    end loop;
  elsif new.question_payload -> 'rubric' <> '[]'::jsonb then
    raise exception 'objective items must not have a human rubric';
  end if;
  if jsonb_typeof(coalesce(new.question_payload -> 'hints', '[]'::jsonb)) <> 'array'
  then raise exception 'invalid hints'; end if;
  options := coalesce(new.question_payload -> 'hints', '[]'::jsonb);
  if jsonb_array_length(options) > 100
    or (select count(distinct x ->> 'hint_id') from jsonb_array_elements(options) x) <> jsonb_array_length(options)
  then raise exception 'invalid registered hints'; end if;
  for entry in select x.value from jsonb_array_elements(options) x loop
    if jsonb_typeof(entry -> 'hint_id') is distinct from 'string'
      or char_length(entry ->> 'hint_id') not between 1 and 160
      or jsonb_typeof(entry -> 'text') is distinct from 'string'
      or nullif(btrim(entry ->> 'text'), '') is null
    then raise exception 'invalid registered hint'; end if;
  end loop;
  foreach field in array array['source_sha256', 'extraction_sha256', 'kc_set_sha256', 'quiz_sha256'] loop
    if jsonb_typeof(new.lineage -> field) is distinct from 'string'
      or not (new.lineage ->> field ~ '^[0-9a-f]{64}$')
    then raise exception 'missing or invalid lineage hash: %', field; end if;
  end loop;
  if not (new.lineage ? 'authoring_context_sha256')
    or (new.lineage -> 'authoring_context_sha256' <> 'null'::jsonb and (
      jsonb_typeof(new.lineage -> 'authoring_context_sha256') <> 'string'
      or not (new.lineage ->> 'authoring_context_sha256' ~ '^[0-9a-f]{64}$')))
    or new.lineage ->> 'policy_version' is distinct from 'evidence-rules.v1'
    or new.lineage ->> 'source_sha256' is distinct from (
      select r.source_sha256 from public.review_runs r where r.id = new.run_id)
  then raise exception 'lineage source, context, or policy is invalid'; end if;
  if jsonb_typeof(new.lineage -> 'review_targets') is distinct from 'array'
  then raise exception 'lineage must contain review_targets'; end if;
  if jsonb_array_length(new.lineage -> 'review_targets') not between 2 and 500
  then raise exception 'invalid review target count'; end if;
  for entry in select x.value from jsonb_array_elements(new.lineage -> 'review_targets') x loop
    if not exists (select 1 from public.review_targets t where t.run_id = new.run_id
      and t.stage = entry ->> 'stage' and t.item_type = entry ->> 'item_type'
      and t.item_key = entry ->> 'item_key'
      and t.base_artifact_sha256 = entry ->> 'base_artifact_sha256')
    then raise exception 'lineage review target is not registered at this baseline'; end if;
  end loop;
  if not (new.lineage -> 'review_targets' @> jsonb_build_array(jsonb_build_object(
      'stage', 'quiz', 'item_type', 'question', 'item_key', new.question_id,
      'base_artifact_sha256', new.question_sha256)))
    or not (new.lineage -> 'review_targets' @> jsonb_build_array(jsonb_build_object(
      'stage', 'kc', 'item_type', 'leaf_kc', 'item_key', new.kc_id)))
  then raise exception 'lineage must bind the exact question and its KC'; end if;
  return new;
end;
$$;
create trigger learning_item_validated before insert on public.learning_items
  for each row execute function public.validate_learning_item();

create function public.reject_learning_mutation()
returns trigger language plpgsql set search_path = '' as $$
begin raise exception '% is immutable / append-only', tg_table_name; end;
$$;
create trigger learning_items_immutable before update or delete on public.learning_items
  for each row execute function public.reject_learning_mutation();
create trigger learning_events_append_only before update or delete on public.learning_events
  for each row execute function public.reject_learning_mutation();

create function public.guard_learning_attempt_update()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then raise exception 'learning attempts cannot be deleted'; end if;
  if (to_jsonb(new) - array['response', 'hint_ids', 'status', 'submitted_at', 'graded_at',
      'score', 'max_score', 'correct', 'grading_method', 'graded_by', 'rubric_scores', 'grading_note'])
    is distinct from (to_jsonb(old) - array['response', 'hint_ids', 'status', 'submitted_at', 'graded_at',
      'score', 'max_score', 'correct', 'grading_method', 'graded_by', 'rubric_scores', 'grading_note'])
    or old.status = 'graded'
    or (old.status <> 'in_progress' and (
      new.response is distinct from old.response or new.hint_ids is distinct from old.hint_ids
      or new.submitted_at is distinct from old.submitted_at or new.status <> 'graded'))
  then raise exception 'attempt identity, submitted response, and completed grades are immutable'; end if;
  return new;
end;
$$;
create trigger learning_attempt_guard before update or delete on public.learning_attempts
  for each row execute function public.guard_learning_attempt_update();

-- Mutators lock the caller profile first, serializing idempotency/rate checks and
-- starts across tabs. Attempt row locks serialize hints, submission and grading.
create function public.learning_require_actor(p_lock boolean default false)
returns uuid language plpgsql security definer set search_path = '' as $$
declare actor uuid := auth.uid(); profile_id uuid;
begin
  if actor is null then raise exception 'an authenticated learner session is required'; end if;
  if p_lock then
    select p.user_id into profile_id from public.reviewer_profiles p where p.user_id = actor for update;
  else
    select p.user_id into profile_id from public.reviewer_profiles p where p.user_id = actor;
  end if;
  if profile_id is null then raise exception 'learner display name is required'; end if;
  return actor;
end;
$$;
create function public.learning_check_rate(p_actor uuid)
returns void language plpgsql security definer set search_path = '' as $$
begin
  if (select count(*) from public.learning_events e where e.actor_id = p_actor
    and e.created_at > clock_timestamp() - interval '5 minutes') >= 120
  then raise exception 'too many learning actions; try again in a few minutes'; end if;
end;
$$;

-- A later edit/rejection/rebase invalidates old evidence, including on reads.
-- Likes/dislikes are deliberately absent from this policy and from all graders.
create function public.learning_item_quality(p_item public.learning_items)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare reasons text[] := '{}'; quality text := p_item.initial_check_status;
begin
  if quality <> 'PASS' then reasons := array_append(reasons, 'initial_check_not_pass'); end if;
  if exists (
    select 1 from jsonb_array_elements(p_item.lineage -> 'review_targets') ref
    where not exists (select 1 from public.review_targets t where t.run_id = p_item.run_id
      and t.stage = ref ->> 'stage' and t.item_type = ref ->> 'item_type'
      and t.item_key = ref ->> 'item_key'
      and t.base_artifact_sha256 = ref ->> 'base_artifact_sha256')
    or exists (select 1 from public.review_events e where e.run_id = p_item.run_id
      and e.stage = ref ->> 'stage' and e.item_type = ref ->> 'item_type'
      and e.item_key = ref ->> 'item_key' and e.action in ('edit', 'reject'))
  ) then
    quality := 'STALE'; reasons := array_append(reasons, 'content_review_changed');
  end if;
  return jsonb_build_object('quality_status', quality, 'exclusion_reasons', to_jsonb(reasons));
end;
$$;

create function public.learning_attempt_json(p_attempt public.learning_attempts)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare item public.learning_items%rowtype; quality jsonb; reasons jsonb;
begin
  select i.* into strict item from public.learning_items i where i.run_id = p_attempt.run_id
    and i.question_id = p_attempt.question_id and i.question_sha256 = p_attempt.question_sha256;
  quality := public.learning_item_quality(item);
  reasons := quality -> 'exclusion_reasons';
  if p_attempt.is_repeat then reasons := reasons || '"repeated_question"'::jsonb; end if;
  if p_attempt.status <> 'graded' then reasons := reasons || '"not_graded"'::jsonb; end if;
  return to_jsonb(p_attempt) || quality || jsonb_build_object(
    'initial_check_status', item.initial_check_status, 'lineage', item.lineage,
    'exclusion_reasons', reasons,
    'evidence_eligible', p_attempt.status = 'graded' and jsonb_array_length(reasons) = 0
  );
end;
$$;

create function public.get_learning_state(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(); result jsonb;
begin
  if not exists (select 1 from public.review_runs r where r.id = p_run_id and r.is_public)
  then raise exception 'learning run unavailable'; end if;
  select jsonb_build_object(
    'attempts', coalesce((select jsonb_agg(public.learning_attempt_json(a) order by a.started_at, a.attempt_id)
      from public.learning_attempts a where a.learner_id = actor and a.run_id = p_run_id), '[]'::jsonb),
    'feedback', coalesce((select jsonb_agg(to_jsonb(e) order by e.created_at, e.event_id)
      from public.learning_events e where e.learner_id = actor and e.run_id = p_run_id
        and e.kind = 'feedback'), '[]'::jsonb),
    'item_quality', coalesce((select jsonb_object_agg((picked.item).question_id,
        public.learning_item_quality(picked.item) || jsonb_build_object(
          'question_sha256', (picked.item).question_sha256,
          'initial_check_status', (picked.item).initial_check_status))
      from (select distinct on (i.question_id) i as item
        from public.learning_items i left join public.review_targets t
          on t.run_id = i.run_id and t.stage = 'quiz' and t.item_type = 'question'
            and t.item_key = i.question_id
        where i.run_id = p_run_id
        order by i.question_id, (i.question_sha256 = t.base_artifact_sha256) desc nulls last,
          i.created_at desc, i.question_sha256) picked), '{}'::jsonb),
    'can_grade', exists (select 1 from public.learning_staff s where s.user_id = actor and s.enabled),
    'policy_version', 'evidence-rules.v1'
  ) into result;
  return result;
end;
$$;

create function public.start_learning_attempt(
  p_run_id text, p_question_id text, p_question_sha256 text, p_attempt_id uuid
)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); item public.learning_items%rowtype;
  attempt public.learning_attempts%rowtype; quality jsonb; repeated boolean;
begin
  if p_attempt_id is null then raise exception 'attempt ID is required'; end if;
  select i.* into item from public.learning_items i join public.review_runs r on r.id = i.run_id
    where i.run_id = p_run_id and i.question_id = p_question_id
      and i.question_sha256 = p_question_sha256 and r.is_public;
  if not found then raise exception 'registered learning item unavailable'; end if;
  select a.* into attempt from public.learning_attempts a where a.attempt_id = p_attempt_id for update;
  if found then
    if attempt.learner_id <> actor or attempt.run_id <> p_run_id
      or attempt.question_id <> p_question_id or attempt.question_sha256 <> p_question_sha256
    then raise exception 'attempt ID already used'; end if;
    return public.learning_attempt_json(attempt);
  end if;
  select a.* into attempt from public.learning_attempts a where a.learner_id = actor
    and a.run_id = p_run_id and a.question_id = p_question_id and a.status = 'in_progress' for update;
  if found then
    if attempt.question_sha256 <> p_question_sha256
    then raise exception 'an earlier version of this question is still in progress'; end if;
    return public.learning_attempt_json(attempt);
  end if;
  perform public.learning_check_rate(actor);
  if (select count(*) from public.learning_attempts a where a.learner_id = actor and a.run_id = p_run_id) >= 1000
  then raise exception 'learning run attempt limit reached'; end if;
  repeated := exists (select 1 from public.learning_attempts a where a.learner_id = actor
    and a.run_id = p_run_id and a.question_id = p_question_id);
  quality := public.learning_item_quality(item);
  insert into public.learning_attempts(attempt_id, learner_id, run_id, question_id, question_sha256,
    kc_id, slot_id, is_repeat, grading_version, quality_status_at_start)
  values (p_attempt_id, actor, p_run_id, p_question_id, p_question_sha256, item.kc_id, item.slot_id,
    repeated, case when item.question_payload ->> 'interaction' = 'short_text'
      then 'rubric-human-v1' else 'exact-v1' end, quality ->> 'quality_status') returning * into attempt;
  insert into public.learning_events(learner_id, actor_id, run_id, question_id, question_sha256, attempt_id, kind, payload)
  values (actor, actor, p_run_id, p_question_id, p_question_sha256, p_attempt_id, 'start',
    jsonb_build_object('is_repeat', repeated, 'quality', quality, 'lineage', item.lineage));
  return public.learning_attempt_json(attempt);
end;
$$;

create function public.reveal_learning_hint(p_attempt_id uuid, p_hint_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); attempt public.learning_attempts%rowtype;
  item public.learning_items%rowtype;
begin
  select a.* into attempt from public.learning_attempts a join public.review_runs r on r.id = a.run_id
    where a.attempt_id = p_attempt_id and a.learner_id = actor and r.is_public for update of a;
  if not found then raise exception 'attempt unavailable'; end if;
  if attempt.status <> 'in_progress' then raise exception 'hints cannot be revealed after submission'; end if;
  select i.* into item from public.learning_items i where i.run_id = attempt.run_id
    and i.question_id = attempt.question_id and i.question_sha256 = attempt.question_sha256;
  if p_hint_id is null or char_length(p_hint_id) not between 1 and 160 or not exists (
    select 1 from jsonb_array_elements(coalesce(item.question_payload -> 'hints', '[]'::jsonb)) h
    where h ->> 'hint_id' = p_hint_id)
  then raise exception 'hint is not registered for this question'; end if;
  if p_hint_id = any(attempt.hint_ids) then return public.learning_attempt_json(attempt); end if;
  perform public.learning_check_rate(actor);
  update public.learning_attempts set hint_ids = array_append(hint_ids, p_hint_id)
    where attempt_id = p_attempt_id returning * into attempt;
  insert into public.learning_events(learner_id, actor_id, run_id, question_id, question_sha256, attempt_id, kind, payload)
  values (actor, actor, attempt.run_id, attempt.question_id, attempt.question_sha256, p_attempt_id, 'hint',
    jsonb_build_object('hint_id', p_hint_id));
  return public.learning_attempt_json(attempt);
end;
$$;

create function public.submit_learning_attempt(p_attempt_id uuid, p_response jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); attempt public.learning_attempts%rowtype;
  item public.learning_items%rowtype; response_value jsonb; interaction text;
  earned numeric; maximum numeric; is_correct boolean; grade_method text;
  submitted_time timestamptz;
begin
  select a.* into attempt from public.learning_attempts a join public.review_runs r on r.id = a.run_id
    where a.attempt_id = p_attempt_id and a.learner_id = actor and r.is_public for update of a;
  if not found then raise exception 'attempt unavailable'; end if;
  select i.* into item from public.learning_items i where i.run_id = attempt.run_id
    and i.question_id = attempt.question_id and i.question_sha256 = attempt.question_sha256;
  response_value := public.learning_normalize_response(item.question_payload, p_response);
  if attempt.status <> 'in_progress' then
    if attempt.response is distinct from response_value then raise exception 'a submitted response cannot be changed'; end if;
    return public.learning_attempt_json(attempt);
  end if;
  perform public.learning_check_rate(actor);
  interaction := item.question_payload ->> 'interaction';
  if interaction = 'short_text' then
    select sum((r ->> 'points')::numeric) into maximum from jsonb_array_elements(item.question_payload -> 'rubric') r;
    earned := null; is_correct := null; grade_method := 'pending';
  else
    maximum := 1; grade_method := 'exact';
    if interaction = 'single_select' then
      is_correct := response_value -> 'selection_ids' = item.question_payload -> 'correct_answer' -> 'selection_ids';
    elsif interaction = 'multi_select' then
      is_correct := (response_value -> 'selection_ids') @> (item.question_payload -> 'correct_answer' -> 'selection_ids')
        and (response_value -> 'selection_ids') <@ (item.question_payload -> 'correct_answer' -> 'selection_ids');
    elsif interaction = 'ordering' then
      is_correct := response_value -> 'ordering' = item.question_payload -> 'correct_answer' -> 'ordering';
    elsif interaction = 'matching' then
      maximum := jsonb_array_length(item.question_payload -> 'matching_left');
      select count(*) into earned from jsonb_array_elements(response_value -> 'mappings') response_pair
        join jsonb_array_elements(item.question_payload -> 'correct_answer' -> 'mappings') key_pair
        on response_pair ->> 'left' = key_pair ->> 'left' and response_pair ->> 'right' = key_pair ->> 'right';
      is_correct := earned = maximum;
    end if;
    if interaction <> 'matching' then earned := case when is_correct then 1 else 0 end; end if;
  end if;
  submitted_time := clock_timestamp();
  update public.learning_attempts set response = response_value, submitted_at = submitted_time,
    status = case when interaction = 'short_text' then 'pending_grade' else 'graded' end,
    score = earned, max_score = maximum, correct = is_correct, grading_method = grade_method,
    graded_at = case when interaction = 'short_text' then null else submitted_time end
    where attempt_id = p_attempt_id returning * into attempt;
  insert into public.learning_events(learner_id, actor_id, run_id, question_id, question_sha256, attempt_id, kind, payload)
  values (actor, actor, attempt.run_id, attempt.question_id, attempt.question_sha256, p_attempt_id, 'submit',
    jsonb_build_object('response', response_value, 'hint_ids', attempt.hint_ids, 'score', earned,
      'max_score', maximum, 'correct', is_correct, 'grading_method', grade_method,
      'grading_version', attempt.grading_version, 'quality', public.learning_item_quality(item)));
  return public.learning_attempt_json(attempt);
end;
$$;

create function public.append_learning_feedback(
  p_run_id text, p_question_id text, p_question_sha256 text, p_vote text,
  p_note text default null, p_attempt_id uuid default null, p_event_id uuid default null
)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); event public.learning_events%rowtype;
  event_payload jsonb := jsonb_build_object('vote', p_vote, 'note', nullif(btrim(p_note), ''));
begin
  if p_event_id is null or p_vote is null or p_vote not in ('like', 'dislike')
    or (p_note is not null and char_length(p_note) > 2000)
  then raise exception 'feedback requires an event ID, like/dislike, and a note of at most 2000 characters'; end if;
  if not exists (select 1 from public.learning_items i join public.review_runs r on r.id = i.run_id
    where i.run_id = p_run_id and i.question_id = p_question_id and i.question_sha256 = p_question_sha256 and r.is_public)
  then raise exception 'registered learning item unavailable'; end if;
  if p_attempt_id is not null and not exists (select 1 from public.learning_attempts a
    where a.attempt_id = p_attempt_id and a.learner_id = actor and a.run_id = p_run_id
      and a.question_id = p_question_id and a.question_sha256 = p_question_sha256)
  then raise exception 'feedback attempt unavailable'; end if;
  select e.* into event from public.learning_events e where e.event_id = p_event_id;
  if found then
    if event.actor_id <> actor or event.kind <> 'feedback' or event.run_id <> p_run_id
      or event.question_id <> p_question_id or event.question_sha256 <> p_question_sha256
      or event.attempt_id is distinct from p_attempt_id or event.payload is distinct from event_payload
    then raise exception 'event ID already used'; end if;
    return to_jsonb(event);
  end if;
  perform public.learning_check_rate(actor);
  if (select count(*) from public.learning_events e where e.learner_id = actor and e.run_id = p_run_id
    and e.kind = 'feedback') >= 1000 then raise exception 'learning run feedback limit reached'; end if;
  insert into public.learning_events(event_id, learner_id, actor_id, run_id, question_id,
    question_sha256, attempt_id, kind, payload)
  values (p_event_id, actor, actor, p_run_id, p_question_id, p_question_sha256, p_attempt_id, 'feedback', event_payload)
  returning * into event;
  return to_jsonb(event);
end;
$$;

create function public.get_learning_grading_queue(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(); result jsonb;
begin
  if not exists (select 1 from public.learning_staff s where s.user_id = actor and s.enabled)
  then raise exception 'trusted grader authorization required'; end if;
  if not exists (select 1 from public.review_runs r where r.id = p_run_id and r.is_public)
  then raise exception 'learning run unavailable'; end if;
  select coalesce(jsonb_agg(queued.entry order by queued.submitted_at, queued.attempt_id), '[]'::jsonb) into result
  from (select a.attempt_id, a.submitted_at, public.learning_attempt_json(a) || jsonb_build_object(
      'question_payload', i.question_payload, 'learner_name', p.display_name) as entry
    from public.learning_attempts a join public.learning_items i
      on (i.run_id, i.question_id, i.question_sha256) = (a.run_id, a.question_id, a.question_sha256)
    join public.reviewer_profiles p on p.user_id = a.learner_id
    where a.run_id = p_run_id and a.status = 'pending_grade' and a.learner_id <> actor
    order by a.submitted_at, a.attempt_id limit 100) queued;
  return result;
end;
$$;

create function public.grade_learning_attempt(
  p_attempt_id uuid, p_scores jsonb, p_note text default null, p_event_id uuid default null
)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); attempt public.learning_attempts%rowtype;
  item public.learning_items%rowtype; event public.learning_events%rowtype;
  index_value integer; earned numeric := 0; maximum numeric := 0; points numeric; allowed numeric;
  note_value text := nullif(btrim(p_note), ''); event_payload jsonb;
begin
  if not exists (select 1 from public.learning_staff s where s.user_id = actor and s.enabled)
  then raise exception 'trusted grader authorization required'; end if;
  if p_event_id is null or p_scores is null or jsonb_typeof(p_scores) <> 'array'
    or octet_length(p_scores::text) > 8192 or (p_note is not null and char_length(p_note) > 2000)
  then raise exception 'invalid grading event, score array, or note'; end if;
  select a.* into attempt from public.learning_attempts a join public.review_runs r on r.id = a.run_id
    where a.attempt_id = p_attempt_id and r.is_public for update of a;
  if not found then raise exception 'attempt unavailable'; end if;
  if attempt.learner_id = actor then raise exception 'a trusted grader cannot grade their own response'; end if;
  select i.* into item from public.learning_items i where i.run_id = attempt.run_id
    and i.question_id = attempt.question_id and i.question_sha256 = attempt.question_sha256;
  if item.question_payload ->> 'interaction' <> 'short_text'
    or jsonb_array_length(p_scores) <> jsonb_array_length(item.question_payload -> 'rubric')
  then raise exception 'one score per frozen short_text rubric criterion is required'; end if;
  for index_value in 0 .. jsonb_array_length(p_scores) - 1 loop
    if jsonb_typeof(p_scores -> index_value) <> 'number' then raise exception 'rubric scores must be numeric'; end if;
    points := (p_scores ->> index_value)::numeric;
    allowed := (item.question_payload -> 'rubric' -> index_value ->> 'points')::numeric;
    if points < 0 or points > allowed then raise exception 'rubric score outside authored criterion bounds'; end if;
    earned := earned + points; maximum := maximum + allowed;
  end loop;
  event_payload := jsonb_build_object('scores', p_scores, 'note', note_value, 'score', earned,
    'max_score', maximum, 'correct', earned = maximum, 'grading_version', 'rubric-human-v1');
  select e.* into event from public.learning_events e where e.event_id = p_event_id;
  if found then
    if event.actor_id <> actor or event.kind <> 'manual_grade' or event.attempt_id <> p_attempt_id
      or event.payload is distinct from event_payload
    then raise exception 'event ID already used'; end if;
    return public.learning_attempt_json(attempt);
  end if;
  if attempt.status <> 'pending_grade' then raise exception 'attempt is not pending a first human grade'; end if;
  perform public.learning_check_rate(actor);
  update public.learning_attempts set status = 'graded', score = earned, max_score = maximum,
    correct = earned = maximum, grading_method = 'rubric_human', graded_at = clock_timestamp(),
    graded_by = actor, rubric_scores = p_scores, grading_note = note_value
    where attempt_id = p_attempt_id returning * into attempt;
  insert into public.learning_events(event_id, learner_id, actor_id, run_id, question_id,
    question_sha256, attempt_id, kind, payload)
  values (p_event_id, attempt.learner_id, actor, attempt.run_id, attempt.question_id,
    attempt.question_sha256, p_attempt_id, 'manual_grade', event_payload);
  return public.learning_attempt_json(attempt);
end;
$$;

-- RLS is defense in depth; all browser reads/writes use the owner-checking RPCs.
-- Staff have no table-wide learner SELECT, only the authorized pending queue.
alter table public.learning_items enable row level security;
alter table public.learning_staff enable row level security;
alter table public.learning_attempts enable row level security;
alter table public.learning_events enable row level security;
revoke all on public.learning_items, public.learning_staff, public.learning_attempts, public.learning_events
  from public, anon, authenticated;
create policy learning_attempts_read_own on public.learning_attempts for select to authenticated
  using (learner_id = (select auth.uid()));
create policy learning_events_read_own on public.learning_events for select to authenticated
  using (learner_id = (select auth.uid()));
create policy learning_staff_read_own on public.learning_staff for select to authenticated
  using (user_id = (select auth.uid()));

revoke all on function public.learning_normalize_response(jsonb, jsonb),
  public.validate_learning_item(), public.reject_learning_mutation(),
  public.guard_learning_attempt_update(), public.learning_require_actor(boolean),
  public.learning_check_rate(uuid), public.learning_item_quality(public.learning_items),
  public.learning_attempt_json(public.learning_attempts)
  from public, anon, authenticated;
revoke all on function public.get_learning_state(text),
  public.start_learning_attempt(text, text, text, uuid), public.reveal_learning_hint(uuid, text),
  public.submit_learning_attempt(uuid, jsonb),
  public.append_learning_feedback(text, text, text, text, text, uuid, uuid),
  public.get_learning_grading_queue(text), public.grade_learning_attempt(uuid, jsonb, text, uuid)
  from public, anon, authenticated;
grant execute on function public.get_learning_state(text),
  public.start_learning_attempt(text, text, text, uuid), public.reveal_learning_hint(uuid, text),
  public.submit_learning_attempt(uuid, jsonb),
  public.append_learning_feedback(text, text, text, text, text, uuid, uuid),
  public.get_learning_grading_queue(text), public.grade_learning_attempt(uuid, jsonb, text, uuid)
  to authenticated;
