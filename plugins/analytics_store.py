"""Privacy-conscious aggregate analytics for KS Movies.

Uses Neon/PostgreSQL when KS_ANALYTICS_DATABASE_URL is configured.
Falls back to SQLite for local development or deployments without that variable.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get("KS_ANALYTICS_DATABASE_URL", "").strip()
DB_PATH = os.environ.get("KS_ANALYTICS_DB_PATH", "/tmp/ks_movies_analytics.sqlite3")


def _connect():
    """Return a database connection and ensure the aggregate table exists."""
    if DATABASE_URL:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "KS_ANALYTICS_DATABASE_URL is set, but psycopg is not installed."
            ) from exc
        conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS analytics_events (
                        day DATE NOT NULL,
                        event_type TEXT NOT NULL,
                        item_label TEXT NOT NULL DEFAULT '',
                        event_count BIGINT NOT NULL DEFAULT 0,
                        PRIMARY KEY (day, event_type, item_label)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS manual_update_slots (
                        slot INTEGER PRIMARY KEY,
                        movie_name TEXT NOT NULL,
                        target_url TEXT NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS custom_movie_posters (
                        movie_key TEXT PRIMARY KEY,
                        movie_name TEXT NOT NULL,
                        poster_url TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            day TEXT NOT NULL,
            event_type TEXT NOT NULL,
            item_label TEXT NOT NULL DEFAULT '',
            event_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, event_type, item_label)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manual_update_slots (
            slot INTEGER PRIMARY KEY,
            movie_name TEXT NOT NULL,
            target_url TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_movie_posters (
            movie_key TEXT PRIMARY KEY,
            movie_name TEXT NOT NULL,
            poster_url TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def _clean_label(value, max_length=100):
    value = " ".join(str(value or "").split()).strip()
    return value[:max_length]


def record_event(event_type, item_label=""):
    if event_type not in {"search", "movie_click"}:
        return False
    label = _clean_label(item_label) if event_type == "movie_click" else ""
    day = datetime.now(timezone.utc).date().isoformat()
    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics_events(day, event_type, item_label, event_count)
                    VALUES (%s, %s, %s, 1)
                    ON CONFLICT (day, event_type, item_label)
                    DO UPDATE SET event_count = analytics_events.event_count + 1
                """, (day, event_type, label))
    else:
        with _connect() as conn:
            conn.execute("""
                INSERT INTO analytics_events(day, event_type, item_label, event_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(day, event_type, item_label)
                DO UPDATE SET event_count = event_count + 1
            """, (day, event_type, label))
    return True


