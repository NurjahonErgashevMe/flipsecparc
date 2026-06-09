from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from .config import Settings
from .io import ResultWriter, json_to_excel, jsonl_to_json, load_input_houses
from .runner import ParserRunner
from .smoke_test import add_smoke_test_parser

_DIR = Path(__file__).resolve().parent.parent


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Парсер истории снятых объявлений Cian по адресам из JSON.",
    )
    default_input = _DIR / "data" / "input_all.json"
    if not default_input.is_file():
        default_input = _DIR / "data" / "input.json"

    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=default_input,
        help="Входной JSON (массив домов flatinfo)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DIR / "data" / "result.jsonl",
        help="Успешные результаты (JSONL, по строке на дом)",
    )
    parser.add_argument(
        "--failed",
        type=Path,
        default=_DIR / "data" / "failed.jsonl",
        help="Ошибки (JSONL)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Повторно запустить обработку для всех объявлений из failed.jsonl",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=16,
        help="Число параллельных воркеров (дома)",
    )
    parser.add_argument(
        "--detail-workers",
        type=int,
        default=32,
        help="Число параллельных воркеров для details API",
    )
    parser.add_argument(
        "--proxies",
        type=Path,
        default=_DIR / "data" / "proxies.txt",
        help="Файл прокси для details API",
    )
    parser.add_argument(
        "--skip-details",
        action="store_true",
        help="Не загружать details (features, images) по offer id",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Обработать только N домов (для теста)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Пропустить первые N домов после фильтра Москвы",
    )
    parser.add_argument(
        "--offers-per-page",
        type=int,
        default=50,
        help="Размер страницы offer history API",
    )
    parser.add_argument(
        "--all-cities",
        action="store_true",
        help="Не фильтровать только Москву",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Прокси URL для geocode/yandex (http://user:pass@host:port)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
    )
    sub = parser.add_subparsers(dest="command")

    merge = sub.add_parser("merge", help="Собрать JSONL в один JSON-массив + Excel")
    merge.add_argument("jsonl", type=Path, nargs="?", default=_DIR / "data" / "result.jsonl")
    merge.add_argument("json", type=Path, nargs="?", default=_DIR / "data" / "result.json")
    merge.add_argument("--excel", type=Path, default=_DIR / "data" / "result.xlsx", help="Путь для Excel-файла")

    add_smoke_test_parser(sub)

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env(
        workers=args.workers,
        offers_per_page=args.offers_per_page,
        moscow_only=not args.all_cities,
        proxy_url=args.proxy,
        detail_workers=args.detail_workers,
        proxies_path=args.proxies,
        skip_details=args.skip_details,
    )
    if not settings.cian_cookies:
        logging.error(
            "Не заданы cookies Cian. Укажите CIAN_COOKIES, cian/cookies.txt "
            "или проверьте доступность cookie-сервера (CIAN_COOKIE_SERVER_URL)."
        )
        return 1

    houses = load_input_houses(
        args.input,
        moscow_only=settings.moscow_only,
        limit=args.limit,
        offset=args.offset,
    )

    if args.retry_failed:
        failed_ids: set[int] = set()
        if args.failed.is_file():
            with args.failed.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        failed_ids.add(int(data["source"]["house_id"]))
                    except Exception:
                        pass
        houses = [h for h in houses if h.house_id in failed_ids]
        logging.info("К повторной обработке (failed): %s домов", len(houses))

        if args.failed.is_file():
            bak_path = args.failed.with_name(args.failed.name + ".bak")
            shutil.copy(args.failed, bak_path)
            args.failed.write_text("", encoding="utf-8")
    else:
        logging.info("К обработке: %s домов", len(houses))

    if not settings.skip_details:
        logging.info(
            "Details enrichment: detail_workers=%s proxies=%s",
            settings.detail_workers,
            settings.proxies_path,
        )

    writer = ResultWriter(args.output, args.failed)
    runner = ParserRunner(settings, writer)
    stats = runner.run(houses)

    logging.info(
        "Готово: total=%s skipped=%s ok=%s fail=%s за %.1f с",
        stats.total,
        stats.skipped,
        stats.success,
        stats.failed,
        stats.elapsed_sec,
    )
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    if not args.jsonl.is_file():
        logging.error("Файл не найден: %s", args.jsonl)
        return 1
    stats = jsonl_to_json(args.jsonl, args.json)

    logging.info("=" * 55)
    logging.info("  ИТОГИ MERGE -> %s", args.json.name)
    logging.info("=" * 55)
    logging.info("  Домов всего:                  %s", stats["total_houses"])
    logging.info("  Снятых публикаций всего:       %s", stats["total_deactivated_offers"])
    logging.info("  Домов с публикациями:          %s", stats["houses_with_offers"])
    logging.info("  Домов без публикаций:          %s", stats["houses_without_offers"])
    logging.info("  Среднее публикаций на дом:     %s", stats["avg_offers_per_house"])
    logging.info("  Макс. публикаций в одном доме: %s", stats["max_offers_in_house"])
    logging.info("  Мин. публикаций в одном доме:  %s", stats["min_offers_in_house"])
    if stats["skipped_lines"]:
        logging.warning("  Пропущено битых строк:         %s", stats["skipped_lines"])
    logging.info("=" * 55)

    logging.info("Генерация Excel -> %s", args.excel)
    excel_stats = json_to_excel(args.json, args.excel)
    logging.info("  Excel: строк=%s, файл=%s", excel_stats["total_rows"], excel_stats["excel_path"])

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "merge":
        return cmd_merge(args)
    if args.command == "smoke-test":
        return args.cmd(args)
    return cmd_run(args)
