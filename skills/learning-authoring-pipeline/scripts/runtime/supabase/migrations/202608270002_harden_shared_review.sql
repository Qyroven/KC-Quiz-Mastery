-- Harden the public, name-only review flow without changing its UX.
-- All event writes go through append_review_event(); direct table inserts are revoked.

create table public.review_targets (
  run_id text not null references public.review_runs(id) on delete restrict,
  stage text not null check (stage in ('extraction', 'kc', 'quiz')),
  item_type text not null check (item_type ~ '^[a-z][a-z0-9_]*$'),
  item_key text not null check (char_length(item_key) between 1 and 160),
  identity_field text not null check (identity_field ~ '^[a-z][a-z0-9_]*$'),
  identity_value jsonb not null check (
    jsonb_typeof(identity_value) in ('string', 'number')
  ),
  base_artifact_sha256 text not null check (base_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  primary key (run_id, stage, item_type, item_key)
);

alter table public.review_targets enable row level security;
revoke all on public.review_targets from anon, authenticated;

alter table public.review_events
  add constraint review_events_registered_target_fk
  foreign key (run_id, stage, item_type, item_key)
  references public.review_targets(run_id, stage, item_type, item_key)
  on delete restrict;

create function public.prevent_review_run_identity_change()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.id is distinct from old.id
    or new.source_id is distinct from old.source_id
    or new.source_filename is distinct from old.source_filename
    or new.source_sha256 is distinct from old.source_sha256
  then
    raise exception 'review run source identity is immutable; publish changed source as a new run';
  end if;
  return new;
end;
$$;

create trigger review_run_identity_immutable
before update on public.review_runs
for each row execute function public.prevent_review_run_identity_change();

create function public.prevent_review_target_rebase()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (
    new.identity_field is distinct from old.identity_field
    or new.identity_value is distinct from old.identity_value
    or new.base_artifact_sha256 is distinct from old.base_artifact_sha256
  ) and exists (
    select 1
    from public.review_events event
    where event.run_id = old.run_id
      and event.stage = old.stage
      and event.item_type = old.item_type
      and event.item_key = old.item_key
  ) then
    raise exception 'review target already has history; publish changed output as a new run';
  end if;
  return new;
end;
$$;

create trigger review_target_rebase_guard
before update on public.review_targets
for each row execute function public.prevent_review_target_rebase();

create function public.review_payload_is_valid(
  p_stage text,
  p_item_type text,
  p_payload jsonb
)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
begin
  if jsonb_typeof(p_payload) <> 'object' then
    return false;
  end if;

  if p_stage = 'extraction' and p_item_type = 'page' then
    return coalesce((jsonb_typeof(p_payload -> 'page_number') = 'number'
      and jsonb_typeof(p_payload -> 'role') = 'string'
      and jsonb_typeof(p_payload -> 'blocks') = 'array'
      and jsonb_typeof(p_payload -> 'reading_order') = 'array'
      and jsonb_typeof(p_payload -> 'page_note') = 'object'
      and jsonb_typeof(p_payload -> 'warnings') = 'array'), false);
  end if;

  if p_stage = 'kc' and p_item_type = 'leaf_kc' then
    return coalesce((jsonb_typeof(p_payload -> 'kc_id') = 'string'
      and jsonb_typeof(p_payload -> 'group_id') = 'string'
      and jsonb_typeof(p_payload -> 'name') = 'string'
      and jsonb_typeof(p_payload -> 'semantic_form') = 'string'
      and jsonb_typeof(p_payload -> 'knowledge_description') = 'string'
      and jsonb_typeof(p_payload -> 'observable_claim') = 'string'
      and jsonb_typeof(p_payload -> 'source_evidence') = 'array'
      and jsonb_typeof(p_payload -> 'assessment_boundary') = 'object'
      and jsonb_typeof(p_payload -> 'assessment_boundary' -> 'included') = 'array'
      and jsonb_typeof(p_payload -> 'assessment_boundary' -> 'excluded') = 'array'
      and jsonb_typeof(p_payload -> 'status') = 'string'
      and jsonb_typeof(p_payload -> 'warning_codes') = 'array'), false);
  end if;

  if p_stage = 'kc' and p_item_type = 'page_audit' then
    return coalesce((jsonb_typeof(p_payload -> 'page') = 'number'
      and jsonb_typeof(p_payload -> 'classification') = 'string'
      and jsonb_typeof(p_payload -> 'summary') = 'string'
      and jsonb_typeof(p_payload -> 'source_block_ids') = 'array'
      and jsonb_typeof(p_payload -> 'kc_ids') = 'array'
      and jsonb_typeof(p_payload -> 'warning_codes') = 'array'), false);
  end if;

  if p_stage = 'quiz' and p_item_type = 'question' then
    return coalesce((jsonb_typeof(p_payload -> 'question_id') = 'string'
      and jsonb_typeof(p_payload -> 'kc_id') = 'string'
      and jsonb_typeof(p_payload -> 'group_id') = 'string'
      and jsonb_typeof(p_payload -> 'title') = 'string'
      and jsonb_typeof(p_payload -> 'interaction') = 'string'
      and jsonb_typeof(p_payload -> 'stimulus') = 'object'
      and jsonb_typeof(p_payload -> 'prompt') = 'string'
      and jsonb_typeof(p_payload -> 'choice_options') = 'array'
      and jsonb_typeof(p_payload -> 'matching_left') = 'array'
      and jsonb_typeof(p_payload -> 'matching_right') = 'array'
      and jsonb_typeof(p_payload -> 'ordering_options') = 'array'
      and jsonb_typeof(p_payload -> 'correct_answer') = 'object'
      and jsonb_typeof(p_payload -> 'rubric') = 'array'
      and jsonb_typeof(p_payload -> 'evidence_refs') = 'array'), false);
  end if;

  return false;
end;
$$;

revoke all on function public.review_payload_is_valid(text, text, jsonb)
from public, anon, authenticated;

drop policy if exists review_events_insert_own_on_open_run on public.review_events;
revoke select on public.review_events from anon, authenticated;
revoke insert on public.review_events from authenticated;

create function public.append_review_event(
  p_run_id text,
  p_stage text,
  p_item_type text,
  p_item_key text,
  p_action text,
  p_note text default null,
  p_revision_payload jsonb default null,
  p_expected_revision_id uuid default null
)
returns public.review_events
language plpgsql
security definer
set search_path = ''
as $$
declare
  caller_id uuid := auth.uid();
  target public.review_targets%rowtype;
  profile_name text;
  latest_revision public.review_events%rowtype;
  latest_revision_id uuid;
  effective_hash text;
  inserted public.review_events%rowtype;
begin
  if caller_id is null then
    raise exception 'an authenticated anonymous review session is required';
  end if;

  if p_action not in ('edit', 'approve', 'reject') then
    raise exception 'unsupported review action';
  end if;

  if p_note is not null and char_length(p_note) > 2000 then
    raise exception 'review note exceeds 2000 characters';
  end if;
  if p_action = 'reject' and nullif(btrim(p_note), '') is null then
    raise exception 'reject requires a note';
  end if;

  select registry.*
  into target
  from public.review_targets registry
  join public.review_runs run on run.id = registry.run_id
  where registry.run_id = p_run_id
    and registry.stage = p_stage
    and registry.item_type = p_item_type
    and registry.item_key = p_item_key
    and run.is_public
    and run.review_open
  for update of registry;

  if not found then
    raise exception 'review target is not registered or its run is closed';
  end if;

  select reviewer.display_name
  into profile_name
  from public.reviewer_profiles reviewer
  where reviewer.user_id = caller_id;

  if profile_name is null then
    raise exception 'reviewer display name is required';
  end if;

  if (
    select count(*)
    from public.review_events recent
    where recent.reviewer_id = caller_id
      and recent.created_at > now() - interval '5 minutes'
  ) >= 60 then
    raise exception 'too many review actions; try again in a few minutes';
  end if;

  select event.*
  into latest_revision
  from public.review_events event
  where event.run_id = p_run_id
    and event.stage = p_stage
    and event.item_type = p_item_type
    and event.item_key = p_item_key
    and event.action = 'edit'
    and event.base_artifact_sha256 = target.base_artifact_sha256
  order by event.created_at desc, event.id desc
  limit 1;

  if found then
    latest_revision_id := latest_revision.id;
  else
    latest_revision_id := null;
  end if;

  if latest_revision_id is distinct from p_expected_revision_id then
    raise exception 'stale revision; reload this item before reviewing it';
  end if;

  if p_action = 'edit' then
    if p_revision_payload is null
      or not public.review_payload_is_valid(p_stage, p_item_type, p_revision_payload)
    then
      raise exception 'revision payload does not match the registered stage schema';
    end if;
    if p_revision_payload -> target.identity_field is distinct from target.identity_value then
      raise exception 'revision payload changed its immutable identity';
    end if;
    if octet_length(p_revision_payload::text) > 262144 then
      raise exception 'revision payload exceeds 256 KiB';
    end if;
    effective_hash := encode(
      extensions.digest(convert_to(p_revision_payload::text, 'UTF8'), 'sha256'),
      'hex'
    );
  else
    if p_revision_payload is not null then
      raise exception 'approve and reject cannot include a revision payload';
    end if;
    effective_hash := coalesce(latest_revision.payload_sha256, target.base_artifact_sha256);
  end if;

  insert into public.review_events (
    run_id, stage, item_type, item_key, action,
    reviewer_id, reviewer_name, note, revision_payload,
    base_artifact_sha256, payload_sha256, target_revision_id
  ) values (
    p_run_id, p_stage, p_item_type, p_item_key, p_action,
    caller_id, profile_name, nullif(btrim(p_note), ''), p_revision_payload,
    target.base_artifact_sha256, effective_hash, latest_revision_id
  )
  returning * into inserted;

  return inserted;
end;
$$;

revoke all on function public.append_review_event(
  text, text, text, text, text, text, jsonb, uuid
) from public, anon;
grant execute on function public.append_review_event(
  text, text, text, text, text, text, jsonb, uuid
) to authenticated;

create function public.get_review_target_events(
  p_run_id text,
  p_stage text,
  p_item_type text,
  p_item_key text,
  p_base_artifact_sha256 text
)
returns setof public.review_events
language sql
stable
security definer
set search_path = ''
as $$
  with registered as (
    select target.base_artifact_sha256
    from public.review_targets target
    join public.review_runs run on run.id = target.run_id
    where target.run_id = p_run_id
      and target.stage = p_stage
      and target.item_type = p_item_type
      and target.item_key = p_item_key
      and target.base_artifact_sha256 = p_base_artifact_sha256
      and run.is_public
  ),
  latest_edit as (
    select event.*
    from public.review_events event, registered
    where event.run_id = p_run_id
      and event.stage = p_stage
      and event.item_type = p_item_type
      and event.item_key = p_item_key
      and event.base_artifact_sha256 = p_base_artifact_sha256
      and event.action = 'edit'
    order by event.created_at desc, event.id desc
    limit 1
  ),
  recent as (
    select event.*
    from public.review_events event, registered
    where event.run_id = p_run_id
      and event.stage = p_stage
      and event.item_type = p_item_type
      and event.item_key = p_item_key
      and event.base_artifact_sha256 = p_base_artifact_sha256
    order by event.created_at desc, event.id desc
    limit 100
  ),
  combined as (
    select * from recent
    union
    select * from latest_edit
  )
  select combined.*
  from combined
  order by combined.created_at desc, combined.id desc;
$$;

revoke all on function public.get_review_target_events(
  text, text, text, text, text
) from public;
grant execute on function public.get_review_target_events(
  text, text, text, text, text
) to anon, authenticated;
