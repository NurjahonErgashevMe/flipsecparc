from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from ..config import Settings

log = logging.getLogger(__name__)


class HttpError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._session.trust_env = False  # игнорировать NO_PROXY / HTTPS_PROXY из окружения
        if settings.proxy_url:
            log.info("Используется прокси для запросов: %s", settings.proxy_url)
            self._session.proxies = {
                "http": settings.proxy_url,
                "https": settings.proxy_url,
            }

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        proxy: str | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self._settings.max_retries + 1):
            try:
                proxies = None
                if proxy:
                    proxies = {"http": proxy, "https": proxy}
                elif self._session.proxies:
                    proxies = self._session.proxies
                response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    proxies=proxies,
                    timeout=self._settings.request_timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise HttpError(
                        f"HTTP {response.status_code}",
                        status=response.status_code,
                    )
                if response.status_code >= 400:
                    raise HttpError(
                        f"HTTP {response.status_code}: {response.text[:300]}",
                        status=response.status_code,
                    )
                return response.json()
            except (requests.RequestException, HttpError, ValueError) as exc:
                last_error = exc
                if attempt >= self._settings.max_retries:
                    break
                delay = self._settings.retry_base_delay * (2 ** (attempt - 1))
                delay += random.uniform(0, 0.25)
                log.debug("Retry %s %s (%s): %s", method, url, attempt, exc)
                time.sleep(delay)
        raise HttpError(str(last_error) if last_error else "unknown error")
