from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Settings
from .io import jsonl_to_json, load_input_houses
from .io import ResultWriter
from .pipeline import HousePipeline
from .runner import ParserRunner

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
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=_DIR / "input.json",
        help="Входной JSON (массив домов flatinfo)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DIR / "result.jsonl",
        help="Успешные результаты (JSONL, по строке на дом)",
    )
    parser.add_argument(
        "--failed",
        type=Path,
        default=_DIR / "failed.jsonl",
        help="Ошибки (JSONL)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=_DIR / "checkpoint.json",
        help="Чекпоинт обработанных house_id",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=16,
        help="Число параллельных воркеров",
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
        "-v",
        "--verbose",
        action="store_true",
    )
    sub = parser.add_subparsers(dest="command")

    merge = sub.add_parser("merge", help="Собрать JSONL в один JSON-массив")
    merge.add_argument("jsonl", type=Path, nargs="?", default=_DIR / "result.jsonl")
    merge.add_argument("json", type=Path, nargs="?", default=_DIR / "result.json")

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env(
        workers=args.workers,
        offers_per_page=args.offers_per_page,
        moscow_only=not args.all_cities,
    )
    if not settings.cian_cookies:
        logging.error(
            "Не заданы cookies Cian. Скопируйте из браузера в cian/cookies.txt "
            "или переменную CIAN_COOKIES."
        )
        return 1

    houses = load_input_houses(
        args.input,
        moscow_only=settings.moscow_only,
        limit=args.limit,
        offset=args.offset,
    )
    logging.info("К обработке: %s домов", len(houses))

    writer = ResultWriter(args.output, args.failed, args.checkpoint)
    pipeline = HousePipeline(settings)
    runner = ParserRunner(settings, pipeline, writer)
    stats = runner.run(houses)

    logging.info(
        "Готово: total=%s skipped=%s ok=%s fail=%s за %.1f с",
        stats.total,
        stats.skipped,
        stats.success,
        stats.failed,
        stats.elapsed_sec,
    )
    return 0 if stats.failed == 0 else 0


def cmd_merge(args: argparse.Namespace) -> int:
    if not args.jsonl.is_file():
        logging.error("Файл не найден: %s", args.jsonl)
        return 1
    count = jsonl_to_json(args.jsonl, args.json)
    logging.info("Записано %s записей в %s", count, args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.command == "merge":
        return cmd_merge(args)
    return cmd_run(args)
