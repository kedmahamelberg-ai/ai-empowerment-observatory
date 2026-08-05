-- AI Empowerment Observatory
-- Stage 7A.2: initial private historical schema
-- Run this once in Supabase SQL Editor.

create extension if not exists pgcrypto;

create table if not exists public.collection_runs (
  run_id uuid primary key default gen_random_uuid(),
  run_key text not null unique,
  started_at timestamptz not null,
  completed_at timestamptz,
  status text not null
    check (status in ('running', 'success', 'partial', 'failed')),
  configured_country_count integer not null default 0
    check (configured_country_count >= 0),
  successful_search_count integer not null default 0
    check (successful_search_count >= 0),
  failed_search_count integer not null default 0
    check (failed_search_count >= 0),
  candidate_count integer not null default 0
    check (candidate_count >= 0),
  workflow_run_id text,
  collector_version text not null default '7A.2',
  created_at timestamptz not null default now()
);

create table if not exists public.search_runs (
  search_id uuid primary key default gen_random_uuid(),
  run_id uuid not null
    references public.collection_runs(run_id)
    on delete cascade,
  country_name text not null,
  country_iso2 text not null
    check (char_length(country_iso2) = 2),
  country_iso3 text not null
    check (char_length(country_iso3) = 3),
  search_language text not null,
  search_query text not null,
  result_count integer not null default 0
    check (result_count >= 0),
  status text not null
    check (status in ('success', 'error')),
  error_message text,
  raw_storage_path text,
  serpapi_search_id text,
  created_at timestamptz not null default now(),
  unique (run_id, country_iso3, search_language)
);

create table if not exists public.articles (
  article_id text primary key,
  canonical_url text not null unique,
  headline text not null,
  publisher text,
  published_at timestamptz,
  displayed_date text,
  language text,
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  source_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.article_observations (
  observation_id uuid primary key default gen_random_uuid(),
  run_id uuid not null
    references public.collection_runs(run_id)
    on delete cascade,
  search_id uuid not null
    references public.search_runs(search_id)
    on delete cascade,
  article_id text not null
    references public.articles(article_id)
    on delete cascade,
  search_country_iso3 text not null
    check (char_length(search_country_iso3) = 3),
  search_language text not null,
  search_rank integer
    check (search_rank is null or search_rank > 0),
  observed_at timestamptz not null,
  observation_metadata jsonb not null default '{}'::jsonb,
  unique (run_id, search_id, article_id)
);

create index if not exists idx_collection_runs_started_at
  on public.collection_runs (started_at desc);

create index if not exists idx_search_runs_run_id
  on public.search_runs (run_id);

create index if not exists idx_search_runs_country_iso3
  on public.search_runs (country_iso3);

create index if not exists idx_articles_published_at
  on public.articles (published_at desc);

create index if not exists idx_articles_last_seen_at
  on public.articles (last_seen_at desc);

create index if not exists idx_article_observations_run_id
  on public.article_observations (run_id);

create index if not exists idx_article_observations_article_id
  on public.article_observations (article_id);

alter table public.collection_runs enable row level security;
alter table public.search_runs enable row level security;
alter table public.articles enable row level security;
alter table public.article_observations enable row level security;

revoke all on table public.collection_runs from anon, authenticated;
revoke all on table public.search_runs from anon, authenticated;
revoke all on table public.articles from anon, authenticated;
revoke all on table public.article_observations from anon, authenticated;

grant usage on schema public to service_role;
grant all on table public.collection_runs to service_role;
grant all on table public.search_runs to service_role;
grant all on table public.articles to service_role;
grant all on table public.article_observations to service_role;

comment on table public.collection_runs is
  'One row per scheduled or manually triggered collection execution.';

comment on table public.search_runs is
  'One row per localized Google News search within a collection run.';

comment on table public.articles is
  'One persistent row per canonical article across all collection runs.';

comment on table public.article_observations is
  'One row each time an article is observed in a particular search run.';
