-- AI Empowerment Observatory
-- Stage 7B.1A: align the database with the agreed event-level methodology.
-- Run once in Supabase SQL Editor.

create extension if not exists pgcrypto;

create table if not exists public.events (
  event_id uuid primary key default gen_random_uuid(),
  canonical_event_key text not null unique,
  event_title text not null,
  event_summary text,
  event_date date,
  primary_country_iso3 text
    check (primary_country_iso3 is null or char_length(primary_country_iso3) = 3),
  additional_country_iso3 text[] not null default '{}',
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  clustering_method text,
  cluster_confidence numeric(5,4)
    check (
      cluster_confidence is null or
      (cluster_confidence >= 0 and cluster_confidence <= 1)
    ),
  requires_cluster_review boolean not null default false,
  cluster_review_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.event_articles (
  event_article_id uuid primary key default gen_random_uuid(),
  event_id uuid not null
    references public.events(event_id)
    on delete cascade,
  article_id text not null
    references public.articles(article_id)
    on delete cascade,
  is_canonical_source boolean not null default false,
  similarity_score numeric(5,4)
    check (
      similarity_score is null or
      (similarity_score >= 0 and similarity_score <= 1)
    ),
  created_at timestamptz not null default now(),
  unique (event_id, article_id)
);

create table if not exists public.event_classifications (
  event_classification_id uuid primary key default gen_random_uuid(),
  classification_run_id uuid not null
    references public.classification_runs(classification_run_id)
    on delete cascade,
  event_id uuid not null
    references public.events(event_id)
    on delete cascade,

  ai_relevant boolean not null,

  empowerment_status text not null
    check (
      empowerment_status in (
        'expanding',
        'contracting',
        'mixed',
        'non_empowerment',
        'unclear'
      )
    ),

  narrative_frame text not null
    check (
      narrative_frame in (
        'opportunity',
        'threat',
        'contested',
        'descriptive_neutral',
        'unclear'
      )
    ),

  distribution_breadth text not null
    check (
      distribution_breadth in (
        'broad',
        'targeted',
        'concentrated',
        'unclear'
      )
    ),

  dominant_dimension text
    check (
      dominant_dimension is null or
      dominant_dimension in (
        'operational',
        'creative',
        'agentic',
        'normative'
      )
    ),

  ai_authority_shift text not null
    check (
      ai_authority_shift in (
        'increasing',
        'decreasing',
        'unchanged',
        'unclear'
      )
    ),

  topic text
    check (
      topic is null or
      topic in (
        'work_employment',
        'business_productivity',
        'consumer_services',
        'creativity_ip',
        'education_research',
        'healthcare',
        'government_regulation',
        'privacy_security',
        'infrastructure_investment',
        'other'
      )
    ),

  content_basis text not null
    check (
      content_basis in (
        'headline_only',
        'headline_and_snippet',
        'article_summary',
        'multiple_sources',
        'full_text'
      )
    ),

  confidence numeric(5,4) not null
    check (confidence >= 0 and confidence <= 1),

  reasoning text not null,
  requires_review boolean not null default true,
  review_reason text,
  raw_output jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),

  unique (classification_run_id, event_id)
);

create table if not exists public.event_dimensions (
  event_dimension_id uuid primary key default gen_random_uuid(),
  event_classification_id uuid not null
    references public.event_classifications(event_classification_id)
    on delete cascade,

  dimension text not null
    check (
      dimension in (
        'operational',
        'creative',
        'agentic',
        'normative'
      )
    ),

  present boolean not null,

  direction text
    check (
      direction is null or
      direction in (
        'expanding',
        'contracting',
        'mixed',
        'unclear'
      )
    ),

  confidence numeric(5,4)
    check (
      confidence is null or
      (confidence >= 0 and confidence <= 1)
    ),

  reasoning text,

  unique (event_classification_id, dimension),

  constraint event_dimension_direction_consistency
    check (
      (present = false and direction is null) or
      (present = true and direction is not null)
    )
);

