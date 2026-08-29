-- Two role-specific apps, one backend. Authoring drafts remain private to the
-- explicitly assigned course teachers. A human publication is a NEW immutable
-- run; it never rewrites a draft, an earlier release, or learner history.
-- Apply through an administrator connection. No role is granted by this file.

-- Review chronology must reflect the actual insertion after the course lock,
-- not the start time of a transaction that may have waited behind another edit.
-- Historical events remain untouched.
alter table public.review_events alter column created_at set default clock_timestamp();

create table public.learning_course_teachers (
  course_id text not null references public.review_runs(id) on delete restrict,
  user_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  enabled boolean not null default true,
  granted_at timestamptz not null default clock_timestamp(),
  note text check (note is null or char_length(note) <= 2000),
  primary key (course_id, user_id)
);

create table public.learning_authoring_packages (
  run_id text primary key references public.review_runs(id) on delete restrict,
  -- Hash of the operator's canonical UTF-8 JSON bytes, not PostgreSQL JSON text.
  package_sha256 text not null check (package_sha256 ~ '^[0-9a-f]{64}$'),
  package jsonb not null check (jsonb_typeof(package) = 'object'
    and octet_length(package::text) <= 33554432),
  created_at timestamptz not null default clock_timestamp()
);

create table public.learning_releases (
  release_id text primary key references public.review_runs(id) on delete restrict,
  course_id text not null references public.learning_authoring_packages(run_id) on delete restrict,
  label text not null check (label = btrim(label) and char_length(label) between 1 and 120),
  package jsonb not null check (jsonb_typeof(package) = 'object'),
  review_version text not null check (review_version ~ '^[0-9a-f]{64}$'),
  question_ids text[] not null check (cardinality(question_ids) > 0),
  published_by uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  publish_event_id uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  unique (course_id, release_id)
);

create table public.learning_enrollments (
  course_id text not null,
  release_id text not null,
  learner_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  enrolled_at timestamptz not null default clock_timestamp(),
  primary key (release_id, learner_id),
  foreign key (course_id, release_id)
    references public.learning_releases(course_id, release_id) on delete restrict
);
create index learning_enrollments_course_learner
  on public.learning_enrollments(course_id, learner_id, enrolled_at desc);

alter table public.learning_course_teachers enable row level security;
alter table public.learning_authoring_packages enable row level security;
alter table public.learning_releases enable row level security;
alter table public.learning_enrollments enable row level security;
revoke all on public.learning_course_teachers, public.learning_authoring_packages,
  public.learning_releases, public.learning_enrollments from public, anon, authenticated;

create trigger learning_authoring_packages_immutable
  before update or delete on public.learning_authoring_packages
  for each row execute function public.reject_learning_mutation();
create trigger learning_releases_immutable before update or delete on public.learning_releases
  for each row execute function public.reject_learning_mutation();
create trigger learning_enrollments_immutable before update or delete on public.learning_enrollments
  for each row execute function public.reject_learning_mutation();

create function public.learning_root_course(p_run_id text)
returns text language sql stable security definer set search_path = '' as $$
  select coalesce((select r.course_id from public.learning_releases r
    where r.release_id = p_run_id), p_run_id);
$$;

create function public.learning_is_course_teacher(p_run_id text)
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.learning_course_teachers t
    where t.course_id = public.learning_root_course(p_run_id)
      and t.user_id = auth.uid() and t.enabled);
$$;

create function public.learning_require_teacher(p_run_id text)
returns uuid language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor();
begin
  if not public.learning_is_course_teacher(p_run_id)
  then raise exception 'course teacher authorization required'; end if;
  return actor;
end;
$$;

create function public.get_teacher_access(p_run_id text)
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('user_id', auth.uid(),
    'course_id', public.learning_root_course(p_run_id),
    'can_teach', public.learning_is_course_teacher(p_run_id),
    'can_publish', public.learning_is_course_teacher(p_run_id),
    'can_grade', public.learning_is_course_teacher(p_run_id));
$$;

create function public.learning_require_run_access(p_run_id text)
returns uuid language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor();
begin
  if exists (select 1 from public.learning_releases r where r.release_id = p_run_id) then
    if not public.learning_is_course_teacher(p_run_id) and not exists (
      select 1 from public.learning_enrollments e
      where e.release_id = p_run_id and e.learner_id = actor)
    then raise exception 'enroll in this published course version first'; end if;
  elsif exists (select 1 from public.learning_authoring_packages p where p.run_id = p_run_id) then
    perform public.learning_require_teacher(p_run_id);
  end if;
  return actor;
end;
$$;

-- Public run metadata is not a back door to a protected draft. Released run
-- metadata contains no answer material. Legacy unregistered demo metadata stays
-- readable, but its review mutations now also require an explicit teacher grant.
drop policy if exists review_runs_public_read on public.review_runs;
-- RLS cannot directly read the private registry as an invoker. Use a definer
-- predicate rather than giving every browser SELECT on that registry.
create function public.learning_run_metadata_visible(p_run_id text)
returns boolean language sql stable security definer set search_path = '' as $$
  select not exists (select 1 from public.learning_authoring_packages p where p.run_id = p_run_id)
    or public.learning_is_course_teacher(p_run_id);
$$;
create policy review_runs_scoped_read on public.review_runs for select to anon, authenticated
  using (is_public and public.learning_run_metadata_visible(id));

alter function public.append_review_event(text,text,text,text,text,text,jsonb,uuid)
  rename to learning_append_review_event_v1;
create function public.append_review_event(
  p_run_id text, p_stage text, p_item_type text, p_item_key text, p_action text,
  p_note text default null, p_revision_payload jsonb default null,
  p_expected_revision_id uuid default null
)
returns public.review_events language plpgsql security definer set search_path = '' as $$
begin
  perform public.learning_require_teacher(p_run_id);
  if exists (select 1 from public.learning_releases r where r.release_id = p_run_id)
  then raise exception 'published versions are immutable; edit the authoring draft'; end if;
  -- Serialize all draft changes with publication, not only changes to one item.
  perform pg_advisory_xact_lock(hashtextextended(p_run_id, 0));
  return public.learning_append_review_event_v1(p_run_id,p_stage,p_item_type,p_item_key,
    p_action,p_note,p_revision_payload,p_expected_revision_id);
end;
$$;

alter function public.get_review_target_events(text,text,text,text,text)
  rename to learning_get_review_target_events_v1;
