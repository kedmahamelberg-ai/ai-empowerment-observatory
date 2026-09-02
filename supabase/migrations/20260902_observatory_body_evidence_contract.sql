-- AIEO shared private body-evidence contract
--
-- Stores legally collected article bodies once in Supabase and exposes the
-- same best-available evidence to Observatory classification and AIEO Brief.
-- Body text remains private: anon and authenticated roles receive no grants.

create extension if not exists pgcrypto;

create table if not exists public.brief_article_fetch_attempts (
  fetch_attempt_id uuid primary key default gen_random_uuid(),
  article_id text not null references public.articles(article_id) on delete cascade,
  source_url text not null,
  source_domain text,
  workflow_run_id text,
  retrieval_method text not null,
  http_status integer,
  robots_allowed boolean,
  tdm_reservation boolean,
  tdm_policy_url text,
  paywall_detected boolean,
  outcome text not null,
  response_content_type text,
  response_bytes bigint,
  elapsed_ms integer,
  metadata jsonb not null default '{}'::jsonb,
  attempted_at timestamptz not null default now()
);

create table if not exists public.brief_article_content_snapshots (
  snapshot_id uuid primary key default gen_random_uuid(),
  article_id text not null references public.articles(article_id) on delete cascade,
  source_url text not null,
  source_domain text,
  retrieval_method text not null,
  http_status integer,
  mime_type text,
  extracted_title text,
  body_text text not null,
  word_count integer not null check (word_count >= 0),
  text_sha256 text not null,
  extraction_quality numeric(6,5),
  content_basis text not null default 'full_page_extraction',
  rights_status text not null,
  rights_basis text not null,
  robots_allowed boolean,
  tdm_reservation boolean,
  tdm_policy_url text,
  paywall_detected boolean not null default false,
  is_current boolean not null default true,
  retrieved_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (article_id, text_sha256)
);

create table if not exists public.brief_article_evidence_snapshots (
  evidence_id uuid primary key default gen_random_uuid(),
  article_id text not null references public.articles(article_id) on delete cascade,
  evidence_type text not null check (evidence_type in ('source_excerpt', 'discovery_snippet')),
  evidence_text text not null,
  evidence_language text,
  publisher text,
  source_url text,
  source_domain text,
  collection_run_id uuid references public.collection_runs(run_id) on delete set null,
  search_id uuid references public.search_runs(search_id) on delete set null,
  search_country_iso3 text,
  search_language text,
  search_rank integer,
  raw_storage_path text,
  raw_field text,
  text_sha256 text not null,
  is_current boolean not null default true,
  created_at timestamptz not null default now(),
  unique (article_id, evidence_type, text_sha256, search_id)
);

create index if not exists idx_brief_fetch_attempt_article
  on public.brief_article_fetch_attempts(article_id, attempted_at desc);
create index if not exists idx_brief_body_article
  on public.brief_article_content_snapshots(article_id, retrieved_at desc);
create unique index if not exists ux_brief_body_one_current
  on public.brief_article_content_snapshots(article_id) where is_current;
create index if not exists idx_brief_evidence_article
  on public.brief_article_evidence_snapshots(article_id, created_at desc);

create or replace view public.brief_article_best_evidence as
select
  a.article_id,
  a.headline,
  a.publisher,
  a.canonical_url as source_url,
  a.published_at,
  chosen.evidence_basis,
  chosen.evidence_text,
  chosen.evidence_at,
  chosen.evidence_ref,
  chosen.extraction_quality