create table if not exists public.event_human_reviews (
  event_human_review_id uuid primary key default gen_random_uuid(),
  event_classification_id uuid not null
    references public.event_classifications(event_classification_id)
    on delete cascade,
  reviewer_name text not null,
  review_status text not null
    check (
      review_status in (
        'pending',
        'accepted',
        'corrected',
        'rejected'
      )
    ),
  final_ai_relevant boolean,
  final_empowerment_status text
    check (
      final_empowerment_status is null or
      final_empowerment_status in (
        'expanding',
        'contracting',
        'mixed',
        'non_empowerment',
        'unclear'
      )
    ),
  final_narrative_frame text
    check (
      final_narrative_frame is null or
      final_narrative_frame in (
        'opportunity',
        'threat',
        'contested',
        'descriptive_neutral',
        'unclear'
      )
    ),
  final_distribution_breadth text
    check (
      final_distribution_breadth is null or
      final_distribution_breadth in (
        'broad',
        'targeted',
        'concentrated',
        'unclear'
      )
    ),
  notes text,
  reviewed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_events_event_date
  on public.events(event_date desc);

create index if not exists idx_events_country
  on public.events(primary_country_iso3);

create index if not exists idx_events_last_seen
  on public.events(last_seen_at desc);

create index if not exists idx_event_articles_event
  on public.event_articles(event_id);

create index if not exists idx_event_articles_article
  on public.event_articles(article_id);

create index if not exists idx_event_classifications_event
  on public.event_classifications(event_id);

create index if not exists idx_event_classifications_status
  on public.event_classifications(empowerment_status);

create index if not exists idx_event_classifications_frame
  on public.event_classifications(narrative_frame);

create index if not exists idx_event_classifications_breadth
  on public.event_classifications(distribution_breadth);

create index if not exists idx_event_classifications_review
  on public.event_classifications(requires_review, confidence);

create index if not exists idx_event_dimensions_classification
  on public.event_dimensions(event_classification_id);

create index if not exists idx_event_human_reviews_classification
  on public.event_human_reviews(event_classification_id);

alter table public.events enable row level security;
alter table public.event_articles enable row level security;
alter table public.event_classifications enable row level security;
alter table public.event_dimensions enable row level security;
alter table public.event_human_reviews enable row level security;

revoke all on table public.events from anon, authenticated;
revoke all on table public.event_articles from anon, authenticated;
revoke all on table public.event_classifications from anon, authenticated;
revoke all on table public.event_dimensions from anon, authenticated;
revoke all on table public.event_human_reviews from anon, authenticated;

grant all on table public.events to service_role;
grant all on table public.event_articles to service_role;
grant all on table public.event_classifications to service_role;
grant all on table public.event_dimensions to service_role;
grant all on table public.event_human_reviews to service_role;

comment on table public.events is
  'Unique real-world AI developments. Country indices count events, not article volume.';

comment on table public.event_articles is
  'Links multiple news articles/sources to one real-world event cluster.';

comment on table public.event_classifications is
  'Event-level Observatory classification: human empowerment direction, narrative frame, distribution breadth, AI authority shift and practical topic.';

comment on table public.event_dimensions is
  'Parallel multi-label operational, creative, agentic and normative dimensions. No hierarchy and no operational residual.';

comment on table public.event_human_reviews is
  'Human audit and correction of event-level model classifications.';

comment on table public.article_classifications is
  'Legacy Stage 7B draft table. Observatory v1 classifies unique events in event_classifications instead.';

comment on table public.classification_dimensions is
  'Legacy Stage 7B draft table. Observatory v1 uses event_dimensions instead.';

comment on table public.stakeholder_effects is
  'Legacy Stage 7B draft table. Detailed stakeholder taxonomy is not part of the Observatory v1 headline index.';
