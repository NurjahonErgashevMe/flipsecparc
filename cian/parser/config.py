from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .cookies import DEFAULT_COOKIE_SERVER_URL, fetch_cookies_header

_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)

def _load_env_file() -> None:
    env_path = _DIR / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

_load_env_file()

# Границы Москвы для Yandex Suggest (как в браузере Cian)
MOSCOW_BBOX = "37.967428,56.021224,36.803101,55.142175"

DEFAULT_YANDEX_API_KEY = "7a8defd8-9fea-4454-a450-6e9d1083ead0"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

CIAN_ORIGIN_HEADERS = {
    "accept": "*/*",
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://www.cian.ru",
    "referer": "https://www.cian.ru/",
    "user-agent": USER_AGENT,
}


@dataclass(frozen=True)
class Settings:
    yandex_api_key: str
    cian_cookies: str
    request_timeout: float
    max_retries: int
    retry_base_delay: float
    workers: int
    offers_per_page: int
    moscow_only: bool
    proxy_url: str | None = None
    detail_workers: int = 32
    proxies_path: Path | None = None
    skip_details: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        workers: int = 16,
        offers_per_page: int = 50,
        moscow_only: bool = True,
        request_timeout: float = 30.0,
        max_retries: int = 4,
        retry_base_delay: float = 0.5,
        proxy_url: str | None = None,
        detail_workers: int = 32,
        proxies_path: Path | None = None,
        skip_details: bool = False,
    ) -> Settings:
        cookies = os.environ.get("CIAN_COOKIES", "").strip()
        if not cookies:
            cookie_path = _DIR / "cookies.txt"
            if cookie_path.is_file():
                cookies = cookie_path.read_text(encoding="utf-8").strip()
        if not cookies:
            cookie_url = os.environ.get(
                "CIAN_COOKIE_SERVER_URL",
                DEFAULT_COOKIE_SERVER_URL,
            ).strip()
            try:
                cookies = fetch_cookies_header(cookie_url)
            except Exception as exc:
                log.warning("Не удалось загрузить cookies с %s: %s", cookie_url, exc)

        return cls(
            yandex_api_key=os.environ.get("YANDEX_SUGGEST_API_KEY", DEFAULT_YANDEX_API_KEY),
            cian_cookies=cookies,
            request_timeout=request_timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            workers=workers,
            offers_per_page=offers_per_page,
            moscow_only=moscow_only,
            proxy_url=proxy_url or os.environ.get("CIAN_PROXY"),
            detail_workers=detail_workers,
            proxies_path=proxies_path,
            skip_details=skip_details,
        )
