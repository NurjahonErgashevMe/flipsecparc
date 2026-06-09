from __future__ import annotations

import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)


class ProxyManager:
    """Loads proxy list and provides rotation."""

    def __init__(self, proxy_file: str | Path | None) -> None:
        self._proxies: list[str] = []
        self._index = 0
        if proxy_file is not None:
            self._load(proxy_file)

    def _load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            log.warning("Proxy file not found: %s – running without proxies", p)
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                self._proxies.append(line)
        random.shuffle(self._proxies)
        log.info("Loaded %d proxies from %s", len(self._proxies), p)

    @property
    def count(self) -> int:
        return len(self._proxies)

    def _format(self, proxy_raw: str) -> str:
        if not proxy_raw.startswith("http"):
            return f"http://{proxy_raw}"
        return proxy_raw

    def get_proxy(self) -> str | None:
        if not self._proxies:
            return None
        proxy_raw = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return self._format(proxy_raw)

    def get_random_proxy(self) -> str | None:
        if not self._proxies:
            return None
        return self._format(random.choice(self._proxies))
