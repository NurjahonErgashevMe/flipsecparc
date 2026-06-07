"""
CIAN Offer Page Parser - HTTP Client.

Handles fetching offer pages from cian.ru using curl_cffi with proxy rotation
and cookie management. Designed for reuse in other projects.
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests as cffi_requests

log = logging.getLogger(__name__)

# -------------------------------------------------------------------
#  Constants
# -------------------------------------------------------------------
_OFFER_URL_TEMPLATE = "https://www.cian.ru/sale/flat/{offer_id}/"

_BROWSER_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "en,ru;q=0.9,en-US;q=0.8,uz;q=0.7",
    "priority": "u=0, i",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
}

_COOKIE_SERVER_URL = "http://72.56.33.73:8000/cookies"

# Markers that indicate CAPTCHA / block / checkpoint page
_BLOCK_MARKERS = [
    "<title>captcha</title>",
    "подтвердите, что вы не робот",
    "мы заметили подозрительную активность",
    "доступ ограничен",
    "access denied",
]


# -------------------------------------------------------------------
#  Proxy Manager
# -------------------------------------------------------------------
class ProxyManager:
    """Loads proxy list and provides rotation."""

    def __init__(self, proxy_file: str | Path) -> None:
        self._proxies: list[str] = []
        self._index = 0
        self._load(proxy_file)

    def _load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            log.warning("Proxy file not found: %s – running without proxies", p)
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if line:
                self._proxies.append(line)
        random.shuffle(self._proxies)
        log.info("Loaded %d proxies from %s", len(self._proxies), p)

    @property
    def count(self) -> int:
        return len(self._proxies)

    def get_proxy(self) -> str | None:
        """Return next proxy in round-robin, formatted as http:// URL."""
        if not self._proxies:
            return None
        proxy_raw = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        # Format: user:pass@host:port  ->  http://user:pass@host:port
        if not proxy_raw.startswith("http"):
            proxy_raw = f"http://{proxy_raw}"
        return proxy_raw

    def get_random_proxy(self) -> str | None:
        """Return a random proxy."""
        if not self._proxies:
            return None
        raw = random.choice(self._proxies)
        if not raw.startswith("http"):
            raw = f"http://{raw}"
        return raw


# -------------------------------------------------------------------
#  Cookie Manager
# -------------------------------------------------------------------
class CookieManager:
    """Fetches and caches cookies from the cookie server."""

    def __init__(self, cookie_url: str = _COOKIE_SERVER_URL) -> None:
        self._url = cookie_url
        self._cookies: dict[str, str] | None = None

    def get_cookies(self, force_refresh: bool = False) -> dict[str, str]:
        """Fetch cookies from server, return as {name: value} dict."""
        if self._cookies is not None and not force_refresh:
            return self._cookies

        log.info("Fetching cookies from %s ...", self._url)
        try:
            resp = cffi_requests.get(self._url, timeout=15, impersonate="chrome")
            resp.raise_for_status()
            cookie_list: list[dict[str, Any]] = resp.json()
            self._cookies = {c["name"]: c["value"] for c in cookie_list}
            log.info("Got %d cookies", len(self._cookies))
            return self._cookies
        except Exception as exc:
            log.error("Failed to fetch cookies: %s", exc)
            return self._cookies or {}

    def clear(self) -> None:
        self._cookies = None


# -------------------------------------------------------------------
#  CIAN Offer Client
# -------------------------------------------------------------------
def _is_blocked(html: str) -> bool:
    """Check if the response HTML is a CAPTCHA / block page."""
    html_lower = html.lower()
    for marker in _BLOCK_MARKERS:
        if marker.lower() in html_lower:
            return True
    return False


class CianOfferClient:
    """
    Fetches individual offer pages from cian.ru.
    
    Uses curl_cffi with browser impersonation, proxy rotation,
    and cookie management to bypass anti-bot protections.
    
    Architecture note: This class is designed to be imported
    and used independently in other projects.
    """

    def __init__(
        self,
        proxy_manager: ProxyManager,
        cookie_manager: CookieManager,
        *,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        timeout: float = 30.0,
    ) -> None:
        self._proxy_mgr = proxy_manager
        self._cookie_mgr = cookie_manager
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._timeout = timeout

    def fetch_offer_page(self, offer_id: int) -> str:
        """
        Fetch the HTML page for a given CIAN offer ID.

        Retries up to `max_retries` times, rotating proxies and
        refreshing cookies on each failure / block detection.

        Returns the HTML string on success.
        Raises RuntimeError if all retries are exhausted.
        """
        url = _OFFER_URL_TEMPLATE.format(offer_id=offer_id)
        last_error: str = ""

        for attempt in range(1, self._max_retries + 1):
            proxy = self._proxy_mgr.get_random_proxy()
            cookies = self._cookie_mgr.get_cookies()
            log.debug(
                "[%s] attempt %d/%d proxy=%s",
                offer_id, attempt, self._max_retries,
                proxy.split("@")[-1] if proxy else "none",
            )

            try:
                resp = cffi_requests.get(
                    url,
                    headers=_BROWSER_HEADERS,
                    cookies=cookies,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=self._timeout,
                    impersonate="chrome",
                    allow_redirects=True,
                )

                if resp.status_code == 200 and not _is_blocked(resp.text):
                    return resp.text

                # Got a block / captcha / bad status
                last_error = (
                    f"status={resp.status_code}, "
                    f"blocked={_is_blocked(resp.text)}"
                )
                log.warning(
                    "[%s] attempt %d blocked (%s) – rotating proxy & cookies",
                    offer_id, attempt, last_error,
                )

            except Exception as exc:
                last_error = str(exc)
                log.warning(
                    "[%s] attempt %d error: %s",
                    offer_id, attempt, last_error,
                )

            # Refresh cookies for next attempt
            self._cookie_mgr.get_cookies(force_refresh=True)
            time.sleep(self._retry_delay * attempt)

        raise RuntimeError(
            f"Failed to fetch offer {offer_id} after {self._max_retries} "
            f"attempts. Last error: {last_error}"
        )
