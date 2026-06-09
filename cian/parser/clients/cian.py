from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import CIAN_ORIGIN_HEADERS, Settings
from ..models import DeactivatedOffer, RoomCount
from ..errors import PipelineError
from .http import HttpClient, HttpError

GEOCODE_CACHED_URL = "https://api.cian.ru/geo/v1/geocode-cached/"
GEOCODED_FOR_SEARCH_URL = "https://api.cian.ru/geo/v1/geocoded-for-search/"
OFFER_HISTORY_URL = (
    "https://api.cian.ru/valuation-offer-history/v4/get-house-offer-history-desktop/"
)
OFFER_DETAILS_URL = (
    "https://api.cian.ru/valuation-offer-history/v2/get-offer-from-history-web/"
)


@dataclass(frozen=True)
class GeocodeCachedItem:
    text: str
    name: str
    kind: str
    lat: float
    lng: float


class CianClient:
    def __init__(self, http: HttpClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings

    def _headers(self, *, json_content: bool = False) -> dict[str, str]:
        headers = dict(CIAN_ORIGIN_HEADERS)
        if json_content:
            headers["content-type"] = "application/json"
        cookies = self._settings.cian_cookies
        if cookies:
            headers["Cookie"] = cookies
        return headers

    def geocode_cached(self, request_text: str) -> GeocodeCachedItem:
        data = self._http.request(
            "GET",
            GEOCODE_CACHED_URL,
            headers=self._headers(),
            params={"request": request_text},
        )
        items = data.get("items") or []
        if not items:
            raise PipelineError("geocode_cached", "пустой items")
        item = items[0]
        coords = item.get("coordinates") or []
        if len(coords) < 2:
            raise PipelineError("geocode_cached", "нет coordinates")
        return GeocodeCachedItem(
            text=str(item.get("text", "")),
            name=str(item.get("name", "")),
            kind=str(item.get("kind", "")),
            lng=float(coords[0]),
            lat=float(coords[1]),
        )

    def resolve_house_id(
        self,
        *,
        address: str,
        kind: str,
        lat: float,
        lng: float,
    ) -> tuple[int, dict[str, Any]]:
        if not self._settings.cian_cookies:
            raise PipelineError(
                "geocoded_for_search",
                "нужны cookies Cian (CIAN_COOKIES или cian/cookies.txt)",
            )
        data = self._http.request(
            "POST",
            GEOCODED_FOR_SEARCH_URL,
            headers=self._headers(json_content=True),
            json_body={
                "address": address,
                "kind": kind,
                "lat": lat,
                "lng": lng,
            },
        )
        details = data.get("details") or []
        for detail in details:
            if detail.get("geoType") == "House":
                house_id = detail.get("id")
                if house_id is not None:
                    return int(house_id), data
        raise PipelineError("geocoded_for_search", "House не найден в details")

    def fetch_deactivated_offers(
        self,
        house_id: int,
        *,
        results_on_page: int,
    ) -> tuple[list[DeactivatedOffer], int, list[RoomCount]]:
        if not self._settings.cian_cookies:
            raise PipelineError(
                "offer_history",
                "нужны cookies Cian (CIAN_COOKIES или cian/cookies.txt)",
            )
        collected: list[DeactivatedOffer] = []
        room_counts: list[RoomCount] = []
        total_count = 0
        page = 1

        while True:
            data = self._http.request(
                "POST",
                OFFER_HISTORY_URL,
                headers=self._headers(json_content=True),
                json_body={
                    "houseId": house_id,
                    "resultsOnPage": results_on_page,
                    "page": page,
                },
            )
            total_count = int(data.get("totalCount") or 0)
            if page == 1:
                room_counts = [
                    RoomCount.from_api(item)
                    for item in (data.get("roomCounts") or [])
                    if isinstance(item, dict)
                ]
            offers_raw = data.get("offers") or []
            if not offers_raw:
                break
            for raw in offers_raw:
                offer = DeactivatedOffer.from_api(raw)
                if offer is not None:
                    collected.append(offer)
            if len(offers_raw) < results_on_page:
                break
            if page * results_on_page >= total_count:
                break
            page += 1

        return collected, total_count, room_counts

    def fetch_offer_details(
        self,
        cian_id: int,
        *,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        if not self._settings.cian_cookies:
            raise PipelineError(
                "offer_details",
                "нужны cookies Cian (CIAN_COOKIES или cian/cookies.txt)",
            )
        data = self._http.request(
            "GET",
            OFFER_DETAILS_URL,
            headers=self._headers(),
            params={"cianId": cian_id},
            proxy=proxy,
        )
        if not isinstance(data, dict):
            raise PipelineError("offer_details", "неожиданный формат ответа")
        return data
