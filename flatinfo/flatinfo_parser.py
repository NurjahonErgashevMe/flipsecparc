from __future__ import annotations

import argparse
import json
import logging
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests

URL = "https://flatinfo.ru/leaflet/get_details.php"
HEADERS = {
    "accept": "*/*",
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json; charset=utf-8",
    "origin": "https://flatinfo.ru",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

_DIR = Path(__file__).resolve().parent
MAX_WORKERS = 300
HID_START = 200
HID_END = 400_000
OUTPUT_PATH = _DIR / "result.json"
FAILED_HIDS_PATH = _DIR / "failed_hids.txt"
FAILED_DETAILS_PATH = _DIR / "failed_details.jsonl"
LOG_PATH = _DIR / "logs.log"

REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 4
RETRY_BASE_DELAY = 0.5
PROGRESS_STEP = 1000
IN_FLIGHT_MULTIPLIER = 4
MAX_RECOVERY_ROUNDS = 30
RECOVERY_PAUSE_SECONDS = 5.0

FetchStatus = Literal["residential", "not_residential", "error"]


class _FlushFileHandler(logging.FileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class _FlushStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


@dataclass
class FetchResult:
    hid: int
    status: FetchStatus
    data: dict[str, Any] | None = None
    attempts: int = 0
    error_type: str | None = None
    error_message: str | None = None
    http_status: int | None = None


_thread_local = threading.local()


def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = _FlushFileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)
    root.addHandler(fh)
    sh = _FlushStreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    root.addHandler(sh)
    root.setLevel(logging.INFO)


def _normalize_jil_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\u00a0", " ").replace("\u2009", " ").strip()


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def is_residential(data: dict[str, Any]) -> bool:
    return _normalize_jil_type(data.get("jil_type")) == "Жилой"


def fetch_house(hid: int, timeout: float = REQUEST_TIMEOUT) -> FetchResult:
    session = _get_session()
    last_error_type: str | None = None
    last_error_msg: str | None = None
    last_http_status: int | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.post(URL, json={"hid": hid}, timeout=timeout)
            last_http_status = response.status_code
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("JSON не объект")
            if is_residential(payload):
                return FetchResult(hid=hid, status="residential", data=payload, attempts=attempt)
            return FetchResult(hid=hid, status="not_residential", attempts=attempt, http_status=response.status_code)
        except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            last_error_type = type(exc).__name__
            last_error_msg = str(exc)
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                last_http_status = exc.response.status_code
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0.0, 0.3)
                time.sleep(delay)

    return FetchResult(
        hid=hid,
        status="error",
        attempts=MAX_RETRIES,
        error_type=last_error_type,
        error_message=last_error_msg,
        http_status=last_http_status,
    )


