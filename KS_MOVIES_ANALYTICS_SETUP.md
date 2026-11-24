KS MOVIES — ANALYTICS + PINNED NEW UPDATE SETUP

Changed files in this patch:
- website/index.html: aggregate search/click tracking and optional GA4 loader.
- website/admin_analytics.html: token-protected analytics dashboard UI.
- plugins/web_support.py: analytics endpoints/dashboard route and existing website APIs.
- plugins/analytics_store.py: privacy-conscious aggregate SQLite counter.
- plugins/detect_lazysms.py: New Updates promotes the pinned movie from configured DB_CHANNEL only.

Deployment notes:
1. Replace the existing files at the exact paths above and add the new files.
2. In Koyeb set KS_ANALYTICS_ADMIN_TOKEN to a long, random secret value.
3. Optional: set GA4_MEASUREMENT_ID to the GA4 web stream measurement ID (format G-XXXXXXXXXX).
4. Optional but recommended: mount persistent storage and set KS_ANALYTICS_DB_PATH to a database file inside that mount, e.g. /data/ks_movies_analytics.sqlite3. Without a persistent volume, local SQLite analytics may be lost on redeploy/restart.
5. Redeploy the service.
6. Open https://www.ksmovies.site/admin/analytics and enter the exact KS_ANALYTICS_ADMIN_TOKEN value to load the dashboard.

Pinned movie behavior:
- The code checks the pinned message in the Telegram channel configured by DB_CHANNEL.
- The pinned message must use the same database entry format as the website's movie catalog (a URL at the beginning and movie title in parentheses).
- If the pinned message cannot be read or does not match that format, the existing New Updates ordering continues unchanged.
- Only the pinned movie is promoted; other entries keep their normal newest-first order.

Privacy:
- The custom analytics database stores aggregate daily counts for searches and movie-link clicks, plus a movie title for click totals.
- It does not store search terms, IP addresses, account IDs, or browser identifiers.
- GA4 is optional and follows Google's own analytics processing/settings.
