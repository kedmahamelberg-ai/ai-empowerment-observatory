create schema if not exists observatory_measurement_private;
revoke all on schema observatory_measurement_private from public;
grant usage on schema observatory_measurement_private to anon, authenticated, service_role;

create table if not exists observatory_measurement_private.daily_page_views (
  day date not null,
  page_path text not null,
  source text not null check (
    source in ('google', 'chatgpt', 'claude', 'internal', 'other_referral', 'direct_or_unknown')
  ),
  total bigint not null default 1 check (total > 0),
  primary key (day, page_path, source)
);

alter table observatory_measurement_private.daily_page_views enable row level security;
revoke all on observatory_measurement_private.daily_page_views from public, anon, authenticated;
grant select on observatory_measurement_private.daily_page_views to service_role;

create or replace function observatory_measurement_private.record_page_view(p_path text, p_source text)
returns void language plpgsql security definer set search_path = '' as $$
declare
  view_day date := (now() at time zone 'Europe/Amsterdam')::date;
begin
  -- Anonymous page-load counts use only bounded public paths and source categories.
  if p_path is null or p_path not in (
    '/', '/about/', '/contact/', '/edu/', '/methodology/', '/privacy/',
    '/pro/', '/report/', '/reports/', '/status/'
  ) or p_source is null or p_source not in (
    'google', 'chatgpt', 'claude', 'internal', 'other_referral', 'direct_or_unknown'
  ) then
    raise exception 'Invalid Observatory page-view category' using errcode = '22023';
  end if;

  delete from observatory_measurement_private.daily_page_views
    where day < (view_day - interval '13 months')::date;
  insert into observatory_measurement_private.daily_page_views as counts (day, page_path, source)
    values (view_day, p_path, p_source)
    on conflict (day, page_path, source) do update set total = counts.total + 1;
end;
$$;

revoke all on function observatory_measurement_private.record_page_view(text, text) from public;
grant execute on function observatory_measurement_private.record_page_view(text, text)
  to anon, authenticated, service_role;

create or replace function public.observatory_record_page_view(p_path text, p_source text)
returns void language sql security invoker set search_path = '' as $$
  select observatory_measurement_private.record_page_view(p_path, p_source);
$$;

revoke all on function public.observatory_record_page_view(text, text) from public;
grant execute on function public.observatory_record_page_view(text, text)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
