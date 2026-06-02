from __future__ import annotations

from ..config import MOSCOW_BBOX, USER_AGENT, Settings
from ..errors import PipelineError
from .http import HttpClient

YANDEX_SUGGEST_URL = "https://suggest-maps.yandex.ru/v1/suggest"


class YandexSuggestClient:
    def __init__(self, http: HttpClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    def suggest_formatted_address(self, query: str) -> str:
        data = self._http.request(
            "GET",
            YANDEX_SUGGEST_URL,
            headers={
                "Accept": "*/*",
                "Origin": "https://www.cian.ru",
                "Referer": "https://www.cian.ru/",
                "User-Agent": USER_AGENT,
            },
            params={
                "apikey": self._settings.yandex_api_key,
                "types": "geo",
                "text": query,
                "lang": "ru_RU",
                "results": 10,
                "origin": "jsapi2Geocoder",
                "print_address": 1,
                "bbox": MOSCOW_BBOX,
                "strict_bounds": 0,
            },
        )
        results = data.get("results") or []
        if not results:
            raise PipelineError("yandex_suggest", "пустой results")
        first = results[0]
        address = first.get("address") or {}
        formatted = address.get("formatted_address")
        if not formatted:
            raise PipelineError("yandex_suggest", "нет formatted_address")
        return str(formatted)
