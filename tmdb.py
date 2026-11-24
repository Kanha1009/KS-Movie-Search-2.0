
import asyncio
import re
import time
from urllib.parse import urlencode

import aiohttp
from rapidfuzz.fuzz import ratio, WRatio

from config import (
    TMDB_API_KEY, TMDB_ACCESS_TOKEN, TMDB_LANGUAGE,
    TMDB_REGION, TMDB_CACHE_TTL
)

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
_cache = {}
_lock = asyncio.Lock()

def tmdb_enabled():
    return bool(TMDB_ACCESS_TOKEN or TMDB_API_KEY)

def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9\u0900-\u097f\u0c00-\u0c7f]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _year(s):
    m = re.search(r"\b((?:19|20)\d{2})\b", s or "")
    return int(m.group(1)) if m else None

# Only remove the release/quality tags explicitly supplied by the site owner.
# This is used for TMDB requests only; the Telegram title itself is never changed.
_TMDB_FILTER_TAGS = re.compile(
    r"(?<!\w)(?:"
    r"18\s*\+|2160p|1080p|720p|480p|4k|"
    r"full\s*hd|uhd|hd|hdrip|web[\s._-]*dl|web[\s._-]*rip|"
    r"blu[\s._-]*ray|brrip|"
    r"hq[\s._-]*cam|hd[\s._-]*cam|camrip|cam|"
    r"hq[\s._-]*hdtc|hdts|hdtc|dvd[\s._-]*scr|pre[\s._-]*dvd[\s._-]*rip|"
    r"hq[\s._-]*hindi[\s._-]*dub|lq[\s._-]*hindi[\s._-]*dub|"
    r"studio[\s._-]*dub|hindi[\s._-]*dub(?:bed)?|"
    r"hq[\s._-]*dub|lq[\s._-]*dub|"
    r"dual[\s._-]*audio|multi[\s._-]*audio|"
    r"uncut|proper|repack|hevc|x264|x265|10bit|"
    r"aac|ddp[\s._-]*5[.]1|e[\s._-]*subs?|english[\s._-]*subtitles|"
    r"hc[\s._-]*e[\s._-]*sub|clean"
    r")(?!\w)",
    re.IGNORECASE,
)

# Language/release labels are removed from the TMDB search query only.
# Keep the original Telegram title intact and use these clues for candidate matching.
_TMDB_LANGUAGE_TAGS = re.compile(
    r"(?<!\w)(?:"
    r"hindi|english|tamil|telugu|malayalam|kannada|bengali|bangla|marathi|"
    r"punjabi|odia|oriya|gujarati|assamese|urdu|sanskrit|"
    r"korean|japanese|chinese|mandarin|cantonese|thai|vietnamese|"
    r"indonesian|malay|filipino|tagalog|spanish|french|german|italian|"
    r"portuguese|russian|arabic|turkish|persian|iranian|dutch|polish|"
    r"swedish|norwegian|danish|finnish|greek|hebrew|ukrainian|"
    r"multi[\s._-]*language|multi[\s._-]*lang|dual[\s._-]*language"
    r")(?!\w)",
    re.IGNORECASE,
)
_TMDB_SEASON_TAGS = re.compile(
    r"(?<!\w)(?:s\s*0?\d{1,2}|season\s*0?\d{1,2})(?!\w)",
    re.IGNORECASE,
)

_LANGUAGE_HINTS = {
    "hindi": {"hi"}, "english": {"en"}, "tamil": {"ta"},
    "telugu": {"te"}, "malayalam": {"ml"}, "kannada": {"kn"},
    "bengali": {"bn"}, "bangla": {"bn"}, "marathi": {"mr"},
    "punjabi": {"pa"}, "odia": {"or"}, "oriya": {"or"},
    "gujarati": {"gu"}, "assamese": {"as"}, "urdu": {"ur"},
    "korean": {"ko"}, "japanese": {"ja"}, "chinese": {"zh"},
    "mandarin": {"zh"}, "cantonese": {"cn", "yue"}, "thai": {"th"},
    "vietnamese": {"vi"}, "indonesian": {"id"}, "malay": {"ms"},
    "filipino": {"tl"}, "tagalog": {"tl"}, "spanish": {"es"},
    "french": {"fr"}, "german": {"de"}, "italian": {"it"},
    "portuguese": {"pt"}, "russian": {"ru"}, "arabic": {"ar"},
    "turkish": {"tr"}, "persian": {"fa"}, "dutch": {"nl"},
    "polish": {"pl"}, "swedish": {"sv"}, "norwegian": {"no"},
    "danish": {"da"}, "finnish": {"fi"}, "greek": {"el"},
    "hebrew": {"he"}, "ukrainian": {"uk"},
}

