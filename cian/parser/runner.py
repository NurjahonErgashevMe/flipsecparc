from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from .config import Settings
from .io import ResultWriter
from .models import FailedHouse, InputHouse, ParsedHouse
from .pipeline import HousePipeline

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    total: int
    skipped: int
    success: int
    failed: int
    elapsed_sec: float


class ParserRunner:
    def __init__(self, settings: Settings, pipeline: HousePipeline, writer: ResultWriter) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._writer = writer
        self._stats_lock = threading.Lock()
        self._success = 0
        self._failed = 0
        self._done = 0

    def _process_one(self, house: InputHouse) -> ParsedHouse | FailedHouse:
        return self._pipeline.parse_safe(house)

    def _on_result(self, result: ParsedHouse | FailedHouse) -> None:
        if isinstance(result, ParsedHouse):
            self._writer.write_success(result)
            with self._stats_lock:
                self._success += 1
        else:
            self._writer.write_failure(result)
            with self._stats_lock:
                self._failed += 1
        with self._stats_lock:
            self._done += 1
            done = self._done
        if done % 100 == 0:
            log.info("Обработано: %s (ok=%s, fail=%s)", done, self._success, self._failed)

    def run(self, houses: list[InputHouse]) -> RunStats:
        pending = [
            h for h in houses if not self._writer.is_done(h.house_id)
        ]
        skipped = len(houses) - len(pending)
        if skipped:
            log.info("Пропущено по checkpoint: %s", skipped)
        if not pending:
            log.info("Нечего обрабатывать")
            return RunStats(
                total=len(houses),
                skipped=skipped,
                success=0,
                failed=0,
                elapsed_sec=0.0,
            )

        started = time.monotonic()
        max_in_flight = self._settings.workers * 2
        executor = ThreadPoolExecutor(max_workers=self._settings.workers)
        futures: dict[Future, InputHouse] = {}
        house_iter = iter(pending)

        try:
            for _ in range(min(max_in_flight, len(pending))):
                house = next(house_iter, None)
                if house is None:
                    break
                fut = executor.submit(self._process_one, house)
                futures[fut] = house

            while futures:
                done_set, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done_set:
                    house = futures.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = FailedHouse(
                            source=house.source_snapshot(),
                            stage="worker",
                            error=str(exc),
                        )
                    self._on_result(result)

                    next_house = next(house_iter, None)
                    if next_house is not None:
                        nf = executor.submit(self._process_one, next_house)
                        futures[nf] = next_house
        finally:
            executor.shutdown(wait=True)
            self._writer.flush_checkpoint()

        elapsed = time.monotonic() - started
        return RunStats(
            total=len(houses),
            skipped=skipped,
            success=self._success,
            failed=self._failed,
            elapsed_sec=elapsed,
        )
