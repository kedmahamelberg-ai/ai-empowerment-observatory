-- AIEO Brief Phase 1.1
-- Fix service-role privileges for the private Brief foundation tables.
-- Safe to run after the Phase 1 migration. It does not recreate or delete data.

grant usage on schema public to service_role;

grant select, insert, update, delete on table public.brief_article_content_snapshots to service_role;
grant select, insert, update, delete on table public.brief_article_fetch_attempts to service_role;
grant select, insert, update, delete on table public.brief_source_rights_registry to service_role;
grant select, insert, update, delete on table public.brief_generated_artifacts to service_role;
grant select, insert, update, delete on table public.brief_research_works to service_role;
grant select, insert, update, delete on table public.brief_stories to service_role;
grant select, insert, update, delete on table public.brief_story_sources to service_role;
grant select, insert, update, delete on table public.brief_consent_receipts to service_role;
grant select, insert, update, delete on table public.brief_behavior_events to service_role;
grant select, insert, update, delete on table public.brief_ad_campaigns to service_role;
grant select, insert, update, delete on table public.brief_ad_creatives to service_role;
grant select, insert, update, delete on table public.brief_ad_placements to service_role;
grant select, insert, update, delete on table public.brief_data_provenance to service_role;

-- RLS remains enabled. No anon or authenticated-user grants are added here.
-- service_role is used only by trusted server-side and GitHub Actions workflows.