def get_summary(days=30):
    days = min(365, max(1, int(days)))
    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT event_type, COALESCE(SUM(event_count), 0)
                    FROM analytics_events
                    WHERE day >= CURRENT_DATE - (%s * INTERVAL '1 day')
                    GROUP BY event_type
                """, (days - 1,))
                totals = cur.fetchall()
                cur.execute("""
                    SELECT day,
                           SUM(CASE WHEN event_type='search' THEN event_count ELSE 0 END),
                           SUM(CASE WHEN event_type='movie_click' THEN event_count ELSE 0 END)
                    FROM analytics_events
                    WHERE day >= CURRENT_DATE - (%s * INTERVAL '1 day')
                    GROUP BY day ORDER BY day
                """, (days - 1,))
                daily = cur.fetchall()
                cur.execute("""
                    SELECT item_label, SUM(event_count) AS total
                    FROM analytics_events
                    WHERE event_type='movie_click'
                      AND day >= CURRENT_DATE - (%s * INTERVAL '1 day')
                      AND item_label <> ''
                    GROUP BY item_label
                    ORDER BY total DESC, item_label ASC LIMIT 10
                """, (days - 1,))
                popular = cur.fetchall()
        daily = [(d.isoformat(), s, c) for d, s, c in daily]
    else:
        with _connect() as conn:
            totals = conn.execute("""
                SELECT event_type, COALESCE(SUM(event_count), 0)
                FROM analytics_events
                WHERE day >= date('now', ?)
                GROUP BY event_type
            """, (f"-{days - 1} days",)).fetchall()
            daily = conn.execute("""
                SELECT day,
                       SUM(CASE WHEN event_type='search' THEN event_count ELSE 0 END),
                       SUM(CASE WHEN event_type='movie_click' THEN event_count ELSE 0 END)
                FROM analytics_events
                WHERE day >= date('now', ?)
                GROUP BY day ORDER BY day
            """, (f"-{days - 1} days",)).fetchall()
            popular = conn.execute("""
                SELECT item_label, SUM(event_count) AS total
                FROM analytics_events
                WHERE event_type='movie_click' AND day >= date('now', ?)
                  AND item_label <> ''
                GROUP BY item_label ORDER BY total DESC, item_label ASC LIMIT 10
            """, (f"-{days - 1} days",)).fetchall()

    counts = dict(totals)
    return {
        "days": days,
        "searches": int(counts.get("search", 0)),
        "movie_clicks": int(counts.get("movie_click", 0)),
        "daily": [{"date": d, "searches": int(s), "movie_clicks": int(c)} for d, s, c in daily],
        "popular_movies": [{"title": t, "clicks": int(c)} for t, c in popular],
    }


def get_manual_update_slots():
    """Return the saved manual New Updates selections in slot order."""
    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slot, movie_name, target_url FROM manual_update_slots ORDER BY slot")
                rows = cur.fetchall()
    else:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT slot, movie_name, target_url FROM manual_update_slots ORDER BY slot"
            ).fetchall()
    return [
        {"slot": int(slot), "movie_name": str(name), "target_url": str(url)}
        for slot, name, url in rows
    ]


def save_manual_update_slots(items):
    """Replace the ordered manual slots atomically. Empty list restores Telegram ordering."""
    clean = []
    seen_urls = set()
    for item in (items or [])[:5]:
        if not isinstance(item, dict):
            continue
        name = _clean_label(item.get("movie_name"), 180)
        url = str(item.get("target_url") or "").strip()[:2000]
        if not name or not url or not url.startswith(("https://", "http://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        clean.append((len(clean) + 1, name, url))

    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM manual_update_slots")
                for slot, name, url in clean:
                    cur.execute(
                        "INSERT INTO manual_update_slots(slot, movie_name, target_url) VALUES (%s, %s, %s)",
                        (slot, name, url),
                    )
    else:
        with _connect() as conn:
            conn.execute("DELETE FROM manual_update_slots")
            conn.executemany(
                "INSERT INTO manual_update_slots(slot, movie_name, target_url) VALUES (?, ?, ?)",
                clean,
            )
    return get_manual_update_slots()


def _movie_key(value):
    """Normalize a movie title for global custom-poster matching."""
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def get_custom_movie_posters():
    """Return all global custom poster mappings, keyed by normalized movie title."""
    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT movie_key, movie_name, poster_url FROM custom_movie_posters ORDER BY movie_name")
                rows = cur.fetchall()
    else:
        with _connect() as conn:
            rows = conn.execute("SELECT movie_key, movie_name, poster_url FROM custom_movie_posters ORDER BY movie_name").fetchall()
    return [{"movie_key": str(k), "movie_name": str(n), "poster_url": str(u)} for k, n, u in rows]


def save_custom_movie_poster(movie_name, poster_url):
    """Create/update a custom poster. Empty URL removes the mapping."""
    name = _clean_label(movie_name, 180)
    url = str(poster_url or "").strip()[:2000]
    key = _movie_key(name)
    if not key or not name:
        raise ValueError("Movie name is required.")
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Poster URL must be a valid HTTPS image URL.")
    if DATABASE_URL:
        with _connect() as conn:
            with conn.cursor() as cur:
                if url:
                    cur.execute("""INSERT INTO custom_movie_posters(movie_key,movie_name,poster_url,updated_at)
                        VALUES (%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(movie_key) DO UPDATE SET
                        movie_name=EXCLUDED.movie_name, poster_url=EXCLUDED.poster_url, updated_at=CURRENT_TIMESTAMP""", (key,name,url))
                else:
                    cur.execute("DELETE FROM custom_movie_posters WHERE movie_key=%s", (key,))
    else:
        with _connect() as conn:
            if url:
                conn.execute("""INSERT INTO custom_movie_posters(movie_key,movie_name,poster_url,updated_at)
                    VALUES (?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(movie_key) DO UPDATE SET
                    movie_name=excluded.movie_name, poster_url=excluded.poster_url, updated_at=CURRENT_TIMESTAMP""", (key,name,url))
            else:
                conn.execute("DELETE FROM custom_movie_posters WHERE movie_key=?", (key,))
    return {"movie_key": key, "movie_name": name, "poster_url": url}
