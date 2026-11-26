# Observatory traffic measurement

Use the first-party counter for page views, Search Console for clicks from
Google Search, and Google Analytics for directional reports. Filter Google
Analytics reports to host name `observatory.hamelberg-ai.com` because the
Observatory and The Brief share a stream. Use a URL filter for the same host
in the domain-wide Search Console property.

The site counts each visible load once, with no analytics cookies or
visitor IDs. The database receives only a known public page path and a
category. Google, ChatGPT and Claude are identified by an allowed referrer
hostname or an allowed `utm_source`. A visit with no source signal is counted
as `direct_or_unknown`. This counter does not measure unique readers. Rows
older than 13 months expire the next time a view is recorded.

Run this in the Observatory project's SQL Editor for a recent daily
breakdown. Separate totals by `source` to see attributable visits.

```sql
select day, source, sum(total) as page_views
from observatory_measurement_private.daily_page_views
where day >= (now() at time zone 'Europe/Amsterdam')::date - 29
group by day, source
order by day desc, source;
```
