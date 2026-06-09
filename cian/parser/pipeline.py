from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .address import build_geocode_cached_request, build_yandex_query
from .clients import CianClient, HttpClient, HttpError, YandexSuggestClient
from .config import Settings
from .errors import PipelineError
from .models import CianHouseGeo, FailedHouse, InputHouse, ParsedHouse
from .offer_details import OfferDetailsEnricher
from .proxy import ProxyManager

log = logging.getLogger(__name__)


class HousePipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        detail_executor: ThreadPoolExecutor | None = None,
    ) -> None:
        http = HttpClient(settings)
        self._yandex = YandexSuggestClient(http, settings)
        self._cian = CianClient(http, settings)
        self._settings = settings
        self._enricher: OfferDetailsEnricher | None = None
        if not settings.skip_details and detail_executor is not None:
            proxy_manager = ProxyManager(settings.proxies_path)
            self._enricher = OfferDetailsEnricher(
                self._cian,
                proxy_manager,
                detail_executor,
                max_retries=settings.max_retries,
            )

    def parse(self, house: InputHouse) -> ParsedHouse:
        yandex_query = build_yandex_query(house)
        yandex_address = self._yandex.suggest_formatted_address(yandex_query)
        log.debug("[%s] yandex -> %s", house.house_id, yandex_address)

        geocode_request = build_geocode_cached_request(house)
        cached = self._cian.geocode_cached(geocode_request)
        log.debug("[%s] geocode-cached -> %s", house.house_id, cached.text)

        cian_house_id, geo_payload = self._cian.resolve_house_id(
            address=cached.text,
            kind=cached.kind,
            lat=cached.lat,
            lng=cached.lng,
        )
        cian_geo = _build_cian_geo(cian_house_id, cached, geo_payload)

        offers, total, room_counts = self._cian.fetch_deactivated_offers(
            cian_house_id,
            results_on_page=self._settings.offers_per_page,
        )
        log.debug(
            "[%s] offers: %s deactivated / %s total",
            house.house_id,
            len(offers),
            total,
        )

        if self._enricher is not None and offers:
            offers = self._enricher.enrich(offers)
            ok = sum(1 for o in offers if o.details is not None)
            log.debug("[%s] details enriched: %s/%s", house.house_id, ok, len(offers))

        return ParsedHouse(
            source=house.source_snapshot(),
            yandex_formatted_address=yandex_address,
            geocode_text=cached.text,
            geocode_kind=cached.kind,
            cian=cian_geo,
            offers=offers,
            offers_total_count=total,
            room_counts=room_counts,
        )

    def parse_safe(self, house: InputHouse) -> ParsedHouse | FailedHouse:
        try:
            return self.parse(house)
        except PipelineError as exc:
            return FailedHouse(
                source=house.source_snapshot(),
                stage=exc.stage,
                error=str(exc),
            )
        except HttpError as exc:
            return FailedHouse(
                source=house.source_snapshot(),
                stage="http",
                error=str(exc),
            )
        except Exception as exc:
            return FailedHouse(
                source=house.source_snapshot(),
                stage="unknown",
                error=str(exc),
            )


def _build_cian_geo(
    house_id: int,
    cached,
    payload: dict,
) -> CianHouseGeo:
    details = payload.get("details") or []
    street_id: int | None = None
    location_id: int | None = None
    for detail in details:
        geo_type = detail.get("geoType")
        detail_id = detail.get("id")
        if detail_id is None:
            continue
        if geo_type == "Street":
            street_id = int(detail_id)
        elif geo_type == "Location":
            location_id = int(detail_id)

    return CianHouseGeo(
        cian_house_id=house_id,
        lat=cached.lat,
        lng=cached.lng,
        address=cached.text,
        region_id=_optional_int(payload.get("regionId")),
        country_id=_optional_int(payload.get("countryId")),
        street_id=street_id,
        location_id=location_id,
    )


def _optional_int(value) -> int | None:
    if value is None:
        return None
    return int(value)
