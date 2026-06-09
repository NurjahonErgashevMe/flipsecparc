from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_COOKIE_SERVER_URL = "http://72.56.33.73:8000/cookies"

_cached_header: str | None = None


def cookies_list_to_header(cookie_list: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in cookie_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        parts.append(f"{name}={item.get('value', '')}")
    return "; ".join(parts)


def fetch_cookies_header(
    url: str = DEFAULT_COOKIE_SERVER_URL,
    *,
    timeout: float = 15.0,
    force_refresh: bool = False,
) -> str:
    """Fetch cookies JSON array from server, return Cookie header string."""
    global _cached_header
    if _cached_header is not None and not force_refresh:
        return _cached_header

    log.info("Fetching cookies from %s ...", url)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected cookie server response: {type(data)}")

    header = cookies_list_to_header(data)
    if not header:
        raise ValueError("Cookie server returned empty cookie list")

    _cached_header = header
    log.info("Got %d cookies from server", len(data))
    return header
