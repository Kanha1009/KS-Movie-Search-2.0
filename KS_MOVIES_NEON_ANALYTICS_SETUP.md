KS MOVIES — ANALYTICS SETUP

Changed files in this patch:
- plugins/analytics_store.py: uses Neon/PostgreSQL when KS_ANALYTICS_DATABASE_URL is set; otherwise retains SQLite fallback.
- requirements.txt: adds the PostgreSQL driver needed for Neon.

Koyeb environment variables:
1. KS_ANALYTICS_DATABASE_URL — paste the full Neon PostgreSQL connection string. Keep it secret.
2. KS_ANALYTICS_ADMIN_TOKEN — keep your existing long, random admin token.
3. GA4_MEASUREMENT_ID — keep your existing GA4 Measurement ID if configured.
4. KS_ANALYTICS_DB_PATH — only relevant to the SQLite fallback; it is not used when KS_ANALYTICS_DATABASE_URL is set.

Deploy:
1. Replace only the two code/dependency files listed above at their exact paths.
2. Ensure KS_ANALYTICS_DATABASE_URL is set in Koyeb.
3. Redeploy the service. The first analytics request will create the analytics_events table in Neon automatically.
4. Open /admin/analytics and check that the dashboard loads.

Important:
- This change does not migrate existing SQLite analytics rows to Neon. Neon starts with new events after deployment.
- The website UI, movie-search logic, Telegram bot logic, and admin dashboard file are not changed.
- No raw search text, IP addresses, account IDs, or browser identifiers are stored by this analytics store.
