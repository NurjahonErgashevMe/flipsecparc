from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent

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
    ) -> Settings:
        cookies = os.environ.get("CIAN_COOKIES", "").strip()
        if not cookies:
            cookie_path = _DIR / "cookies.txt"
            if cookie_path.is_file():
                cookies = cookie_path.read_text(encoding="utf-8").strip()

        return cls(
            yandex_api_key=os.environ.get("YANDEX_SUGGEST_API_KEY", DEFAULT_YANDEX_API_KEY),
            cian_cookies=cookies,
            request_timeout=request_timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            workers=workers,
            offers_per_page=offers_per_page,
            moscow_only=moscow_only,
        )