create function public.get_review_target_events(
  p_run_id text, p_stage text, p_item_type text, p_item_key text, p_base_artifact_sha256 text
)
returns setof public.review_events language plpgsql stable security definer set search_path = '' as $$
begin
  perform public.learning_require_teacher(p_run_id);
  return query select * from public.learning_get_review_target_events_v1(
    p_run_id,p_stage,p_item_type,p_item_key,p_base_artifact_sha256);
end;
$$;

create function public.validate_learning_authoring_package()
returns trigger language plpgsql set search_path = '' as $$
declare q jsonb; k jsonb; s jsonb; meta jsonb; field text;
begin
  if new.package ->> 'schema_version' is distinct from 'learning-package.v1'
    or new.package ->> 'run_id' is distinct from new.run_id
    or new.package -> 'source' ->> 'source_sha256' is distinct from (
      select r.source_sha256 from public.review_runs r where r.id = new.run_id)
    or new.package -> 'versions' ->> 'policy_version' is distinct from 'evidence-rules.v1'
  then raise exception 'authoring package identity or evidence policy is invalid'; end if;
  foreach field in array array['questions','kcs','groups','slots'] loop
    if jsonb_typeof(new.package -> field) is distinct from 'array'
    then raise exception 'authoring package requires array %', field; end if;
  end loop;
  if jsonb_typeof(new.package -> 'question_meta') is distinct from 'object'
    or jsonb_array_length(new.package -> 'questions') = 0
    or (select count(distinct x ->> 'question_id') from jsonb_array_elements(new.package -> 'questions') x)
      <> jsonb_array_length(new.package -> 'questions')
    or (select count(distinct x ->> 'kc_id') from jsonb_array_elements(new.package -> 'kcs') x)
      <> jsonb_array_length(new.package -> 'kcs')
    or (select count(distinct x ->> 'slot_id') from jsonb_array_elements(new.package -> 'slots') x)
      <> jsonb_array_length(new.package -> 'slots')
  then raise exception 'authoring package metadata or duplicate identities are invalid'; end if;
  for k in select value from jsonb_array_elements(new.package -> 'kcs') loop
    if not public.review_payload_is_valid('kc','leaf_kc',k) or not exists (
      select 1 from public.review_targets t where t.run_id = new.run_id and t.stage = 'kc'
        and t.item_type = 'leaf_kc' and t.item_key = k ->> 'kc_id')
    then raise exception 'authoring KC is not a registered valid leaf'; end if;
  end loop;
  for s in select value from jsonb_array_elements(new.package -> 'slots') loop
    if not exists (select 1 from jsonb_array_elements(new.package -> 'kcs') kc_row
      where kc_row.value ->> 'kc_id' = s ->> 'kc_id')
    then raise exception 'assessment slot refers to an unknown KC'; end if;
  end loop;
  for q in select value from jsonb_array_elements(new.package -> 'questions') loop
    meta := new.package -> 'question_meta' -> (q ->> 'question_id');
    if not exists (select 1 from public.learning_items i where i.run_id = new.run_id
      and i.question_id = q ->> 'question_id' and i.question_payload = q
      and i.question_sha256 = meta ->> 'question_sha256'
      and i.initial_check_status = meta ->> 'initial_check_status' and i.lineage = meta -> 'lineage')
    then raise exception 'authoring question must match its registered immutable learning item'; end if;
    if not exists (select 1 from jsonb_array_elements(new.package -> 'kcs') kc_row
        where kc_row.value ->> 'kc_id' = q ->> 'kc_id')
      or (q ->> 'slot_id' is not null and not exists (
        select 1 from jsonb_array_elements(new.package -> 'slots') slot_row
        where slot_row.value ->> 'slot_id' = q ->> 'slot_id' and slot_row.value ->> 'kc_id' = q ->> 'kc_id'))
    then raise exception 'authoring question KC or assessment slot is invalid'; end if;
  end loop;
  return new;
end;
$$;
create trigger learning_authoring_package_validated before insert on public.learning_authoring_packages
  for each row execute function public.validate_learning_authoring_package();

create function public.learning_current_review(
  p_run_id text, p_stage text, p_item_type text, p_item_key text, p_base_payload jsonb default null
)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare target public.review_targets%rowtype; revision public.review_events%rowtype;
  decision public.review_events%rowtype; effective_payload jsonb;
begin
  select * into target from public.review_targets t where t.run_id = p_run_id
    and t.stage = p_stage and t.item_type = p_item_type and t.item_key = p_item_key;
  if not found then raise exception 'review target is not registered'; end if;
  select * into revision from public.review_events e where e.run_id = p_run_id
    and e.stage = p_stage and e.item_type = p_item_type and e.item_key = p_item_key
    and e.base_artifact_sha256 = target.base_artifact_sha256 and e.action = 'edit'
    order by e.created_at desc, e.id desc limit 1;
  select * into decision from public.review_events e where e.run_id = p_run_id
    and e.stage = p_stage and e.item_type = p_item_type and e.item_key = p_item_key
    and e.base_artifact_sha256 = target.base_artifact_sha256
    and e.action in ('approve','reject')
    and e.target_revision_id is not distinct from revision.id
    order by e.created_at desc, e.id desc limit 1;
  effective_payload := coalesce(revision.revision_payload, p_base_payload);
  return jsonb_build_object('payload', effective_payload, 'revision_id', revision.id,
    'payload_sha256', coalesce(revision.payload_sha256,target.base_artifact_sha256),
    'base_artifact_sha256',target.base_artifact_sha256,
    'action', coalesce(decision.action,'unreviewed'), 'decision_id',decision.id,
    'decision_at',decision.created_at,'edited_at',revision.created_at,
    'reviewer_id',decision.reviewer_id,'reviewer_name',decision.reviewer_name);
end;
$$;

create function public.learning_review_version(p_run_id text)
returns text language sql stable security definer set search_path = '' as $$
  select encode(extensions.digest(convert_to(jsonb_build_object(
    'package_sha256', p.package_sha256,
    'package_snapshot_sha256',encode(extensions.digest(convert_to(p.package::text,'UTF8'),'sha256'),'hex'),
    'targets',coalesce((select jsonb_agg(jsonb_build_array(t.stage,t.item_type,t.item_key,
      t.base_artifact_sha256) order by t.stage,t.item_type,t.item_key)
      from public.review_targets t where t.run_id = p_run_id),'[]'::jsonb),
    'events',coalesce((select jsonb_agg(jsonb_build_array(e.id,e.action,e.payload_sha256,
      e.target_revision_id) order by e.created_at,e.id)
      from public.review_events e where e.run_id = p_run_id),'[]'::jsonb)
  )::text,'UTF8'),'sha256'),'hex')
  from public.learning_authoring_packages p where p.run_id = p_run_id;