from public.articles a
left join lateral (
  select candidate.evidence_basis,
         candidate.evidence_text,
         candidate.evidence_at,
         candidate.evidence_ref,
         candidate.extraction_quality
  from (
    select 1 as preference,
           'full_source'::text as evidence_basis,
           body.body_text as evidence_text,
           body.retrieved_at as evidence_at,
           body.snapshot_id as evidence_ref,
           body.extraction_quality
    from public.brief_article_content_snapshots body
    where body.article_id = a.article_id and body.is_current

    union all

    select case when ev.evidence_type = 'source_excerpt' then 2 else 3 end,
           ev.evidence_type,
           ev.evidence_text,
           ev.created_at,
           ev.evidence_id,
           null::numeric
    from public.brief_article_evidence_snapshots ev
    where ev.article_id = a.article_id and ev.is_current

    union all

    select 4,
           'discovery_snippet',
           coalesce(
             nullif(a.source_metadata->>'snippet', ''),
             nullif(a.source_metadata->>'description', ''),
             nullif(a.source_metadata->>'summary', ''),
             nullif(a.source_metadata->>'source_snippet', '')
           ),
           a.updated_at,
           null::uuid,
           null::numeric
    where coalesce(
      nullif(a.source_metadata->>'snippet', ''),
      nullif(a.source_metadata->>'description', ''),
      nullif(a.source_metadata->>'summary', ''),
      nullif(a.source_metadata->>'source_snippet', '')
    ) is not null

    union all

    select 5,
           'headline_only',
           a.headline,
           a.updated_at,
           null::uuid,
           null::numeric
  ) candidate
  order by candidate.preference, candidate.evidence_at desc nulls last
  limit 1
) chosen on true;

create or replace view public.brief_event_source_evidence as
select
  e.event_id,
  e.event_title,
  e.event_summary,
  e.event_date,
  a.article_id,
  ea.is_canonical_source,
  a.headline as source_headline,
  a.publisher,
  a.canonical_url as source_url,
  a.published_at,
  best.evidence_basis,
  best.evidence_text,
  best.evidence_at,
  best.evidence_ref,
  best.extraction_quality
from public.events e
join public.event_articles ea on ea.event_id = e.event_id
join public.articles a on a.article_id = ea.article_id
left join public.brief_article_best_evidence best on best.article_id = a.article_id;

create or replace view public.brief_event_evidence_readiness as
select
  event_id,
  max(event_title) as event_title,
  count(*)::integer as source_count,
  count(*) filter (where evidence_basis = 'full_source')::integer as full_source_count,
  count(*) filter (where evidence_basis = 'source_excerpt')::integer as source_excerpt_count,
  count(*) filter (where evidence_basis = 'discovery_snippet')::integer as discovery_snippet_count,
  count(*) filter (where evidence_basis = 'headline_only' or evidence_basis is null)::integer as headline_only_count,
  case
    when count(*) filter (where evidence_basis = 'full_source') >= 2 then 'strong_multi_source'
    when count(*) filter (where evidence_basis = 'full_source') >= 1
         and count(*) > count(*) filter (where evidence_basis = 'full_source') then 'mixed_with_full_source'
    when count(*) filter (where evidence_basis = 'full_source') = 1 then 'single_full_source'
    when count(*) filter (where evidence_basis in ('source_excerpt', 'discovery_snippet')) >= 1 then 'contextual_only'
    else 'headline_only'
  end as editorial_evidence_level
from public.brief_event_source_evidence
group by event_id;

alter table public.brief_article_fetch_attempts enable row level security;
alter table public.brief_article_content_snapshots enable row level security;
alter table public.brief_article_evidence_snapshots enable row level security;

revoke all on table public.brief_article_fetch_attempts from anon, authenticated;
revoke all on table public.brief_article_content_snapshots from anon, authenticated;
revoke all on table public.brief_article_evidence_snapshots from anon, authenticated;
revoke all on table public.brief_article_best_evidence from anon, authenticated;
revoke all on table public.brief_event_source_evidence from anon, authenticated;
revoke all on table public.brief_event_evidence_readiness from anon, authenticated;

grant select, insert, update, delete on table public.brief_article_fetch_attempts to service_role;
grant select, insert, update, delete on table public.brief_article_content_snapshots to service_role;
grant select, insert, update, delete on table public.brief_article_evidence_snapshots to service_role;
grant select on table public.brief_article_best_evidence to service_role;
grant select on table public.brief_event_source_evidence to service_role;
grant select on table public.brief_event_evidence_readiness to service_role;

comment on table public.brief_article_content_snapshots is
  'Private versioned article-body evidence shared by Observatory classification and AIEO Brief; never published verbatim.';
comment on view public.brief_article_best_evidence is
  'Best private evidence per article: full source, excerpt, discovery snippet, then headline.';
