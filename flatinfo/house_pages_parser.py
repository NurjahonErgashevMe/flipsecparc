#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests
from bs4 import BeautifulSoup

URL_TEMPLATE = "https://flatinfo.ru/h_info1.asp?hid={hid}"
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
}

_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = _DIR / "house_pages_result.json"
LOG_PATH = _DIR / "house_pages_parser.log"
FAILED_HIDS_PATH = _DIR / "house_pages_failed_hids.txt"
FAILED_DETAILS_PATH = _DIR / "house_pages_failed_details.jsonl"

DEFAULT_WORKERS = 120
DEFAULT_HID_START = 200
DEFAULT_HID_END = 400_000
REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 4
RETRY_BASE_DELAY = 0.5
PROGRESS_STEP = 500
IN_FLIGHT_MULTIPLIER = 4
MAX_RECOVERY_ROUNDS = 30
RECOVERY_PAUSE_SECONDS = 5.0

FetchStatus = Literal["ok", "not_found", "error"]
FALLBACK_EMPTY = ""
RUNTIME_COOKIE = ""


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
    payload: dict[str, Any] | None = None
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


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def _clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u2009", " ")
    return " ".join(value.split()).strip()


def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "00:00:00"
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _extract_address(soup: BeautifulSoup) -> str:
    title = soup.find("h1", class_="page__title")
    if title:
        title_text = _clean_text(title.get_text(" ", strip=True))
        match = re.search(r"Дом по адресу\s+(.+?)\s+в\s+Москве", title_text, re.IGNORECASE)
        if match:
            return _clean_text(match.group(1))

    city = ""
    street = ""
    house_num = ""
    for li in soup.select("li.fi-list-item"):
        label_el = li.select_one(".fi-list-item__label")
        value_el = li.select_one(".fi-list-item__value")
        if not label_el or not value_el:
            continue
        label = _clean_text(label_el.get_text(" ", strip=True)).lower()
        value = _clean_text(value_el.get_text(" ", strip=True))
        if label.startswith("нас. пункт"):
            city = value
        elif label.startswith("улица") or label.startswith("адрес"):
            street = value
        elif "дом" == label:
            house_num = value

    parts = [p for p in (city, street, house_num) if p]
    return ", ".join(parts)