def _language_hints(source_title):
    title = (source_title or "").lower()
    hints = set()
    for label, codes in _LANGUAGE_HINTS.items():
        if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", title):
            hints.update(codes)
    return hints

def _clean_tmdb_query(value):
    value = _TMDB_FILTER_TAGS.sub(" ", value or "")
    value = _TMDB_LANGUAGE_TAGS.sub(" ", value)
    value = _TMDB_SEASON_TAGS.sub(" ", value)
    # The release year is sent separately through TMDB's year parameter.
    value = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ", value)
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -_.")
    return value

def _queries(title):
    title = re.sub(r"\s+", " ", (title or "")).strip()
    # Prefer the title before parenthetical release/language details, then
    # retry using the full title after removing only the listed tags.
    main = re.split(r"\s*\(", title, maxsplit=1)[0].strip()
    vals = []
    for q in (main, title):
        q = _clean_tmdb_query(q)
        if q and q not in vals:
            vals.append(q)
    return vals[:2]

async def _get(path, params=None):
    if not tmdb_enabled():
        return None
    params = dict(params or {})
    headers = {"accept": "application/json"}
    if TMDB_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_ACCESS_TOKEN}"
    else:
        params["api_key"] = TMDB_API_KEY

    key = path + "?" + urlencode(sorted(params.items()))
    now = time.time()
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(BASE + path, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"TMDB HTTP {resp.status}: {text[:200]}")
            data = await resp.json()

    _cache[key] = (now + TMDB_CACHE_TTL, data)
    return data

async def _search(kind, query, year=None):
    params = {
        "query": query,
        "language": TMDB_LANGUAGE,
        "include_adult": "false",
        "page": 1,
    }
    if kind == "movie":
        params["region"] = TMDB_REGION
        if year:
            params["year"] = year
    if kind == "tv" and year:
        params["first_air_date_year"] = year
    data = await _get(f"/search/{kind}", params)
    return (data or {}).get("results", [])[:8]

def _candidate_score(source_title, item, kind):
    target = _norm(_clean_tmdb_query(source_title))
    name = item.get("title") if kind == "movie" else item.get("name")
    original = item.get("original_title") if kind == "movie" else item.get("original_name")
    names = [_norm(name), _norm(original)]
    names = [x for x in names if x]
    score = max([WRatio(target, x) for x in names] or [0])

    main = _norm(_clean_tmdb_query(re.split(r"\s*\(", source_title, maxsplit=1)[0]))
    if main:
        score = max(score, max([WRatio(main, x) for x in names] or [0]))

    sy = _year(source_title)
    date = item.get("release_date") if kind == "movie" else item.get("first_air_date")
    if sy and date and str(date)[:4].isdigit():
        score += 18 if int(str(date)[:4]) == sy else -12

    # If a language label was present in the Telegram title, use it as a
    # matching hint even though the label is removed from the search query.
    hints = _language_hints(source_title)
    item_language = (item.get("original_language") or "").lower()
    if hints and item_language in hints:
        score += 22
    elif hints and item_language:
        score -= 4

    # India-aware tie breaking. This is especially important for titles like
    # "Leo", where an Indian and Hollywood movie can share the same title.
    lang = (item.get("original_language") or "").lower()
    countries = {str(x).upper() for x in (item.get("origin_country") or [])}
    indian_languages = {"hi", "ta", "te", "ml", "kn", "bn", "mr", "gu", "pa", "as", "or"}
    if lang in indian_languages:
        score += 18
    if "IN" in countries:
        score += 18
    if lang == "en" and "IN" not in countries:
        score -= 8
    return score

