# Full-Stack Backend Assignment

This assignment is backend-focused. There is no UI requirement; a CLI, a set of scripts, or a couple of API endpoints tested with curl/Postman are all acceptable. We care about how you handle data correctness and failure, not how anything looks.

## Problem Statement 1: Can you build a sync pipeline that doesn't lie or duplicate data?

You're given 2-3 data sources that each expose records in a different shape: a CRM, a payments processor, and a calendar/events feed. Each source supports an incremental fetch (give it a cursor/timestamp, get back "what changed since then") and a full fetch (get everything). Set up a free HubSpot developer account for the CRM source, and the free Google Calendar API for the events source, and seed each with a handful of sample records yourself. Build a pipeline that ingests all three into one normalized schema despite each source naming and shaping fields differently; that falls back to a full backfill instead of silently losing data or crashing when an incremental cursor goes stale or a source rejects it (e.g. a 410/expired-token error); that writes idempotently, so the same webhook firing twice or the sync job re-running back-to-back never produces duplicate rows; and that keeps going when one source is down or returns garbage, so the other two still land their data instead of the whole run wedging.

## Problem Statement 2: Can you build one metrics number that never drifts?

You're given normalized transaction/invoice data from multiple source systems. Each source uses a different status vocabulary for "this money is actually collected," e.g., one source says paid, another says succeeded, another says completed, and others use pending, voided, refunded, or failed. Pull real sample transactions from a free finance apps test-mode account, and store your normalized data in a free Supabase Postgres project. Build a single metrics service that computes total revenue collected for an arbitrary date range across all sources using one canonical definition of "collected" and an allow-list of statuses that count, not an exclusion list of statuses that don't, since exclusion lists silently let new or unexpected statuses through as revenue. Expose that same number through two different views, e.g., a single summary total endpoint and a day-by-day or week-by-week breakdown endpoint, and make sure both views always agree, even after a new source system or a new status value is added. Structure the code so that if someone later adds a second, slightly different way of computing this same number somewhere else in the codebase, something would actually catch it.

## What to Submit

1. Live deployment: deploy your solution on Render's free tier. We should be able to hit a real running endpoint or trigger a real running job, not just read code.
2. Short demo video (5 minutes max): walk through what you built and run it live, including at least one failure/edge case from the problem statement.
3. GitHub repo link (public link): with a README.md explaining how to run it locally and what tradeoffs you made.
4. Sources & references: a short list (in the README is fine) of any docs, blog posts, Stack Overflow answers, libraries, or free-tier accounts/APIs you used; we want to see how you research.
5. AI usage: if you used Claude, ChatGPT, Copilot, or any AI tool while building this, say so and share the chat history/export (Claude conversation share link, ChatGPT shared link, etc.) alongside your repo. We use AI tools heavily ourselves. We want to see how you direct and review AI output, not whether you used it.
