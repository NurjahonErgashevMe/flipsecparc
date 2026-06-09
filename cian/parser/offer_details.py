from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import replace

from .clients.cian import CianClient
from .clients.http import HttpError
from .models import DeactivatedOffer, OfferDetails
from .proxy import ProxyManager

log = logging.getLogger(__name__)


class OfferDetailsEnricher:
    def __init__(
        self,
        cian: CianClient,
        proxy_manager: ProxyManager,
        executor: ThreadPoolExecutor,
        *,
        max_retries: int = 4,
    ) -> None:
        self._cian = cian
        self._proxies = proxy_manager
        self._executor = executor
        self._max_retries = max_retries

    def enrich(self, offers: list[DeactivatedOffer]) -> list[DeactivatedOffer]:
        if not offers:
            return offers

        futures: dict[Future[DeactivatedOffer], int] = {
            self._executor.submit(self._fetch_one, offer): offer.id
            for offer in offers
        }
        by_id: dict[int, DeactivatedOffer] = {}
        for future in as_completed(futures):
            offer_id = futures[future]
            try:
                by_id[offer_id] = future.result()
            except Exception as exc:
                log.warning("Unexpected error enriching offer %s: %s", offer_id, exc)
                original = next(o for o in offers if o.id == offer_id)
                by_id[offer_id] = replace(
                    original,
                    details=None,
                    details_error=str(exc),
                )

        return [by_id[o.id] for o in offers]

    def _fetch_one(self, offer: DeactivatedOffer) -> DeactivatedOffer:
        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            proxy = self._proxies.get_random_proxy()
            try:
                raw = self._cian.fetch_offer_details(offer.id, proxy=proxy)
                details = OfferDetails.from_api(raw)
                return replace(offer, details=details, details_error=None)
            except HttpError as exc:
                last_error = str(exc)
                log.debug(
                    "Offer %s details attempt %s failed: %s",
                    offer.id,
                    attempt,
                    exc,
                )
            except Exception as exc:
                last_error = str(exc)
                log.debug(
                    "Offer %s details attempt %s failed: %s",
                    offer.id,
                    attempt,
                    exc,
                )

        return replace(offer, details=None, details_error=last_error or "unknown error")
