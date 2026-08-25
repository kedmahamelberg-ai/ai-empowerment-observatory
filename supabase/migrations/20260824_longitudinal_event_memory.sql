-- AI Empowerment Observatory
-- Phase 5: longitudinal event memory, story families, recurring-coverage
-- occurrences, reconciliation provenance, and release revision history.
--
-- Run once in Supabase SQL Editor before deploying the Phase 5 workflows.
-- This migration is additive. It does not delete or rename existing objects.

create extension if not exists pgcrypto;

create table if not exists public.story_families (
    story_family_id uuid primary key default gen_random_uuid(),
    canonical_story_key text not null unique,
    story_title text not null,
    first_event_date date,
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    status text not null default 'active'
        check (status in ('active', 'closed', 'review')),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.events
    add column if not exists story_family_id uuid
        references public.story_families(story_family_id) on delete set null;
alter table public.events
    add column if not exists canonical_event_id uuid
        references public.events(event_id) on delete set null;
alter table public.events add column if not exists canonicalized_at timestamptz;
alter table public.events add column if not exists canonicalization_reason text;
alter table public.events add column if not exists last_reconciled_at timestamptz;
alter table public.events add column if not exists registry_version text;
alter table public.events
    add column if not exists registry_metadata jsonb not null default '{}'::jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'events_canonical_event_not_self'
    ) then
        alter table public.events
            add constraint events_canonical_event_not_self
            check (canonical_event_id is null or canonical_event_id <> event_id);
    end if;
end $$;

create index if not exists idx_events_story_family
    on public.events(story_family_id);
create index if not exists idx_events_canonical_event
    on public.events(canonical_event_id)
    where canonical_event_id is not null;
create index if not exists idx_events_last_reconciled
    on public.events(last_reconciled_at desc nulls last);