def _build_label_map(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for li in soup.select("li.fi-list-item"):
        label_el = li.select_one(".fi-list-item__label")
        value_el = li.select_one(".fi-list-item__value")
        if not label_el or not value_el:
            continue
        label = _clean_text(label_el.get_text(" ", strip=True))
        value = _clean_text(value_el.get_text(" ", strip=True))
        if label:
            out[label] = value
    return out


def _pick_value(label_map: dict[str, str], *needles: str) -> str:
    lowered_items = [(k.lower(), v) for k, v in label_map.items()]
    for needle in needles:
        n = needle.lower()
        for key, value in lowered_items:
            if n in key:
                return value
    return FALLBACK_EMPTY


def _extract_metro_info(soup: BeautifulSoup) -> str:
    station_rows = soup.select("ul.fi-list.underground li.fi-list-item")
    walk = FALLBACK_EMPTY
    transport = FALLBACK_EMPTY

    for row in station_rows:
        label_el = row.select_one(".fi-list-item__label")
        value_el = row.select_one(".fi-list-item__value")
        if not label_el or not value_el:
            continue
        station = _clean_text(label_el.get_text(" ", strip=True))
        travel = _clean_text(value_el.get_text(" ", strip=True))
        combined = f"{station}: {travel}" if station else travel

        if (("мин" in travel) or ("м " in f"{travel} ")) and not walk:
            walk = combined
        if "ост." in travel and not transport:
            transport = combined

    if walk and transport:
        return f"пешком: {walk}; транспорт: {transport}"
    if walk:
        return f"пешком: {walk}"
    if transport:
        return f"транспорт: {transport}"
    return FALLBACK_EMPTY


def parse_house_page(html: str, hid: int) -> tuple[FetchStatus, dict[str, Any] | None]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean_text(soup.get_text(" ", strip=True)).lower()
    if "403 forbidden" in page_text or "доступ запрещен" in page_text or "captcha" in page_text:
        return "error", None

    label_map = _build_label_map(soup)

    # Если на странице нет ключевых блоков с параметрами дома:
    # - "не найден" трактуем как not_found
    # - всё остальное как ошибку (например, антибот/ломаная выдача).
    if not label_map:
        if "не найден" in page_text or "не существует" in page_text:
            return "not_found", None
        return "error", None

    lifts_pass = _pick_value(label_map, "Пассажирских лифтов")
    lifts_cargo = _pick_value(label_map, "Грузовых лифтов")
    lifts = f"пасс.: {lifts_pass}; груз.: {lifts_cargo}"

    levels = _pick_value(label_map, "Этажей всего")
    floors_text = levels if levels else FALLBACK_EMPTY
    series = _pick_value(label_map, "Типовая серия")

    item: dict[str, Any] = {
        "house_id": hid,
        "address": _extract_address(soup),
        "year": _pick_value(label_map, "Год постройки"),
        "overlaps": _pick_value(label_map, "Перекрытия"),
        "house_type": _pick_value(label_map, "Тип дома"),
        "floors_text": floors_text,
        "series": series,
        "okrug": _pick_value(label_map, "Округ"),
        "rayon": _pick_value(label_map, "Район"),
        "lifts": lifts if lifts != "пасс.: ; груз.: " else FALLBACK_EMPTY,
        "ceiling_height": _pick_value(label_map, "Высота потолков"),
        "management_company": _pick_value(label_map, "Управляющая компания"),
        "residential_complex": _pick_value(label_map, "Жилой комплекс"),
        "metro": _extract_metro_info(soup),
        "developer": _pick_value(label_map, "Застройщик"),
    }

    # По требованию: отсутствующие поля должны быть пустой строкой.
    for key, value in list(item.items()):
        if key == "house_id":
            continue
        item[key] = value if value else FALLBACK_EMPTY

    return "ok", item


def fetch_and_parse_house(hid: int, timeout: float = REQUEST_TIMEOUT) -> FetchResult:
    session = _get_session()
    last_error_type: str | None = None
    last_error_msg: str | None = None
    last_http_status: int | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req_headers: dict[str, str] = {
                "referer": URL_TEMPLATE.format(hid=max(DEFAULT_HID_START, hid - 1))
            }
            if RUNTIME_COOKIE:
                req_headers["cookie"] = RUNTIME_COOKIE

            response = session.get(URL_TEMPLATE.format(hid=hid), headers=req_headers, timeout=timeout)
            last_http_status = response.status_code
            response.raise_for_status()

            status, parsed = parse_house_page(response.text, hid)
            return FetchResult(
                hid=hid,
                status=status,
                payload=parsed,
                attempts=attempt,
                http_status=response.status_code,
            )
        except (requests.RequestException, ValueError) as exc:
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


def _process_result(
    result: FetchResult,
    rows: list[dict[str, Any]],
    failed_hids: list[int],
    counters: dict[str, int],
    error_out,
) -> None:
    counters["attempts_total"] += result.attempts

    if result.status == "ok" and result.payload is not None:
        rows.append(result.payload)
        counters["ok"] += 1
        return
    if result.status == "not_found":
        counters["not_found"] += 1
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
    rows: list[dict[str, Any]],
    workers: int,
    error_out,
    batch_label: str,
) -> tuple[list[int], dict[str, int]]:
    total = len(hids)
    in_flight_limit = max(workers * IN_FLIGHT_MULTIPLIER, workers)
    started = time.time()
    failed_hids: list[int] = []
    counters = {
        "done": 0,
        "ok": 0,
        "not_found": 0,
        "errors": 0,
        "attempts_total": 0,
    }

    logging.info(
        "[%s] Старт партии | всего=%s | workers=%s | in_flight_limit=%s",
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
            future_to_hid[pool.submit(fetch_and_parse_house, hid)] = hid

        while future_to_hid:
            done_set, _ = wait(set(future_to_hid.keys()), return_when=FIRST_COMPLETED)
            for future in done_set:
                hid = future_to_hid.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # safety net
                    result = FetchResult(
                        hid=hid,
                        status="error",
                        attempts=MAX_RETRIES,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )

                _process_result(result, rows, failed_hids, counters, error_out)
                counters["done"] += 1

                if counters["done"] % PROGRESS_STEP == 0 or counters["done"] == total:
                    elapsed = max(time.time() - started, 1e-9)
                    speed = counters["done"] / elapsed
                    remaining = total - counters["done"]
                    eta = remaining / speed if speed > 0 else 0.0
                    err_rate = (counters["errors"] / counters["done"]) * 100 if counters["done"] else 0.0
                    logging.info(
                        (
                            "[%s] Прогресс %s/%s (%.2f%%) | ok=%s | not_found=%s | errors=%s (%.2f%%) "
                            "| speed=%.1f req/s | ETA=%s"
                        ),
                        batch_label,
                        counters["done"],
                        total,
                        (counters["done"] / total) * 100,
                        counters["ok"],
                        counters["not_found"],
                        counters["errors"],
                        err_rate,
                        speed,
                        _format_eta(eta),
                    )

                try:
                    next_hid = next(hid_iter)
                    future_to_hid[pool.submit(fetch_and_parse_house, next_hid)] = next_hid
                except StopIteration:
                    pass

    elapsed = max(time.time() - started, 1e-9)
    logging.info(
        "[%s] Финиш партии | done=%s | ok=%s | not_found=%s | errors=%s | speed=%.1f req/s",
        batch_label,
        counters["done"],
        counters["ok"],
        counters["not_found"],
        counters["errors"],
        counters["done"] / elapsed,
    )
    return failed_hids, counters


def run_range(hid_start: int, hid_end: int, workers: int, output_path: Path) -> None:
    if hid_end < hid_start:
        raise ValueError("--end не может быть меньше --start")
    if workers <= 0:
        raise ValueError("--workers должен быть > 0")

    all_hids = list(range(hid_start, hid_end + 1))
    total = len(all_hids)
    started = time.time()
    rows: list[dict[str, Any]] = []
    rounds_total = 0
    recovery_rounds = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    FAILED_DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.info("Старт парсинга страниц домов: hid=%s-%s | всего=%s | workers=%s", hid_start, hid_end, total, workers)
    logging.info(
        "Файлы: output=%s | log=%s | failed_hids=%s | failed_details=%s",
        output_path.resolve(),
        LOG_PATH.resolve(),
        FAILED_HIDS_PATH.resolve(),
        FAILED_DETAILS_PATH.resolve(),
    )

    with open(FAILED_DETAILS_PATH, "w", encoding="utf-8") as error_out:
        failed_hids, _ = _run_hids_batch(
            hids=all_hids,
            rows=rows,
            workers=workers,
            error_out=error_out,
            batch_label="main",
        )
        rounds_total += 1

        while failed_hids and recovery_rounds < MAX_RECOVERY_ROUNDS:
            recovery_rounds += 1
            pending = sorted(set(failed_hids))
            logging.warning(
                "Раунд восстановления %s/%s: повторяем %s hid с ошибками",
                recovery_rounds,
                MAX_RECOVERY_ROUNDS,
                len(pending),
            )
            failed_hids, _ = _run_hids_batch(
                hids=pending,
                rows=rows,
                workers=workers,
                error_out=error_out,
                batch_label=f"recovery-{recovery_rounds}",
            )
            rounds_total += 1

            if failed_hids:
                logging.warning(
                    "После recovery-%s осталось %s ошибок. Пауза %.1f сек.",
                    recovery_rounds,
                    len(set(failed_hids)),
                    RECOVERY_PAUSE_SECONDS,
                )
                time.sleep(RECOVERY_PAUSE_SECONDS)

    # Последняя запись по house_id побеждает (если hid попал в recovery успешно после main).
    uniq: dict[int, dict[str, Any]] = {}
    for row in rows:
        hid = row.get("house_id")
        if isinstance(hid, int):
            uniq[hid] = row

    final_rows = [uniq[k] for k in sorted(uniq.keys())]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_rows, f, ensure_ascii=False, indent=2)

    remaining_failed = sorted(set(failed_hids))
    if remaining_failed:
        with open(FAILED_HIDS_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(str(x) for x in remaining_failed))
    elif FAILED_HIDS_PATH.exists():
        FAILED_HIDS_PATH.unlink()

    elapsed = max(time.time() - started, 1e-9)
    logging.info(
        (
            "Финиш процесса: rounds=%s (recovery=%s) | workers=%s | время=%s | avg_speed=%.1f req/s | "
            "rows_saved=%s | errors_left=%s"
        ),
        rounds_total,
        recovery_rounds,
        workers,
        _format_eta(elapsed),
        total / elapsed,
        len(final_rows),
        len(remaining_failed),
    )
    logging.info("JSON сохранен: %s", output_path.resolve())
    if remaining_failed:
        logging.warning("Остались неуспешные hid: %s (см. %s)", len(remaining_failed), FAILED_HIDS_PATH.resolve())
    else:
        logging.info("Ошибок после раундов восстановления нет.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Парсер страниц домов flatinfo (14 полей + house_id)")
    parser.add_argument("--start", type=int, default=DEFAULT_HID_START, help="Начальный hid (включительно)")
    parser.add_argument("--end", type=int, default=DEFAULT_HID_END, help="Конечный hid (включительно)")
    parser.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, help="Количество воркеров")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Выходной JSON")
    parser.add_argument(
        "--cookie",
        type=str,
        default=os.environ.get("FLATINFO_COOKIE", ""),
        help="Cookie-строка браузера (или через переменную окружения FLATINFO_COOKIE)",
    )
    args = parser.parse_args()

    global RUNTIME_COOKIE
    RUNTIME_COOKIE = args.cookie.strip()

    setup_logging()
    if RUNTIME_COOKIE:
        logging.info("Cookie для запросов включен (длина=%s)", len(RUNTIME_COOKIE))
    else:
        logging.info("Cookie не задан. Если будут 403/antibot, запусти с --cookie или FLATINFO_COOKIE.")

    try:
        run_range(
            hid_start=args.start,
            hid_end=args.end,
            workers=args.workers,
            output_path=args.output,
        )
    except Exception as exc:
        logging.exception("Критическая ошибка: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
