from aiohttp import web
import os

routes = web.RouteTableDef()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(BASE_DIR, "assets")
INDEX_HTML_PATH = os.path.join(BASE_DIR, "website", "index.html")


def get_index_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.Response(text=get_index_html(), content_type="text/html")


@routes.get("/assets/{name}", allow_head=True)
async def asset_handler(request):
    name = request.match_info["name"]
    if name not in {"hero.png", "logo.png" , "ks_movies_flipkart_bbd_banner-1.mp4",
    "ks_movies_free_money_banner.mp4"}:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(ASSET_DIR, name))


@routes.get("/api/health")
async def health_handler(request):
    from tmdb import tmdb_enabled
    return web.json_response({"ok": True, "service": "ks-movies", "tmdb_enabled": tmdb_enabled()})


@routes.get("/api/search")
async def search_handler(request):
    query = (request.query.get("q") or "").strip()
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = min(20, max(1, int(request.query.get("limit", "10"))))
    except ValueError:
        return web.json_response({"error": "Invalid pagination."}, status=400)
    if not query:
        return web.json_response({"error": "Search query is required."}, status=400)
    if len(query) > 120:
        return web.json_response({"error": "Search query is too long."}, status=400)

    try:
        from plugins.detect_lazysms import search_links_for_web
        from tmdb import enrich_results, tmdb_enabled
        results, total = await search_links_for_web(query, offset=offset, limit=limit)
        results = await enrich_results(results)
        return web.json_response({
            "ok": True,
            "query": query,
            "results": results,
            "offset": offset,
            "limit": limit,
            "total": total,
            "tmdb_enabled": tmdb_enabled(),
        })
    except Exception as exc:
        print(f"Web search error: {exc}")
        return web.json_response({"error": "Search is temporarily unavailable."}, status=503)


async def _catalog(limit=30, offset=0):
    """Read a paginated slice of recent Telegram DB entries and enrich them with TMDB."""
    from plugins.detect_lazysms import get_latest_links_for_web
    from tmdb import enrich_results
    raw = await get_latest_links_for_web(limit=limit, offset=offset)
    return await enrich_results(raw)


async def _ordered_latest_catalog(limit=30, offset=0):
    """Manual admin selections first, followed by the normal newest Telegram entries."""
    from plugins.analytics_store import get_manual_update_slots
    from plugins.detect_lazysms import get_latest_links_for_web
    from tmdb import enrich_results

    manual = get_manual_update_slots()
    manual_urls = {str(x.get("target_url") or "").rstrip("/") for x in manual}
    manual_items = await enrich_results([
        {"movie_name": x["movie_name"], "target_url": x["target_url"]}
        for x in manual
    ]) if manual else []

    # Read enough recent Telegram entries to fill the page after removing manual duplicates.
    raw = await get_latest_links_for_web(limit=min(500, max(60, limit + offset + len(manual) + 40)), offset=0)
    rest = [x for x in raw if str(x.get("target_url") or "").rstrip("/") not in manual_urls]
    rest_items = await enrich_results(rest)
    combined = manual_items + rest_items
    return combined[offset:offset + limit]


@routes.get("/api/home")
async def home_handler(request):
    try:
        from tmdb import tmdb_enabled
        # Keep the homepage light, while giving View All/genre APIs their own pagination.
        items = await _catalog(limit=48, offset=0)
        latest = await _ordered_latest_catalog(limit=12, offset=0)

        # Trending is always Telegram-backed. Build a balanced pool so one
        # Hollywood title group cannot crowd out Indian/South titles.
        valid = [x for x in items if x.get("tmdb") and x.get("target_url")]
        valid.sort(key=lambda x: (
            float((x.get("tmdb") or {}).get("popularity") or 0),
            float((x.get("tmdb") or {}).get("rating") or 0),
        ), reverse=True)
        indian_langs = {"hi", "ta", "te", "ml", "kn", "bn", "mr", "gu", "pa", "as", "or"}
        indian, international = [], []
        for item in valid:
            tm = item.get("tmdb") or {}
            is_indian = (str(tm.get("original_language") or "").lower() in indian_langs
                         or "IN" in {str(c).upper() for c in (tm.get("origin_country") or [])})
            (indian if is_indian else international).append(item)
        trending = []
        # Prefer a 6/6 Indian-vs-international balance when enough candidates exist.
        trending.extend(indian[:6])
        trending.extend(international[:6])
        if len(trending) < 12:
            used = {id(x) for x in trending}
            for item in valid:
                if id(item) not in used:
                    trending.append(item)
                if len(trending) >= 12:
                    break

        return web.json_response({
            "ok": True,
            "latest": latest,
            "trending": trending,
            "tmdb_enabled": tmdb_enabled(),
        })
    except Exception as exc:
        print(f"Home API error: {exc}")
        return web.json_response({"error": "Home data is temporarily unavailable."}, status=503)