create table if not exists public.event_reconciliation_runs (
    reconciliation_run_id uuid primary key default gen_random_uuid(),
    run_key text not null unique,
    mode text not null
        check (mode in ('weekly', 'monthly', 'quarterly', 'annual', 'manual')),
    collection_run_id uuid references public.collection_runs(run_id) on delete set null,
    resolution_run_id uuid,
    pool_start_at timestamptz,
    pool_considered_through timestamptz not null,
    dry_run boolean not null default false,
    started_at timestamptz not null,
    completed_at timestamptz,
    status text not null
        check (status in ('running', 'success', 'partial', 'failed')),
    candidate_count integer not null default 0 check (candidate_count >= 0),
    auto_merge_count integer not null default 0 check (auto_merge_count >= 0),
    follow_on_count integer not null default 0 check (follow_on_count >= 0),
    review_count integer not null default 0 check (review_count >= 0),
    occurrence_count integer not null default 0 check (occurrence_count >= 0),
    registry_snapshot_id text,
    notes text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

alter table public.event_reconciliation_runs
    add column if not exists dry_run boolean not null default false;

create index if not exists idx_event_reconciliation_runs_started
    on public.event_reconciliation_runs(started_at desc);
create index if not exists idx_event_reconciliation_runs_live
    on public.event_reconciliation_runs(completed_at desc)
    where status = 'success' and dry_run = false;

create table if not exists public.event_relationships (
    relationship_id uuid primary key default gen_random_uuid(),
    from_event_id uuid not null references public.events(event_id) on delete cascade,
    to_event_id uuid not null references public.events(event_id) on delete cascade,
    story_family_id uuid references public.story_families(story_family_id) on delete set null,
    relationship_type text not null
        check (relationship_type in (
            'follow_on_development',
            'possible_same_event',
            'same_topic_only',
            'same_event_alias',
            'supersedes',
            'corrects',
            'updates'
        )),
    confidence numeric(5,4)
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    source text not null default 'longitudinal_reconciler'
        check (source in ('resolver', 'longitudinal_reconciler', 'human')),
    status text not null default 'proposed'
        check (status in ('proposed', 'accepted', 'rejected')),
    resolution_run_id uuid,
    reconciliation_run_id uuid
        references public.event_reconciliation_runs(reconciliation_run_id)
        on delete set null,
    evidence jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    reviewed_at timestamptz,
    unique (from_event_id, to_event_id, relationship_type, source)
);

create index if not exists idx_event_relationships_from
    on public.event_relationships(from_event_id, relationship_type, status);
create index if not exists idx_event_relationships_to
    on public.event_relationships(to_event_id, relationship_type, status);

create table if not exists public.event_occurrences (
    occurrence_id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.events(event_id) on delete cascade,
    effective_event_id uuid not null references public.events(event_id) on delete cascade,
    story_family_id uuid references public.story_families(story_family_id) on delete set null,
    article_id text not null references public.articles(article_id) on delete cascade,
    collection_run_id uuid not null references public.collection_runs(run_id) on delete cascade,
    release_id text,
    appearance_type text not null
        check (appearance_type in (
            'first_event_coverage',
            'same_event_new_coverage',
            'same_article_rediscovered',
            'follow_on_development',
            'possible_historical_match',
            'historical_backfill'
        )),
    article_published_at timestamptz,
    observed_at timestamptz not null,
    previous_event_coverage_at timestamptz,
    days_since_event_first_seen numeric(12,3),
    days_since_previous_coverage numeric(12,3),
    publisher text,
    source_domain text,
    search_markets text[] not null default '{}',
    first_source_appearance boolean not null default false,
    first_market_appearances text[] not null default '{}',
    resolution_track text not null default 'recent'
        check (resolution_track in ('exact', 'recent', 'historical', 'reconciliation')),
    relationship_confidence numeric(5,4)
        check (
            relationship_confidence is null
            or (relationship_confidence >= 0 and relationship_confidence <= 1)
        ),
    resolver_version text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (collection_run_id, article_id)
);

create unique index if not exists ux_event_occurrences_collection_article
    on public.event_occurrences(collection_run_id, article_id);
create index if not exists idx_event_occurrences_effective_event
    on public.event_occurrences(effective_event_id, observed_at);
create index if not exists idx_event_occurrences_article
    on public.event_occurrences(article_id, observed_at);
create index if not exists idx_event_occurrences_release
    on public.event_occurrences(release_id)
    where release_id is not null;
create index if not exists idx_event_occurrences_appearance
    on public.event_occurrences(appearance_type, observed_at);

create table if not exists public.event_revisions (
    event_revision_id uuid primary key default gen_random_uuid(),
    reconciliation_run_id uuid
        references public.event_reconciliation_runs(reconciliation_run_id)
        on delete set null,
    event_id uuid not null references public.events(event_id) on delete cascade,
    prior_effective_event_id uuid references public.events(event_id) on delete set null,
    new_effective_event_id uuid references public.events(event_id) on delete set null,
    revision_type text not null
        check (revision_type in (
            'merge', 'unmerge', 'story_link', 'story_unlink', 'metadata_correction'
        )),
    reason text not null,
    evidence jsonb not null default '{}'::jsonb,
    applied_by text not null default 'longitudinal_reconciler',
    applied_at timestamptz not null default now()
);

create index if not exists idx_event_revisions_event
    on public.event_revisions(event_id, applied_at desc);

create table if not exists public.release_revision_events (
    release_revision_event_id uuid primary key default gen_random_uuid(),
    release_id text not null,
    from_revision integer not null check (from_revision >= 1),
    to_revision integer not null check (to_revision > from_revision),
    reconciliation_run_id uuid
        references public.event_reconciliation_runs(reconciliation_run_id)
        on delete set null,
    reason text not null,
    change_summary jsonb not null default '{}'::jsonb,
    changed_at timestamptz not null default now(),
    unique (release_id, to_revision)
);

create index if not exists idx_release_revision_events_release
    on public.release_revision_events(release_id, to_revision desc);

update public.events
set registry_version = coalesce(registry_version, 'event_registry_v1')
where registry_version is null;

alter table public.story_families enable row level security;
alter table public.event_relationships enable row level security;
alter table public.event_occurrences enable row level security;
alter table public.event_reconciliation_runs enable row level security;
alter table public.event_revisions enable row level security;
alter table public.release_revision_events enable row level security;

revoke all on table public.story_families from anon, authenticated;
revoke all on table public.event_relationships from anon, authenticated;
revoke all on table public.event_occurrences from anon, authenticated;
revoke all on table public.event_reconciliation_runs from anon, authenticated;
revoke all on table public.event_revisions from anon, authenticated;
revoke all on table public.release_revision_events from anon, authenticated;

grant all on table public.story_families to service_role;
grant all on table public.event_relationships to service_role;
grant all on table public.event_occurrences to service_role;
grant all on table public.event_reconciliation_runs to service_role;
grant all on table public.event_revisions to service_role;
grant all on table public.release_revision_events to service_role;

comment on table public.event_occurrences is
    'One durable article-event appearance per collection run. Supports recurring coverage, rediscovery, diffusion and replication-delay analysis.';
comment on table public.story_families is
    'Groups distinct follow-on developments within a continuing story without collapsing them into one event.';
comment on table public.event_revisions is
    'Append-only ledger of event canonicalization and story-family corrections.';
