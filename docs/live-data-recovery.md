# Live job data and refreshes

The static dashboard reads the Render API. On the free hosting plan an idle
service may take over 50 seconds to start. Read requests allow 60 seconds and
retry once after a network error, timeout, or HTTP 502/503/504. Writes retain
their existing timeout and are not automatically replayed. India Insights also
retries the complete paginated load before showing labelled sample data.

The daily official-board refresh uses `--preserve-uncollected-sources`. It
replaces configured sources and retains existing records from other sources,
including the opt-in Apify collections. Preserved records keep their original
collection timestamps and observed/unverified status; the same 30-day posting
date filter removes stale or undated rows. This does not start Apify actors.
Collection reports count newly collected rows; API metadata counts the combined
published snapshot. Both publication workflows share a concurrency group.

Check `/api/health`, `/api/jobs?page_size=100`, and `/api/data-quality` after a
deployment. For snapshots exceeding 100 jobs, check subsequent pages too.
