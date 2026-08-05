"""Shared access layer for the FuelWatch.ie Supabase backend.

The FuelWatch SPA (https://app.fuelwatch.ie/) ships its Supabase URL and anon
JWT inside a hash-suffixed Expo JavaScript bundle. Both values are public —
every browser that loads the site downloads them — but the bundle filename
rotates on redeploy, so we rediscover them each run instead of pinning.

The bundle is ~2.9 MB. Two fetchers now depend on these credentials
(`fuelwatch_ie` for national daily averages, `fuelwatch_counties` for the
county breakdown), so the discovered pair is cached at module level and the
bundle is downloaded once per process rather than once per fetcher.
"""
from __future__ import annotations

import logging
import re

import requests

logger = logging.getLogger(__name__)

APP_URL = "https://app.fuelwatch.ie/"

SUPABASE_URL_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co")
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")
BUNDLE_RE = re.compile(r'src="(/_expo/static/js/web/entry-[^"]+\.js)"')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (irish-fuel-trend/0.1; +https://github.com/)",
}

_cached_credentials: tuple[str, str] | None = None


def _discover() -> tuple[str, str]:
    """Scrape the SPA HTML + JS bundle to recover the Supabase URL and anon key."""
    r = requests.get(APP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    bundle_match = BUNDLE_RE.search(r.text)
    if not bundle_match:
        raise RuntimeError("Could not find Expo JS bundle URL in FuelWatch app HTML.")
    bundle_url = APP_URL.rstrip("/") + bundle_match.group(1)

    b = requests.get(bundle_url, headers=HEADERS, timeout=60)
    b.raise_for_status()
    url_match = SUPABASE_URL_RE.search(b.text)
    key_match = JWT_RE.search(b.text)
    if not url_match or not key_match:
        raise RuntimeError("Could not extract Supabase URL/anon key from bundle.")
    return url_match.group(0), key_match.group(0)


def credentials(force: bool = False) -> tuple[str, str]:
    """Return (base_url, anon_key), discovering and caching on first use."""
    global _cached_credentials
    if _cached_credentials is None or force:
        _cached_credentials = _discover()
        logger.info("Discovered FuelWatch Supabase endpoint %s", _cached_credentials[0])
    return _cached_credentials


def reset_cache() -> None:
    """Drop the cached credentials so the next call rediscovers them."""
    global _cached_credentials
    _cached_credentials = None


def _auth_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def rest_get(path: str, params: dict, timeout: int = 30) -> list[dict]:
    """GET a PostgREST table/view path (e.g. '/rest/v1/daily_price_snapshots')."""
    base, key = credentials()
    r = requests.get(base + path, params=params, headers=_auth_headers(key), timeout=timeout)
    r.raise_for_status()
    return r.json()


def rpc(name: str, args: dict | None = None, timeout: int = 60) -> list[dict]:
    """POST to a PostgREST RPC endpoint and return its rows."""
    base, key = credentials()
    r = requests.post(
        f"{base}/rest/v1/rpc/{name}",
        json=args or {},
        headers=_auth_headers(key),
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, list) else [payload]