$$;

-- Citation edits must follow their new actual pages. Do not keep validating only
-- the pages referenced by the original candidate, and do not hard-code page keys.
create function public.learning_current_question_refs(p_run_id text,p_question jsonb,p_package jsonb)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare q jsonb; k jsonb; kc_payload jsonb; citations jsonb; citation jsonb;
  refs jsonb; ref jsonb; target public.review_targets%rowtype;
begin
  q := public.learning_current_review(p_run_id,'quiz','question',p_question ->> 'question_id',p_question) -> 'payload';
  select value into kc_payload from jsonb_array_elements(p_package -> 'kcs')
    where value ->> 'kc_id' = p_question ->> 'kc_id';
  k := public.learning_current_review(p_run_id,'kc','leaf_kc',p_question ->> 'kc_id',kc_payload) -> 'payload';
  if jsonb_typeof(k -> 'source_evidence') is distinct from 'array'
    or jsonb_typeof(q -> 'evidence_refs') is distinct from 'array'
  then raise exception 'source references must be arrays' using errcode = '22023'; end if;
  select coalesce(jsonb_agg(value),'[]') into refs from jsonb_array_elements(
    p_package -> 'question_meta' -> (p_question ->> 'question_id') -> 'lineage' -> 'review_targets')
    where value ->> 'stage' <> 'extraction';
  citations := (k -> 'source_evidence') || (q -> 'evidence_refs');
  for citation in select value from jsonb_array_elements(citations) loop
    if jsonb_typeof(citation -> 'page') is distinct from 'number' then
      raise exception 'source citation requires a PDF page number' using errcode = '22023';
    end if;
    if (citation ->> 'page')::numeric < 1
      or trunc((citation ->> 'page')::numeric) <> (citation ->> 'page')::numeric
    then raise exception 'source citation page must be a positive integer' using errcode = '22023'; end if;
    select * into target from public.review_targets t where t.run_id = p_run_id
      and t.stage = 'extraction' and t.item_type = 'page' and t.identity_field = 'page_number'
      and t.identity_value = citation -> 'page';
    if not found then raise exception 'source citation page is not registered' using errcode = '22023'; end if;
    ref := jsonb_build_object('stage',target.stage,'item_type',target.item_type,
      'item_key',target.item_key,'base_artifact_sha256',target.base_artifact_sha256);
    if not refs @> jsonb_build_array(ref) then refs := refs || jsonb_build_array(ref); end if;
  end loop;
  return refs;
end;
$$;

create function public.learning_question_publishability(p_run_id text,p_question jsonb,p_package jsonb)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare q jsonb; k jsonb; kc_payload jsonb; refs jsonb; ref jsonb; upstream jsonb;
  reasons text[] := '{}'; q_approved boolean; kc_approved boolean;
begin
  q := public.learning_current_review(p_run_id,'quiz','question',p_question ->> 'question_id',p_question);
  select value into kc_payload from jsonb_array_elements(p_package -> 'kcs')
    where value ->> 'kc_id' = p_question ->> 'kc_id';
  k := public.learning_current_review(p_run_id,'kc','leaf_kc',p_question ->> 'kc_id',kc_payload);
  q_approved := coalesce(q ->> 'action' = 'approve',false);
  kc_approved := coalesce(k ->> 'action' = 'approve',false);
  if not q_approved then reasons := array_append(reasons,'question_not_approved'); end if;
  if not kc_approved then reasons := array_append(reasons,'kc_not_approved'); end if;
  if q -> 'payload' ->> 'kc_id' is distinct from p_question ->> 'kc_id'
    or q -> 'payload' ->> 'slot_id' is distinct from p_question ->> 'slot_id'
    or q -> 'payload' ->> 'group_id' is distinct from p_question ->> 'group_id'
    or k -> 'payload' ->> 'group_id' is distinct from kc_payload ->> 'group_id'
  then reasons := array_append(reasons,'assessment_mapping_changed_requires_new_authoring_run'); end if;
  if q_approved and k ->> 'edited_at' is not null
    and (q ->> 'decision_at')::timestamptz < (k ->> 'edited_at')::timestamptz
  then reasons := array_append(reasons,'question_approval_precedes_kc_revision'); end if;
  refs := p_package -> 'question_meta' -> (p_question ->> 'question_id') -> 'lineage' -> 'review_targets';
  for ref in select value from jsonb_array_elements(refs) loop
    if not exists (select 1 from public.review_targets t where t.run_id = p_run_id
      and t.stage = ref ->> 'stage' and t.item_type = ref ->> 'item_type'
      and t.item_key = ref ->> 'item_key' and t.base_artifact_sha256 = ref ->> 'base_artifact_sha256')
    then reasons := array_append(reasons,'registered_baseline_changed'); end if;
  end loop;
  begin
    refs := public.learning_current_question_refs(p_run_id,p_question,p_package);
  exception when sqlstate '22023' then
    refs := '[]'; reasons := array_append(reasons,'source_reference_invalid');
  end;
  for ref in select value from jsonb_array_elements(refs) loop
    if ref ->> 'stage' = 'extraction' then
      upstream := public.learning_current_review(p_run_id,'extraction',ref ->> 'item_type',ref ->> 'item_key');
      if upstream ->> 'action' = 'reject'
      then reasons := array_append(reasons,'upstream_extraction_rejected'); end if;
      if kc_approved and upstream ->> 'edited_at' is not null and (
        (k ->> 'decision_at')::timestamptz < (upstream ->> 'edited_at')::timestamptz
        or (q_approved and (q ->> 'decision_at')::timestamptz < (upstream ->> 'edited_at')::timestamptz))
      then reasons := array_append(reasons,'approval_precedes_extraction_revision'); end if;
    end if;
  end loop;
  return jsonb_build_object('question_id',p_question ->> 'question_id','kc_id',p_question ->> 'kc_id',
    'title',q -> 'payload' ->> 'title','question_approved',q_approved,'kc_approved',kc_approved,
    'publishable',cardinality(reasons) = 0,'reason',array_to_string(reasons,', '),
    'reasons',to_jsonb(reasons),'question_review',q - 'payload','kc_review',k - 'payload');
end;
$$;