def _format_item(kind, item, details):
    name = details.get("title") if kind == "movie" else details.get("name")
    date = details.get("release_date") if kind == "movie" else details.get("first_air_date")
    genres = [g.get("name") for g in details.get("genres", []) if g.get("name")]
    credits = details.get("credits", {})
    cast = [p.get("name") for p in credits.get("cast", [])[:8] if p.get("name")]
    crew = credits.get("crew", [])
    directors = [p.get("name") for p in crew if p.get("job") == "Director"][:3]
    if not directors and kind == "tv":
        directors = [p.get("name") for p in crew if p.get("job") in ("Director", "Creator")][:3]

    poster = details.get("poster_path") or item.get("poster_path")
    backdrop = details.get("backdrop_path") or item.get("backdrop_path")

    return {
        "id": details.get("id"),
        "media_type": kind,
        "title": name or item.get("title") or item.get("name") or "",
        "year": str(date)[:4] if date else "",
        "release_date": date or "",
        "rating": round(float(details.get("vote_average") or item.get("vote_average") or 0), 1),
        "popularity": round(float(details.get("popularity") or item.get("popularity") or 0), 2),
        "original_language": details.get("original_language") or item.get("original_language") or "",
        "origin_country": details.get("origin_country") or item.get("origin_country") or [],
        "genres": genres,
        "overview": details.get("overview") or item.get("overview") or "",
        "poster": f"{IMG}/w500{poster}" if poster else "",
        "backdrop": f"{IMG}/w780{backdrop}" if backdrop else "",
        "cast": cast,
        "director": directors,
        "runtime": (
            details.get("runtime") if kind == "movie"
            else (details.get("episode_run_time") or [None])[0]
        ),
        "tmdb_url": f"https://www.themoviedb.org/{'movie' if kind == 'movie' else 'tv'}/{details.get('id')}",
    }

async def lookup_title(source_title):
    if not tmdb_enabled():
        return None

    cache_key = "title:" + (source_title or "").strip().lower()
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    year = _year(source_title)
    best = None

    try:
        for q in _queries(source_title):
            # Movie and TV are both searched, but Telegram entries remain the
            # source of truth for links/results.
            movie_results, tv_results = await asyncio.gather(
                _search("movie", q, year),
                _search("tv", q, year),
            )
            for kind, items in (("movie", movie_results), ("tv", tv_results)):
                for item in items:
                    score = _candidate_score(source_title, item, kind)
                    if best is None or score > best[0]:
                        best = (score, kind, item)
            if best and best[0] >= 94:
                break

        if not best or best[0] < 72:
            result = None
        else:
            score, kind, item = best
            details = await _get(
                f"/{kind}/{item.get('id')}",
                {
                    "language": TMDB_LANGUAGE,
                    "append_to_response": "credits",
                },
            )
            result = _format_item(kind, item, details or item)
            result["match_score"] = round(score)

    except Exception as exc:
        print(f"TMDB lookup failed for {source_title!r}: {exc}")
        result = None

    _cache[cache_key] = (now + TMDB_CACHE_TTL, result)
    return result

async def enrich_results(results):
    if not results:
        return []
    # Keep each Telegram result. Never deduplicate by TMDB id.
    sem = asyncio.Semaphore(8)
    async def one(x):
        async with sem:
            info = await lookup_title(x.get("movie_name", ""))
            y = dict(x)
            y["tmdb"] = info
            return y
    return await asyncio.gather(*(one(x) for x in results))

async def trending(limit=12, source_items=None):
    """Build trending cards from Telegram DB items so every card keeps its Telegram link.
    TMDB is used only for metadata/ranking; Telegram remains the source of truth.
    """
    if not tmdb_enabled():
        return []
    try:
        items = source_items or []
        enriched = await enrich_results(items)
        valid = [x for x in enriched if x.get("tmdb")]
        # Popularity first, then rating. Keep Telegram order as a final tie-breaker.
        valid.sort(key=lambda x: (
            float((x.get("tmdb") or {}).get("popularity") or 0),
            float((x.get("tmdb") or {}).get("rating") or 0)
        ), reverse=True)
        return valid[:limit]
    except Exception as exc:
        print(f"TMDB trending failed: {exc}")
        return []

async def genre_items(items, genre, limit=20):
    """Filter Telegram DB items by TMDB genre without creating fake/non-Telegram links."""
    genre = (genre or "").strip().lower()
    if not genre or not tmdb_enabled():
        return []
    enriched = await enrich_results(items)
    out = []
    for x in enriched:
        tm = x.get("tmdb") or {}
        genres = {str(g).lower() for g in (tm.get("genres") or [])}
        if genre in genres:
            out.append(x)
        if len(out) >= limit:
            break
    return out

