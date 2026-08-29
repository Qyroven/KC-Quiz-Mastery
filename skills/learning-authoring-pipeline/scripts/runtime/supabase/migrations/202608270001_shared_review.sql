create extension if not exists pgcrypto;

create table public.review_runs (
  id text primary key,
  source_id text not null,
  source_filename text not null,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  is_public boolean not null default false,
  review_open boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.reviewer_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (
    display_name = btrim(display_name)
    and char_length(display_name) between 1 and 80
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.review_events (
  id uuid primary key default gen_random_uuid(),
  run_id text not null references public.review_runs(id) on delete restrict,
  stage text not null check (stage in ('extraction', 'kc', 'quiz')),
  item_type text not null check (item_type ~ '^[a-z][a-z0-9_]*$'),
  item_key text not null check (char_length(item_key) between 1 and 160),
  action text not null check (action in ('edit', 'approve', 'reject')),
  reviewer_id uuid not null references public.reviewer_profiles(user_id) on delete restrict,
  reviewer_name text not null check (
    reviewer_name = btrim(reviewer_name)
    and char_length(reviewer_name) between 1 and 80
  ),
  note text check (note is null or char_length(note) <= 2000),
  revision_payload jsonb,
  base_artifact_sha256 text not null check (base_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  target_revision_id uuid references public.review_events(id) on delete restrict,
  created_at timestamptz not null default now(),
  constraint review_event_payload_shape check (
    (action = 'edit' and jsonb_typeof(revision_payload) = 'object')
    or (action in ('approve', 'reject') and revision_payload is null)
  ),
  constraint review_reject_requires_note check (
    action <> 'reject' or nullif(btrim(note), '') is not null
  )
);

create index review_events_target_created_idx
  on public.review_events(run_id, stage, item_type, item_key, created_at desc);

create index review_events_reviewer_created_idx
  on public.review_events(reviewer_id, created_at desc);

create function public.set_reviewer_profile_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.user_id = old.user_id;
  new.created_at = old.created_at;
  new.updated_at = now();
  return new;
end;
$$;

create trigger reviewer_profiles_updated_at
before update on public.reviewer_profiles
for each row execute function public.set_reviewer_profile_updated_at();

create function public.validate_review_event_revision()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.target_revision_id is not null and not exists (
    select 1
    from public.review_events parent
    where parent.id = new.target_revision_id
      and parent.action = 'edit'
      and parent.run_id = new.run_id
      and parent.stage = new.stage
      and parent.item_type = new.item_type
      and parent.item_key = new.item_key
  ) then
    raise exception 'target_revision_id must reference an edit event for the same review target';
  end if;
  return new;
end;
$$;

create trigger review_event_revision_target
before insert on public.review_events
for each row execute function public.validate_review_event_revision();

create function public.reject_review_event_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'review_events is append-only';
end;
$$;

create trigger review_events_append_only
before update or delete on public.review_events
for each row execute function public.reject_review_event_mutation();

alter table public.review_runs enable row level security;
alter table public.reviewer_profiles enable row level security;
alter table public.review_events enable row level security;

revoke all on public.review_runs, public.reviewer_profiles, public.review_events
from anon, authenticated;

grant select on public.review_runs, public.review_events to anon, authenticated;
grant select, insert, update on public.reviewer_profiles to authenticated;
grant insert on public.review_events to authenticated;

create policy review_runs_public_read
on public.review_runs for select
to anon, authenticated
using (is_public);

create policy reviewer_profiles_read_own
on public.reviewer_profiles for select
to authenticated
using ((select auth.uid()) = user_id);

create policy reviewer_profiles_insert_own
on public.reviewer_profiles for insert
to authenticated
with check ((select auth.uid()) = user_id);

create policy reviewer_profiles_update_own
on public.reviewer_profiles for update
to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy review_events_public_read
on public.review_events for select
to anon, authenticated
using (
  exists (
    select 1
    from public.review_runs run
    where run.id = review_events.run_id
      and run.is_public
  )
);

create policy review_events_insert_own_on_open_run
on public.review_events for insert
to authenticated
with check (
  reviewer_id = (select auth.uid())
  and reviewer_name = (
    select profile.display_name
    from public.reviewer_profiles profile
    where profile.user_id = (select auth.uid())
  )
  and exists (
    select 1
    from public.review_runs run
    where run.id = review_events.run_id
      and run.is_public
      and run.review_open
  )
);