def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "00:00:00"
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _process_result(
    result: FetchResult,
    storage: list[dict[str, Any]],
    failed_hids: list[int],
    counters: dict[str, int],
    error_out,
) -> None:
    counters["attempts_total"] += result.attempts
    if result.status == "residential" and result.data is not None:
        storage.append(result.data)
        counters["residential"] += 1
        return
    if result.status == "not_residential":
        counters["not_residential"] += 1
        return

    counters["errors"] += 1
    failed_hids.append(result.hid)
    error_out.write(
        json.dumps(
            {
                "hid": result.hid,
                "attempts": result.attempts,
                "error_type": result.error_type,
                "error_message": result.error_message,
                "http_status": result.http_status,
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def _run_hids_batch(
    hids: list[int],
    houses: list[dict[str, Any]],
    workers: int,
    error_out,
    batch_label: str,
) -> tuple[list[int], dict[str, int]]:
    total = len(hids)
    in_flight_limit = max(workers * IN_FLIGHT_MULTIPLIER, workers)
    start_ts = time.time()
    failed_hids: list[int] = []
    counters = {
        "done": 0,
        "residential": 0,
        "not_residential": 0,
        "errors": 0,
        "attempts_total": 0,
    }

    logging.info(
        "[%s] Старт партии | всего %s | workers=%s | in_flight_limit=%s",
        batch_label,
        total,
        workers,
        in_flight_limit,
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        hid_iter = iter(hids)
        future_to_hid: dict[Future[FetchResult], int] = {}

        while len(future_to_hid) < in_flight_limit:
            try:
                hid = next(hid_iter)
            except StopIteration:
                break
            future_to_hid[pool.submit(fetch_house, hid)] = hid

        while future_to_hid:
            done_set, _ = wait(set(future_to_hid.keys()), return_when=FIRST_COMPLETED)
            for fut in done_set:
                hid = future_to_hid.pop(fut)
                try:
                    result = fut.result()
                except Exception as exc:  # safety net
                    result = FetchResult(
                        hid=hid,
                        status="error",
                        attempts=MAX_RETRIES,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                _process_result(result, houses, failed_hids, counters, error_out)
                counters["done"] += 1

                if counters["done"] % PROGRESS_STEP == 0 or counters["done"] == total:
                    elapsed = max(time.time() - start_ts, 1e-9)
                    speed = counters["done"] / elapsed
                    left = total - counters["done"]
                    eta = left / speed if speed > 0 else 0.0
                    err_rate = (counters["errors"] / counters["done"]) * 100 if counters["done"] else 0.0
                    avg_attempts = counters["attempts_total"] / counters["done"] if counters["done"] else 0.0
                    logging.info(
                        (
                            "[%s] Прогресс %s/%s (%.2f%%) | жилой=%s | не жилой=%s | ошибки=%s (%.2f%%) "
                            "| avg_attempts=%.2f | speed=%.1f req/s | ETA=%s"
                        ),
                        batch_label,
                        counters["done"],
                        total,
                        (counters["done"] / total) * 100,
                        counters["residential"],
                        counters["not_residential"],
                        counters["errors"],
                        err_rate,
                        avg_attempts,
                        speed,
                        _format_eta(eta),
                    )

                try:
                    next_hid = next(hid_iter)
                    future_to_hid[pool.submit(fetch_house, next_hid)] = next_hid
                except StopIteration:
                    pass

    elapsed = max(time.time() - start_ts, 1e-9)
    logging.info(
        (
            "[%s] Финиш партии: done=%s | жилой=%s | не жилой=%s | ошибки=%s | "
            "время=%s | скорость=%.1f req/s"
        ),
        batch_label,
        counters["done"],
        counters["residential"],
        counters["not_residential"],
        counters["errors"],
        _format_eta(elapsed),
        counters["done"] / elapsed,
    )
    return failed_hids, counters


def run_range(hid_start: int, hid_end: int, out_path: Path, workers: int) -> None:
    if hid_end < hid_start:
        raise ValueError("--end не может быть меньше --start")
    if workers <= 0:
        raise ValueError("--workers должен быть больше 0")

    start_ts = time.time()
    all_hids = list(range(hid_start, hid_end + 1))
    total = len(all_hids)
    houses: list[dict[str, Any]] = []
    rounds_total = 0
    recovery_rounds_used = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    FAILED_DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.info(
        "Старт: hid %s-%s | всего %s | workers=%s",
        hid_start,
        hid_end,
        total,
        workers,
    )
    logging.info(
        "Файлы: result=%s | failed_hids=%s | failed_details=%s | log=%s",
        out_path.resolve(),
        FAILED_HIDS_PATH.resolve(),
        FAILED_DETAILS_PATH.resolve(),
        LOG_PATH.resolve(),
    )

    with open(FAILED_DETAILS_PATH, "w", encoding="utf-8") as error_out:
        failed_hids, _ = _run_hids_batch(
            hids=all_hids,
            houses=houses,
            workers=workers,
            error_out=error_out,
            batch_label="main",
        )
        rounds_total += 1

        while failed_hids and recovery_rounds_used < MAX_RECOVERY_ROUNDS:
            recovery_rounds_used += 1
            pending = sorted(set(failed_hids))
            logging.warning(
                "Раунд восстановления %s/%s: повторяем %s hid с ошибками",
                recovery_rounds_used,
                MAX_RECOVERY_ROUNDS,
                len(pending),
            )
            failed_hids, _ = _run_hids_batch(
                hids=pending,
                houses=houses,
                workers=workers,
                error_out=error_out,
                batch_label=f"recovery-{recovery_rounds_used}",
            )
            rounds_total += 1
            if failed_hids:
                logging.warning(
                    "После recovery-%s осталось ошибок: %s. Пауза %.1f сек перед следующим раундом.",
                    recovery_rounds_used,
                    len(set(failed_hids)),
                    RECOVERY_PAUSE_SECONDS,
                )
                time.sleep(RECOVERY_PAUSE_SECONDS)

    houses.sort(key=lambda x: int(x.get("house_id", 0)))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(houses, f, ensure_ascii=False, indent=2)

    remaining_failed = sorted(set(failed_hids))
    if remaining_failed:
        with open(FAILED_HIDS_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(str(x) for x in remaining_failed))
    elif FAILED_HIDS_PATH.exists():
        FAILED_HIDS_PATH.unlink()

    if failed_hids:
        logging.error(
            "Авто-восстановление не завершило все ошибки: осталось %s hid.",
            len(remaining_failed),
        )
    elapsed = max(time.time() - start_ts, 1e-9)
    avg_speed = total / elapsed if elapsed > 0 else 0.0
    logging.info(
        "Финиш всего процесса: rounds=%s (recovery=%s) | workers=%s | время=%s | средняя скорость=%.1f req/s",
        rounds_total,
        recovery_rounds_used,
        workers,
        _format_eta(elapsed),
        avg_speed,
    )
    logging.info("JSON сохранен: %s", out_path.resolve())
    logging.info("Детали ошибок: %s", FAILED_DETAILS_PATH.resolve())
    if remaining_failed:
        logging.warning("Неуспешные hid: %s (список в %s)", len(remaining_failed), FAILED_HIDS_PATH.resolve())
    else:
        logging.info("Ошибок после ретраев нет.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Парсинг flatinfo по hid (сохраняем только jil_type=Жилой)")
    parser.add_argument("--start", type=int, default=HID_START, help="Начальный hid (включительно)")
    parser.add_argument("--end", type=int, default=HID_END, help="Конечный hid (включительно)")
    parser.add_argument("-o", "--output", type=Path, default=OUTPUT_PATH, help="Выходной JSON")
    parser.add_argument("-w", "--workers", type=int, default=MAX_WORKERS, help="Количество воркеров")
    args = parser.parse_args()

    setup_logging()
    try:
        run_range(args.start, args.end, args.output, args.workers)
    except Exception as exc:
        logging.exception("Критическая ошибка: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