create function public.get_teacher_learning_package(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare package_value jsonb; q jsonb; k jsonb; questions jsonb := '[]'; kcs jsonb := '[]';
begin
  perform public.learning_require_teacher(p_run_id);
  select r.package into package_value from public.learning_releases r where r.release_id = p_run_id;
  if found then return package_value; end if;
  select p.package into package_value from public.learning_authoring_packages p where p.run_id = p_run_id;
  if not found then raise exception 'authoring package not registered'; end if;
  for q in select value from jsonb_array_elements(package_value -> 'questions') loop
    questions := questions || jsonb_build_array(public.learning_current_review(
      p_run_id,'quiz','question',q ->> 'question_id',q) -> 'payload');
  end loop;
  for k in select value from jsonb_array_elements(package_value -> 'kcs') loop
    kcs := kcs || jsonb_build_array(public.learning_current_review(
      p_run_id,'kc','leaf_kc',k ->> 'kc_id',k) -> 'payload');
  end loop;
  return package_value || jsonb_build_object('questions',questions,'kcs',kcs,
    'review_version',public.learning_review_version(p_run_id));
end;
$$;

create function public.learning_release_summary(p_release public.learning_releases)
returns jsonb language sql stable set search_path = '' as $$
  select jsonb_build_object('release_id',p_release.release_id,'course_id',p_release.course_id,
    'label',p_release.label,'created_at',p_release.created_at,'published_at',p_release.created_at,
    'question_count',jsonb_array_length(p_release.package -> 'questions'),
    'kc_count',jsonb_array_length(p_release.package -> 'kcs'),
    'slot_count',jsonb_array_length(p_release.package -> 'slots'),
    'covered_slot_count',(select count(distinct q ->> 'slot_id')
      from jsonb_array_elements(p_release.package -> 'questions') q),
    'publish_event_id',p_release.publish_event_id,'review_version',p_release.review_version);
$$;

create function public.get_teacher_workspace(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_teacher(p_run_id); course text := public.learning_root_course(p_run_id);
  package_value jsonb; title text; reviews jsonb;
begin
  select p.package,r.source_filename into package_value,title
    from public.learning_authoring_packages p join public.review_runs r on r.id = p.run_id
    where p.run_id = course;
  if not found then raise exception 'authoring package not registered'; end if;
  select coalesce(jsonb_agg(public.learning_question_publishability(course,q,package_value)
    order by n),'[]') into reviews from jsonb_array_elements(package_value -> 'questions') with ordinality a(q,n);
  return jsonb_build_object('course_id',course,'run_id',course,'title',title,
    'user_id',actor,'can_teach',true,'can_grade',true,'can_publish',true,
    'review_version',public.learning_review_version(course),'question_reviews',reviews,
    'question_count',jsonb_array_length(package_value -> 'questions'),
    'slot_count',jsonb_array_length(package_value -> 'slots'),
    'releases',coalesce((select jsonb_agg(public.learning_release_summary(r)
      order by r.created_at desc,r.release_id) from public.learning_releases r where r.course_id = course),'[]'),
    'learners',coalesce((select jsonb_agg(jsonb_build_object('learner_id',e.learner_id,
      'display_name',p.display_name,'release_id',e.release_id,'enrolled_at',e.enrolled_at,
      'attempt_count',(select count(*) from public.learning_attempts a
        where a.run_id = e.release_id and a.learner_id = e.learner_id and a.status <> 'in_progress'),
      'pending_count',(select count(*) from public.learning_attempts a
        where a.run_id = e.release_id and a.learner_id = e.learner_id and a.status = 'pending_grade'),
      'last_activity',(select max(coalesce(a.graded_at,a.submitted_at,a.started_at))
        from public.learning_attempts a where a.run_id = e.release_id and a.learner_id = e.learner_id))
      order by e.enrolled_at desc,p.display_name,e.learner_id)
      from public.learning_enrollments e join public.reviewer_profiles p on p.user_id = e.learner_id
      where e.course_id = course),'[]'));
end;
$$;

create function public.publish_reviewed_release(
  p_run_id text, p_label text, p_expected_review_version text, p_question_ids text[], p_event_id uuid
)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_teacher(p_run_id); base jsonb; package_value jsonb;
  q jsonb; original_q jsonb; k jsonb; row_target public.review_targets%rowtype; state jsonb;
  ref jsonb; refs jsonb; lineage jsonb; old_meta jsonb; meta jsonb := '{}'; approvals jsonb := '{}';
  selected jsonb := '[]'; kcs jsonb := '[]'; kc_publication jsonb := '{}';
  release_id_value text := 'release-' || gen_random_uuid()::text;
  version_value text; question_hash text; target_hash text; kc_hash text; quiz_hash text;
  extraction_hash text; inserted public.learning_releases%rowtype; source_run public.review_runs%rowtype;
begin
  perform pg_advisory_xact_lock(hashtextextended(p_run_id,0));
  if p_event_id is null or p_label is null or char_length(btrim(p_label)) not between 1 and 120
    or p_question_ids is null or cardinality(p_question_ids) = 0
    or cardinality(p_question_ids) <> (select count(distinct id) from unnest(p_question_ids) id)
  then raise exception 'publication requires a label, event ID, and distinct selected questions'; end if;
  select * into inserted from public.learning_releases r where r.publish_event_id = p_event_id;
  if found then
    if inserted.course_id <> p_run_id or inserted.published_by <> actor or inserted.label <> btrim(p_label)
      or inserted.question_ids is distinct from p_question_ids or inserted.review_version is distinct from p_expected_review_version
    then raise exception 'publication event ID already used'; end if;
    return public.learning_release_summary(inserted);
  end if;
  select p.package into base from public.learning_authoring_packages p where p.run_id = p_run_id;
  if not found then raise exception 'authoring package not registered'; end if;
  version_value := public.learning_review_version(p_run_id);
  if version_value is distinct from p_expected_review_version
  then raise exception 'stale review version; reload before publishing'; end if;
  if exists (select 1 from unnest(p_question_ids) wanted where not exists (
    select 1 from jsonb_array_elements(base -> 'questions') question_row where question_row.value ->> 'question_id' = wanted))
  then raise exception 'selected question is not in this authoring package'; end if;
  for original_q in select value from jsonb_array_elements(base -> 'questions') loop
    if not (original_q ->> 'question_id' = any(p_question_ids)) then continue; end if;
    state := public.learning_question_publishability(p_run_id,original_q,base);
    if not (state ->> 'publishable')::boolean
    then raise exception 'question % is not ready for publication: %', original_q ->> 'question_id',state ->> 'reason'; end if;
    q := public.learning_current_review(p_run_id,'quiz','question',original_q ->> 'question_id',original_q) -> 'payload';
    selected := selected || jsonb_build_array(q);
    approvals := approvals || jsonb_build_object(q ->> 'question_id',state - array['title','publishable','reason','reasons']);
  end loop;
  for k in select value from jsonb_array_elements(base -> 'kcs') loop
    kcs := kcs || jsonb_build_array(public.learning_current_review(p_run_id,'kc','leaf_kc',k ->> 'kc_id',k) -> 'payload');
    -- Keep every KC/slot identity for honest coverage. Only the approved KCs
    -- underlying explicitly selected items become student learning material.
    kc_publication := kc_publication || jsonb_build_object(k ->> 'kc_id',
      case when exists (select 1 from jsonb_array_elements(selected) selected_question
        where selected_question.value ->> 'kc_id' = k ->> 'kc_id')
      then 'APPROVED' else 'NOT_PUBLISHED' end);
  end loop;
  kc_hash := encode(extensions.digest(convert_to(kcs::text,'UTF8'),'sha256'),'hex');
  quiz_hash := encode(extensions.digest(convert_to(selected::text,'UTF8'),'sha256'),'hex');
  select * into strict source_run from public.review_runs r where r.id = p_run_id;
  insert into public.review_runs(id,source_id,source_filename,source_sha256,is_public,review_open,metadata)
    values (release_id_value,source_run.source_id,source_run.source_filename,source_run.source_sha256,
      true,false,jsonb_build_object('course_id',p_run_id,'release_label',btrim(p_label),'human_published',true));

  -- Clone effective target identities into the release. The new learning lineage
  -- never points to mutable authoring targets, so later edits do not rewrite it.
  for row_target in select * from public.review_targets t where t.run_id = p_run_id loop
    state := public.learning_current_review(p_run_id,row_target.stage,row_target.item_type,row_target.item_key);
    target_hash := state ->> 'payload_sha256';
    if row_target.stage = 'quiz' and row_target.item_type = 'question' then
      select value into q from jsonb_array_elements(selected) where value ->> 'question_id' = row_target.item_key;
      if not found then continue; end if;
      target_hash := encode(extensions.digest(convert_to(q::text,'UTF8'),'sha256'),'hex');
    elsif row_target.stage = 'kc' and row_target.item_type = 'leaf_kc' then
      select value into k from jsonb_array_elements(kcs) where value ->> 'kc_id' = row_target.item_key;
      if found then target_hash := encode(extensions.digest(convert_to(k::text,'UTF8'),'sha256'),'hex'); end if;
    end if;
    insert into public.review_targets(run_id,stage,item_type,item_key,identity_field,identity_value,base_artifact_sha256)
      values(release_id_value,row_target.stage,row_target.item_type,row_target.item_key,
        row_target.identity_field,row_target.identity_value,target_hash);
  end loop;
  select encode(extensions.digest(convert_to(coalesce(jsonb_agg(jsonb_build_array(t.item_key,t.base_artifact_sha256)
    order by t.item_key),'[]')::text,'UTF8'),'sha256'),'hex') into extraction_hash
    from public.review_targets t where t.run_id = release_id_value and t.stage = 'extraction';
  for q in select value from jsonb_array_elements(selected) loop
    old_meta := base -> 'question_meta' -> (q ->> 'question_id'); refs := '[]';
    select value into strict original_q from jsonb_array_elements(base -> 'questions')
      where value ->> 'question_id' = q ->> 'question_id';
    for ref in select value from jsonb_array_elements(
      public.learning_current_question_refs(p_run_id,original_q,base)) loop
      select t.base_artifact_sha256 into strict target_hash from public.review_targets t
        where t.run_id = release_id_value and t.stage = ref ->> 'stage'
          and t.item_type = ref ->> 'item_type' and t.item_key = ref ->> 'item_key';
      refs := refs || jsonb_build_array(ref || jsonb_build_object('base_artifact_sha256',target_hash));
    end loop;
    question_hash := encode(extensions.digest(convert_to(q::text,'UTF8'),'sha256'),'hex');
    lineage := old_meta -> 'lineage' || jsonb_build_object('review_targets',refs,
      'kc_set_sha256',kc_hash,'quiz_sha256',quiz_hash,'extraction_sha256',extraction_hash,
      'authoring_run_id',p_run_id,'release_id',release_id_value,'human_approved',true,
      'approval',approvals -> (q ->> 'question_id'));
    insert into public.learning_items(run_id,question_id,question_sha256,kc_id,slot_id,
      initial_check_status,question_payload,lineage)
      values(release_id_value,q ->> 'question_id',question_hash,q ->> 'kc_id',q ->> 'slot_id',
        old_meta ->> 'initial_check_status',q,lineage);
    meta := meta || jsonb_build_object(q ->> 'question_id',jsonb_build_object(
      'question_sha256',question_hash,'initial_check_status',old_meta ->> 'initial_check_status',
      'quality_status','PASS','human_approved',true,'lineage',lineage));
  end loop;
  package_value := base || jsonb_build_object('run_id',release_id_value,'release_id',release_id_value,
    'course_id',p_run_id,'label',btrim(p_label),'kcs',kcs,'questions',selected,'question_meta',meta,
    'kc_publication_status',kc_publication,
    'versions',(base -> 'versions') || jsonb_build_object('kc_sha256',kc_hash,'quiz_sha256',quiz_hash,
      'extraction_sha256',extraction_hash),
    'publication',jsonb_build_object('status','PUBLISHED','release_id',release_id_value,'review_method','human',
      'published_by',actor,'review_version',version_value,'upstream_extraction','PROPOSED_OR_REVIEWED',
      'included_question_count',jsonb_array_length(selected),'omitted_question_count',
      jsonb_array_length(base -> 'questions') - jsonb_array_length(selected)),
    'practice_only',true,'secure_exam',false);
  insert into public.learning_releases(release_id,course_id,label,package,review_version,question_ids,
    published_by,publish_event_id) values(release_id_value,p_run_id,btrim(p_label),package_value,
      version_value,p_question_ids,actor,p_event_id) returning * into inserted;
  return public.learning_release_summary(inserted);
end;
$$;

create function public.learning_pick_json(p_value jsonb,p_keys text[])
returns jsonb language sql immutable set search_path = '' as $$
  select coalesce(jsonb_object_agg(key,value),'{}'::jsonb) from jsonb_each(p_value) where key = any(p_keys);
$$;

create function public.learning_student_package(p_package jsonb)
returns jsonb language plpgsql immutable set search_path = '' as $$
declare result jsonb; q jsonb; question_value jsonb; field text; questions jsonb := '[]';
begin
  result := public.learning_pick_json(p_package,array['schema_version','run_id','course_id','release_id','label',
    'source','versions','question_meta','publication','practice_only','secure_exam','evidence_label','baseline_hash_algorithm']);
  for q in select value from jsonb_array_elements(p_package -> 'questions') loop
    question_value := public.learning_pick_json(q,array['question_id','kc_id','slot_id','group_id','variant_index',
      'title','interaction','prompt','hint_absence_reason','cognitive_operation','intended_difficulty']);
    question_value := question_value || jsonb_build_object('stimulus',public.learning_pick_json(q -> 'stimulus',
      array['kind','text','table_columns','table_rows','formula']));
    foreach field in array array['choice_options','matching_left','matching_right','ordering_options'] loop
      question_value := question_value || jsonb_build_object(field,(select coalesce(jsonb_agg(
        public.learning_pick_json(o,array['option_id','text']) order by n),'[]')
        from jsonb_array_elements(q -> field) with ordinality a(o,n)));
    end loop;
    question_value := question_value || jsonb_build_object('rubric',(select coalesce(jsonb_agg(
      public.learning_pick_json(r,array['criterion','points']) order by n),'[]')
      from jsonb_array_elements(q -> 'rubric') with ordinality a(r,n)),
      'hints',(select coalesce(jsonb_agg(public.learning_pick_json(h,array['hint_id','kind']) order by n),'[]')
      from jsonb_array_elements(coalesce(q -> 'hints','[]')) with ordinality a(h,n)));
    questions := questions || jsonb_build_array(question_value);
  end loop;
  return result || jsonb_build_object('questions',questions,
    'kcs',(select coalesce(jsonb_agg(case
      when p_package -> 'kc_publication_status' ->> (k ->> 'kc_id') = 'APPROVED'
      then public.learning_pick_json(k,array['kc_id','group_id','name','knowledge_description','observable_claim'])
        || jsonb_build_object('content_available',true,'publication_status','APPROVED')
      else public.learning_pick_json(k,array['kc_id','group_id']) || jsonb_build_object(
        'name','Nội dung chưa phát hành','knowledge_description','','observable_claim','',
        'content_available',false,'publication_status','NOT_PUBLISHED') end order by n),'[]')
      from jsonb_array_elements(p_package -> 'kcs') with ordinality a(k,n)),
    'groups',(select coalesce(jsonb_agg(public.learning_pick_json(g,array['group_id','name']) order by n),'[]')
      from jsonb_array_elements(p_package -> 'groups') with ordinality a(g,n)),
    'slots',(select coalesce(jsonb_agg(public.learning_pick_json(s,array[
      'slot_id','kc_id','cognitive_operation','intended_difficulty']) || jsonb_build_object(
        'evidence_intent',case when p_package -> 'kc_publication_status' ->> (s ->> 'kc_id') = 'APPROVED'
          then s ->> 'evidence_intent' else '' end,
        'variant_count',(select count(*) from jsonb_array_elements(p_package -> 'questions') public_question
          where public_question.value ->> 'slot_id' = s ->> 'slot_id')) order by n),'[]')
      from jsonb_array_elements(p_package -> 'slots') with ordinality a(s,n)));
end;
$$;

create function public.list_learning_courses()
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor();
begin
  return coalesce((select jsonb_agg(jsonb_build_object('course_id',p.run_id,
    'title',r.source_filename,'source_filename',r.source_filename,
    'latest_release',(select public.learning_release_summary(v) from public.learning_releases v
      join public.review_runs live on live.id = v.release_id and live.is_public
      where v.course_id = p.run_id order by v.created_at desc,v.release_id limit 1),
    'enrollment',(select to_jsonb(e) - 'learner_id' from public.learning_enrollments e
      where e.course_id = p.run_id and e.learner_id = actor order by e.enrolled_at desc,e.release_id limit 1))
    order by p.created_at,p.run_id)
    from public.learning_authoring_packages p join public.review_runs r on r.id = p.run_id
    where exists (select 1 from public.learning_releases v join public.review_runs live
      on live.id = v.release_id and live.is_public where v.course_id = p.run_id)),'[]');
end;
$$;

create function public.enroll_learning_course(p_course_id text,p_release_id text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); chosen text; enrollment public.learning_enrollments%rowtype;
begin
  -- Default re-entry preserves the learner's pinned version. Only an explicit
  -- release choice can add a newer enrollment, retaining the earlier history.
  if p_release_id is null then
    select * into enrollment from public.learning_enrollments e where e.course_id = p_course_id
      and e.learner_id = actor order by e.enrolled_at desc,e.release_id limit 1;
    if found then return to_jsonb(enrollment) - 'learner_id'; end if;
  end if;
  select r.release_id into chosen from public.learning_releases r
    join public.review_runs live on live.id = r.release_id and live.is_public
    where r.course_id = p_course_id and (p_release_id is null or r.release_id = p_release_id)
    order by r.created_at desc,r.release_id limit 1;
  if chosen is null then raise exception 'published course version unavailable'; end if;
  insert into public.learning_enrollments(course_id,release_id,learner_id)
    values(p_course_id,chosen,actor) on conflict (release_id,learner_id) do nothing;
  select * into strict enrollment from public.learning_enrollments e
    where e.release_id = chosen and e.learner_id = actor;
  return to_jsonb(enrollment) - 'learner_id';
end;
$$;

create function public.get_student_learning_package(p_release_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare package_value jsonb;
begin
  perform public.learning_require_run_access(p_release_id);
  select r.package into package_value from public.learning_releases r join public.review_runs live
    on live.id = r.release_id and live.is_public where r.release_id = p_release_id;
  if not found then raise exception 'published course version unavailable'; end if;
  return public.learning_student_package(package_value);
end;
$$;

-- Published human review is distinct from the preserved original AI check. The
-- explicit teacher approval can supersede an initial REVIEW/REJECT; it is never
-- presented as a retroactive AI PASS. Draft edits cannot mutate this snapshot.
alter function public.learning_item_quality(public.learning_items) rename to learning_item_quality_v1;
create function public.learning_item_quality(p_item public.learning_items)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
begin
  if exists (select 1 from public.learning_releases r where r.release_id = p_item.run_id
    and r.package -> 'question_meta' -> p_item.question_id ->> 'human_approved' = 'true')
  then return jsonb_build_object('quality_status','PASS','exclusion_reasons','[]'::jsonb,
    'human_approved',true,'quality_basis','teacher_approved_release',
    'initial_check_status',p_item.initial_check_status); end if;
  return public.learning_item_quality_v1(p_item);
end;
$$;

alter function public.learning_attempt_json(public.learning_attempts) rename to learning_attempt_json_v1;
create function public.learning_attempt_json(p_attempt public.learning_attempts)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare result jsonb := public.learning_attempt_json_v1(p_attempt); question jsonb;
begin
  select i.question_payload into strict question from public.learning_items i where i.run_id = p_attempt.run_id
    and i.question_id = p_attempt.question_id and i.question_sha256 = p_attempt.question_sha256;
  result := result || jsonb_build_object('revealed_hints',(select coalesce(jsonb_agg(
    public.learning_pick_json(h,array['hint_id','kind','text']) order by n),'[]')
    from jsonb_array_elements(coalesce(question -> 'hints','[]')) with ordinality a(h,n)
    where h ->> 'hint_id' = any(p_attempt.hint_ids)));
  if p_attempt.status <> 'in_progress' then
    result := result || jsonb_build_object('answer_material',public.learning_pick_json(question,
      array['correct_answer','answer_explanation','rubric']));
  end if;
  return result;
end;
$$;

alter function public.get_learning_state(text) rename to learning_get_state_v1;
create function public.get_learning_state(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_run_access(p_run_id); result jsonb;
begin
  result := public.learning_get_state_v1(p_run_id);
  return result || jsonb_build_object('can_grade',public.learning_is_course_teacher(p_run_id),
    'course_id',public.learning_root_course(p_run_id),'release_id',p_run_id,
    'enrollment',(select to_jsonb(e) - 'learner_id' from public.learning_enrollments e
      where e.release_id = p_run_id and e.learner_id = actor));
end;
$$;

alter function public.start_learning_attempt(text,text,text,uuid) rename to learning_start_attempt_v1;
create function public.start_learning_attempt(p_run_id text,p_question_id text,p_question_sha256 text,p_attempt_id uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); item public.learning_items%rowtype;
  attempt public.learning_attempts%rowtype; quality jsonb; repeated boolean;
begin
  perform public.learning_require_run_access(p_run_id);
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
  -- Republishing the same item (including a corrected key/text) does not make a
  -- learner forget its earlier exposure. New genuinely distinct variants have
  -- their own question IDs; do not count a release ID change as new evidence.
  repeated := exists (select 1 from public.learning_attempts a where a.learner_id = actor
    and a.question_id = p_question_id
    and public.learning_root_course(a.run_id) = public.learning_root_course(p_run_id));
  quality := public.learning_item_quality(item);
  insert into public.learning_attempts(attempt_id,learner_id,run_id,question_id,question_sha256,
    kc_id,slot_id,is_repeat,grading_version,quality_status_at_start)
    values(p_attempt_id,actor,p_run_id,p_question_id,p_question_sha256,item.kc_id,item.slot_id,repeated,
      case when item.question_payload ->> 'interaction' = 'short_text' then 'rubric-human-v1' else 'exact-v1' end,
      quality ->> 'quality_status') returning * into attempt;
  insert into public.learning_events(learner_id,actor_id,run_id,question_id,question_sha256,attempt_id,kind,payload)
    values(actor,actor,p_run_id,p_question_id,p_question_sha256,p_attempt_id,'start',
      jsonb_build_object('is_repeat',repeated,'quality',quality,'lineage',item.lineage));
  return public.learning_attempt_json(attempt);
end;
$$;

alter function public.reveal_learning_hint(uuid,text) rename to learning_reveal_hint_v1;
create function public.reveal_learning_hint(p_attempt_id uuid,p_hint_id text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare run_value text;
begin
  select a.run_id into run_value from public.learning_attempts a
    where a.attempt_id = p_attempt_id and a.learner_id = auth.uid();
  if run_value is null then raise exception 'attempt unavailable'; end if;
  perform public.learning_require_run_access(run_value);
  return public.learning_reveal_hint_v1(p_attempt_id,p_hint_id);
end;
$$;

alter function public.submit_learning_attempt(uuid,jsonb) rename to learning_submit_attempt_v1;
create function public.submit_learning_attempt(p_attempt_id uuid,p_response jsonb)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare run_value text;
begin
  select a.run_id into run_value from public.learning_attempts a
    where a.attempt_id = p_attempt_id and a.learner_id = auth.uid();
  if run_value is null then raise exception 'attempt unavailable'; end if;
  perform public.learning_require_run_access(run_value);
  return public.learning_submit_attempt_v1(p_attempt_id,p_response);
end;
$$;

alter function public.append_learning_feedback(text,text,text,text,text,uuid,uuid)
  rename to learning_append_feedback_v1;
create function public.append_learning_feedback(p_run_id text,p_question_id text,p_question_sha256 text,
  p_vote text,p_note text default null,p_attempt_id uuid default null,p_event_id uuid default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
begin
  perform public.learning_require_run_access(p_run_id);
  return public.learning_append_feedback_v1(p_run_id,p_question_id,p_question_sha256,
    p_vote,p_note,p_attempt_id,p_event_id);
end;
$$;

create or replace function public.get_learning_grading_queue(p_run_id text)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_teacher(p_run_id);
begin
  if not exists (select 1 from public.review_runs r where r.id = p_run_id and r.is_public)
  then raise exception 'learning run unavailable'; end if;
  return coalesce((select jsonb_agg(queued.entry order by queued.submitted_at,queued.attempt_id)
    from (select a.attempt_id,a.submitted_at,public.learning_attempt_json(a) || jsonb_build_object(
      'question_payload',i.question_payload,'learner_name',p.display_name) as entry
      from public.learning_attempts a join public.learning_items i
        on (i.run_id,i.question_id,i.question_sha256) = (a.run_id,a.question_id,a.question_sha256)
      join public.reviewer_profiles p on p.user_id = a.learner_id
      where a.run_id = p_run_id and a.status = 'pending_grade' and a.learner_id <> actor
      order by a.submitted_at,a.attempt_id limit 100) queued),'[]');
end;
$$;

create or replace function public.grade_learning_attempt(
  p_attempt_id uuid,p_scores jsonb,p_note text default null,p_event_id uuid default null
)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare actor uuid := public.learning_require_actor(true); attempt public.learning_attempts%rowtype;
  item public.learning_items%rowtype; event public.learning_events%rowtype;
  index_value integer; earned numeric := 0; maximum numeric := 0; points numeric; allowed numeric;
  note_value text := nullif(btrim(p_note),''); event_payload jsonb;
begin
  if p_event_id is null or p_scores is null or jsonb_typeof(p_scores) <> 'array'
    or octet_length(p_scores::text) > 8192 or (p_note is not null and char_length(p_note) > 2000)
  then raise exception 'invalid grading event, score array, or note'; end if;
  select a.* into attempt from public.learning_attempts a join public.review_runs r on r.id = a.run_id
    where a.attempt_id = p_attempt_id and r.is_public for update of a;
  if not found then raise exception 'attempt unavailable'; end if;
  perform public.learning_require_teacher(attempt.run_id);
  if attempt.learner_id = actor then raise exception 'a teacher cannot grade their own response'; end if;
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
  event_payload := jsonb_build_object('scores',p_scores,'note',note_value,'score',earned,
    'max_score',maximum,'correct',earned = maximum,'grading_version','rubric-human-v1');
  select e.* into event from public.learning_events e where e.event_id = p_event_id;
  if found then
    if event.actor_id <> actor or event.kind <> 'manual_grade' or event.attempt_id <> p_attempt_id
      or event.payload is distinct from event_payload
    then raise exception 'event ID already used'; end if;
    return public.learning_attempt_json(attempt);
  end if;
  if attempt.status <> 'pending_grade' then raise exception 'attempt is not pending a first human grade'; end if;
  perform public.learning_check_rate(actor);
  update public.learning_attempts set status = 'graded',score = earned,max_score = maximum,
    correct = earned = maximum,grading_method = 'rubric_human',graded_at = clock_timestamp(),
    graded_by = actor,rubric_scores = p_scores,grading_note = note_value
    where attempt_id = p_attempt_id returning * into attempt;
  insert into public.learning_events(event_id,learner_id,actor_id,run_id,question_id,
    question_sha256,attempt_id,kind,payload) values(p_event_id,attempt.learner_id,actor,attempt.run_id,
      attempt.question_id,attempt.question_sha256,p_attempt_id,'manual_grade',event_payload);
  return public.learning_attempt_json(attempt);
end;
$$;

create function public.get_teacher_learner_state(p_run_id text,p_learner_id uuid,p_release_id text default null)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare actor uuid := public.learning_require_teacher(p_run_id); course text := public.learning_root_course(p_run_id);
  chosen text; package_value jsonb; learner jsonb;
begin
  select e.release_id,jsonb_build_object('learner_id',p.user_id,'display_name',p.display_name)
    into chosen,learner from public.learning_enrollments e join public.reviewer_profiles p on p.user_id = e.learner_id
    where e.course_id = course and e.learner_id = p_learner_id
      and (p_release_id is null or e.release_id = p_release_id)
    order by e.enrolled_at desc,e.release_id limit 1;
  if chosen is null then raise exception 'learner is not enrolled in this course version'; end if;
  select r.package into strict package_value from public.learning_releases r where r.release_id = chosen;
  return jsonb_build_object('course_id',course,'release_id',chosen,'learner',learner,
    'learning_package',package_value,'policy_version','evidence-rules.v1',
    'attempts',coalesce((select jsonb_agg(public.learning_attempt_json(a) order by a.started_at,a.attempt_id)
      from public.learning_attempts a where a.run_id = chosen and a.learner_id = p_learner_id),'[]'),
    'feedback',coalesce((select jsonb_agg(to_jsonb(e) order by e.created_at,e.event_id)
      from public.learning_events e where e.run_id = chosen and e.learner_id = p_learner_id and e.kind = 'feedback'),'[]'),
    'item_quality',coalesce((select jsonb_object_agg(i.question_id,public.learning_item_quality(i) ||
      jsonb_build_object('question_sha256',i.question_sha256,'initial_check_status',i.initial_check_status))
      from public.learning_items i where i.run_id = chosen),'{}'));
end;
$$;

-- No caller can invoke renamed legacy entrypoints to bypass role/enrollment checks.
revoke all on function public.learning_append_review_event_v1(text,text,text,text,text,text,jsonb,uuid),
  public.learning_get_review_target_events_v1(text,text,text,text,text),
  public.learning_item_quality_v1(public.learning_items),public.learning_attempt_json_v1(public.learning_attempts),
  public.learning_get_state_v1(text),public.learning_start_attempt_v1(text,text,text,uuid),
  public.learning_reveal_hint_v1(uuid,text),public.learning_submit_attempt_v1(uuid,jsonb),
  public.learning_append_feedback_v1(text,text,text,text,text,uuid,uuid),
  public.learning_root_course(text),public.learning_is_course_teacher(text),public.learning_require_teacher(text),
  public.learning_require_run_access(text),public.validate_learning_authoring_package(),
  public.learning_current_review(text,text,text,text,jsonb),public.learning_review_version(text),
  public.learning_current_question_refs(text,jsonb,jsonb),
  public.learning_question_publishability(text,jsonb,jsonb),public.learning_release_summary(public.learning_releases),
  public.learning_pick_json(jsonb,text[]),public.learning_student_package(jsonb),
  public.learning_item_quality(public.learning_items),public.learning_attempt_json(public.learning_attempts)
  from public,anon,authenticated;

revoke all on function public.get_teacher_access(text),public.get_teacher_workspace(text),
  public.get_teacher_learning_package(text),public.publish_reviewed_release(text,text,text,text[],uuid),
  public.list_learning_courses(),public.enroll_learning_course(text,text),public.get_student_learning_package(text),
  public.get_teacher_learner_state(text,uuid,text),
  public.append_review_event(text,text,text,text,text,text,jsonb,uuid),
  public.get_review_target_events(text,text,text,text,text),public.get_learning_state(text),
  public.start_learning_attempt(text,text,text,uuid),public.reveal_learning_hint(uuid,text),
  public.submit_learning_attempt(uuid,jsonb),public.append_learning_feedback(text,text,text,text,text,uuid,uuid),
  public.get_learning_grading_queue(text),public.grade_learning_attempt(uuid,jsonb,text,uuid)
  from public,anon,authenticated;
grant execute on function public.get_teacher_access(text),public.get_teacher_workspace(text),
  public.get_teacher_learning_package(text),public.publish_reviewed_release(text,text,text,text[],uuid),
  public.list_learning_courses(),public.enroll_learning_course(text,text),public.get_student_learning_package(text),
  public.get_teacher_learner_state(text,uuid,text),
  public.append_review_event(text,text,text,text,text,text,jsonb,uuid),
  public.get_review_target_events(text,text,text,text,text),public.get_learning_state(text),
  public.start_learning_attempt(text,text,text,uuid),public.reveal_learning_hint(uuid,text),
  public.submit_learning_attempt(uuid,jsonb),public.append_learning_feedback(text,text,text,text,text,uuid,uuid),
  public.get_learning_grading_queue(text),public.grade_learning_attempt(uuid,jsonb,text,uuid)
  to authenticated;
revoke all on function public.learning_run_metadata_visible(text) from public,anon,authenticated;
grant execute on function public.learning_run_metadata_visible(text) to anon,authenticated;
