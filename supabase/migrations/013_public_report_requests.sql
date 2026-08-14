-- AI Empowerment Observatory
-- Public report request / newsletter signup table.
-- The public client may INSERT only. It cannot read, update, or delete rows.

create extension if not exists pgcrypto;

create table if not exists public.report_requests (
  report_request_id uuid primary key default gen_random_uuid(),
  first_name text not null check (char_length(first_name) between 1 and 120),
  last_name text not null check (char_length(last_name) between 1 and 120),
  email text not null check (position('@' in email) > 1),
  report_slug text not null,
  privacy_acknowledged boolean not null check (privacy_acknowledged = true),
  newsletter_opt_in boolean not null default false,
  source text not null default 'report_page',
  created_at timestamptz not null default now(),
  unique (email, report_slug)
);

alter table public.report_requests enable row level security;

revoke all on table public.report_requests from anon, authenticated;
grant insert on table public.report_requests to anon, authenticated;
grant all on table public.report_requests to service_role;

drop policy if exists "public may request a report" on public.report_requests;

create policy "public may request a report"
on public.report_requests
for insert
to anon, authenticated
with check (
  privacy_acknowledged = true
  and char_length(first_name) between 1 and 120
  and char_length(last_name) between 1 and 120
  and position('@' in email) > 1
);

comment on table public.report_requests is
  'Requests for public Observatory reports, with a separate optional newsletter choice. Public roles have insert-only access.';