@routes.get("/api/latest")
async def latest_handler(request):
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = min(24, max(1, int(request.query.get("limit", "12"))))
    except ValueError:
        return web.json_response({"error": "Invalid pagination."}, status=400)

    try:
        from tmdb import tmdb_enabled
        results = await _ordered_latest_catalog(limit=limit, offset=offset)
        return web.json_response({
            "ok": True,
            "results": results,
            "offset": offset,
            "limit": limit,
            "has_more": len(results) == limit,
            "tmdb_enabled": tmdb_enabled(),
        })
    except Exception as exc:
        print(f"Latest API error: {exc}")
        return web.json_response({"error": "Latest movies are temporarily unavailable."}, status=503)


@routes.get("/api/genre")
async def genre_handler(request):
    genre = (request.query.get("genre") or "").strip()
    try:
        offset = max(0, int(request.query.get("offset", "0")))
        limit = min(24, max(1, int(request.query.get("limit", "10"))))
    except ValueError:
        return web.json_response({"error": "Invalid pagination."}, status=400)
    if not genre or len(genre) > 40:
        return web.json_response({"error": "Genre is required."}, status=400)

    try:
        from tmdb import tmdb_enabled
        if not tmdb_enabled():
            return web.json_response({"ok": True, "genre": genre, "results": [], "has_more": False, "tmdb_enabled": False})

        # Scan a larger Telegram-backed catalog and paginate the matching genre.
        scan_limit = min(300, max(80, offset + limit + 48))
        items = await _catalog(limit=scan_limit, offset=0)
        wanted = genre.casefold()
        matches = []
        for item in items:
            if not item.get("target_url"):
                continue
            tm = item.get("tmdb") or {}
            genres = {str(g).casefold() for g in (tm.get("genres") or [])}
            if wanted in genres:
                matches.append(item)

        page = matches[offset:offset + limit]
        return web.json_response({
            "ok": True,
            "genre": genre,
            "results": page,
            "offset": offset,
            "limit": limit,
            "has_more": len(matches) > offset + limit,
            "tmdb_enabled": True,
        })
    except Exception as exc:
        print(f"Genre API error: {exc}")
        return web.json_response({"error": "Genre data is temporarily unavailable."}, status=503)


@routes.get("/api/trending")
async def trending_handler(request):
    try:
        limit = min(24, max(1, int(request.query.get("limit", "12"))))
    except ValueError:
        return web.json_response({"error": "Invalid limit."}, status=400)

    try:
        from tmdb import trending
        # Pull a wider Telegram-backed pool, then rank it with TMDB popularity.
        items = await _catalog(limit=96, offset=0)
        results = await trending(limit=limit, source_items=items)
        return web.json_response({
            "ok": True,
            "results": results,
            "tmdb_enabled": True,
        })
    except Exception as exc:
        print(f"Trending API error: {exc}")
        return web.json_response({"error": "Trending movies are temporarily unavailable."}, status=503)


