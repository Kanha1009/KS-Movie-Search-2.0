KS Movies Admin Error Fix — only these 3 files
Replace these paths in your repository:
1. plugins/analytics_store.py
2. plugins/web_support.py
3. website/admin_analytics.html

Important: The previous JSON error can occur when the admin page is deployed but the matching API routes are missing from web_support.py. These files must be deployed together. Keep your existing Koyeb environment variables KS_ANALYTICS_DATABASE_URL and KS_ANALYTICS_ADMIN_TOKEN unchanged.