@routes.get("/api/admin/new-updates/search")
async def admin_new_updates_search_handler(request):
    if not _analytics_authorized(request):
        return web.json_response({"ok": False, "error": "Unauthorized. Enter the admin token."}, status=401)
    query = (request.query.get("q") or "").strip()
    if len(query) < 2 or len(query) > 120:
        return web.json_response({"ok": False, "error": "Enter a movie name (2–120 characters)."}, status=400)
    try:
        from plugins.detect_lazysms import search_links_for_web
        from tmdb import enrich_results
        results, _total = await search_links_for_web(query, offset=0, limit=10)
        results = await enrich_results(results)
        return web.json_response({"ok": True, "results": results})
    except Exception as exc:
        print(f"Admin movie search error: {exc}")
        return web.json_response({"ok": False, "error": "Movie search is temporarily unavailable."}, status=503)


@routes.get("/api/admin/new-updates")
async def admin_new_updates_get_handler(request):
    if not _analytics_authorized(request):
        return web.json_response({"ok": False, "error": "Unauthorized. Enter the admin token."}, status=401)
    try:
        from plugins.analytics_store import get_manual_update_slots
        return web.json_response({"ok": True, "slots": get_manual_update_slots()})
    except Exception as exc:
        print(f"Admin manual updates load error: {exc}")
        return web.json_response({"ok": False, "error": "Saved movie slots are temporarily unavailable."}, status=503)


@routes.put("/api/admin/new-updates")
async def admin_new_updates_save_handler(request):
    if not _analytics_authorized(request):
        return web.json_response({"ok": False, "error": "Unauthorized. Enter the admin token."}, status=401)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON."}, status=400)
    items = payload.get("slots") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) > 5:
        return web.json_response({"ok": False, "error": "Send a list containing up to 5 movies."}, status=400)
    try:
        from plugins.analytics_store import save_manual_update_slots
        slots = save_manual_update_slots(items)
        return web.json_response({"ok": True, "slots": slots})
    except Exception as exc:
        print(f"Admin manual updates save error: {exc}")
        return web.json_response({"ok": False, "error": "Could not save movie slots."}, status=503)


async def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    return web_app


# ---------------- KS Movies aggregate analytics ----------------
@routes.get("/api/analytics/config")
async def analytics_config_handler(request):
    # Measurement ID is public by design; no secrets are sent to the browser.
    return web.json_response({"measurement_id": os.environ.get("GA4_MEASUREMENT_ID", "").strip()})


@routes.post("/api/analytics/event")
async def analytics_event_handler(request):
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid event."}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "Invalid event."}, status=400)
    event_type = payload.get("event_type")
    if event_type not in {"search", "movie_click"}:
        return web.json_response({"ok": False, "error": "Unsupported event."}, status=400)
    # Do not store search text, IP addresses, user IDs, or browser identifiers.
    label = payload.get("item_label", "") if event_type == "movie_click" else ""
    if not isinstance(label, str):
        label = ""
    label = " ".join(label.split())[:100]
    try:
        from plugins.analytics_store import record_event
        record_event(event_type, label)
        return web.json_response({"ok": True}, status=202)
    except Exception as exc:
        print(f"Analytics event error: {exc}")
        return web.json_response({"ok": False}, status=503)


def _analytics_authorized(request):
    expected = os.environ.get("KS_ANALYTICS_ADMIN_TOKEN", "")
    supplied = request.headers.get("X-Analytics-Admin-Token", "")
    return bool(expected and supplied and supplied == expected)


@routes.get("/admin/analytics", allow_head=True)
async def analytics_dashboard_handler(request):
    dashboard_path = os.path.join(BASE_DIR, "website", "admin_analytics.html")
    return web.FileResponse(dashboard_path, headers={"Cache-Control": "no-store"})


@routes.get("/api/admin/analytics")
async def admin_analytics_handler(request):
    if not _analytics_authorized(request):
        return web.json_response({"ok": False, "error": "Unauthorized. Enter the admin token."}, status=401)
    try:
        days = min(365, max(1, int(request.query.get("days", "30"))))
    except ValueError:
        return web.json_response({"ok": False, "error": "Invalid day range."}, status=400)
    try:
        from plugins.analytics_store import get_summary
        return web.json_response({"ok": True, **get_summary(days)})
    except Exception as exc:
        print(f"Analytics dashboard error: {exc}")
        return web.json_response({"ok": False, "error": "Analytics are temporarily unavailable."}, status=503)
