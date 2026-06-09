from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Settings
from .io import ResultWriter, load_input_houses
from .runner import ParserRunner

log = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent.parent

TITLE_PARSED_KEYS = (
    "total_area_sqm",
    "rooms",
    "floor_current",
    "floor_total",
)


def sample_houses(
    input_path: Path,
    *,
    sample_size: int,
    seed: int,
    moscow_only: bool = True,
) -> list:
    houses = load_input_houses(input_path, moscow_only=moscow_only)
    rng = random.Random(seed)
    n = min(sample_size, len(houses))
    return rng.sample(houses, n)


def _classify_error(error: str) -> str:
    if not error:
        return "unknown"
    m = re.search(r"HTTP (\d+)", error)
    if m:
        return f"HTTP {m.group(1)}"
    lower = error.lower()
    if "timeout" in lower:
        return "timeout"
    if "connection" in lower:
        return "connection"
    return error[:80]


def analyze_smoke_results(jsonl_path: Path) -> dict[str, Any]:
    houses_ok = 0
    offers_total = 0
    details_ok = 0
    details_failed = 0
    title_fill: Counter[str] = Counter()
    title_with_title = 0
    feature_fill: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    examples_ok: list[dict[str, Any]] = []
    examples_fail: list[dict[str, Any]] = []

    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            houses_ok += 1
            for offer in row.get("deactivated_offers", []):
                offers_total += 1
                if offer.get("title"):
                    title_with_title += 1
                tp = offer.get("title_parsed") or {}
                for key in TITLE_PARSED_KEYS:
                    if tp.get(key) is not None:
                        title_fill[key] += 1

                if offer.get("details") is not None:
                    details_ok += 1
                    fp = (offer.get("details") or {}).get("features_parsed") or {}
                    for key, val in fp.items():
                        if val is not None and val != "":
                            feature_fill[key] += 1
                    if len(examples_ok) < 3:
                        examples_ok.append({
                            "id": offer.get("id"),
                            "title_parsed": tp,
                            "features_parsed": fp,
                        })
                else:
                    details_failed += 1
                    err = offer.get("details_error") or "no details"
                    error_types[_classify_error(str(err))] += 1
                    if len(examples_fail) < 3:
                        examples_fail.append({
                            "id": offer.get("id"),
                            "error": err,
                        })

    def _pct(n: int, denom: int) -> float:
        return round(100.0 * n / denom, 1) if denom else 0.0

    title_denom = title_with_title or offers_total
    top_features = [
        {"key": k, "count": v, "pct": _pct(v, offers_total)}
        for k, v in feature_fill.most_common(10)
    ]

    return {
        "houses_ok": houses_ok,
        "offers_total": offers_total,
        "details_ok": details_ok,
        "details_failed": details_failed,
        "details_success_rate_pct": _pct(details_ok, offers_total),
        "title_parsed_fill_rate_pct": {
            key: _pct(title_fill[key], title_denom)
            for key in TITLE_PARSED_KEYS
        },
        "features_parsed_top10": top_features,
        "error_types": dict(error_types.most_common()),
        "examples_ok": examples_ok,
        "examples_fail": examples_fail,
    }


def print_report(report: dict[str, Any], *, elapsed_sec: float | None = None) -> None:
    log.info("=" * 55)
    log.info("  SMOKE TEST REPORT")
    log.info("=" * 55)
    if elapsed_sec is not None:
        log.info("  Elapsed:                       %.1f s", elapsed_sec)
    log.info("  Houses ok:                     %s", report["houses_ok"])
    log.info("  Offers total:                  %s", report["offers_total"])
    log.info("  Details ok / failed:           %s / %s", report["details_ok"], report["details_failed"])
    log.info("  Details success rate:          %s%%", report["details_success_rate_pct"])
    log.info("  title_parsed fill rates:")
    for key, pct in report["title_parsed_fill_rate_pct"].items():
        log.info("    %-20s %s%%", key, pct)
    if report["features_parsed_top10"]:
        log.info("  features_parsed top keys:")
        for item in report["features_parsed_top10"]:
            log.info("    %-20s %s (%s%%)", item["key"], item["count"], item["pct"])
    if report["error_types"]:
        log.info("  Error types:")
        for err, cnt in report["error_types"].items():
            log.info("    %-20s %s", err, cnt)
    if report["examples_ok"]:
        log.info("  Examples OK:")
        for ex in report["examples_ok"]:
            log.info("    %s", json.dumps(ex, ensure_ascii=False))
    if report["examples_fail"]:
        log.info("  Examples FAIL:")
        for ex in report["examples_fail"]:
            log.info("    %s", json.dumps(ex, ensure_ascii=False))
    log.info("=" * 55)


def cmd_smoke_test(args: argparse.Namespace) -> int:
    if args.analyze_only:
        if not args.analyze_only.is_file():
            log.error("File not found: %s", args.analyze_only)
            return 1
        report = analyze_smoke_results(args.analyze_only)
        print_report(report)
        if args.report:
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return 0

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
        log.error(
            "Не заданы cookies Cian (CIAN_COOKIES, cian/cookies.txt "
            "или cookie-сервер CIAN_COOKIE_SERVER_URL)"
        )
        return 1

    houses = sample_houses(
        args.input,
        sample_size=args.sample_size,
        seed=args.seed,
        moscow_only=settings.moscow_only,
    )
    log.info("Smoke sample: %s houses (seed=%s)", len(houses), args.seed)

    writer = ResultWriter(args.output, args.failed)
    runner = ParserRunner(settings, writer)
    started = time.monotonic()
    stats = runner.run(houses)
    elapsed = time.monotonic() - started

    log.info(
        "Smoke run: ok=%s fail=%s skipped=%s за %.1f с",
        stats.success,
        stats.failed,
        stats.skipped,
        elapsed,
    )

    if args.output.is_file():
        report = analyze_smoke_results(args.output)
        report["houses_failed"] = stats.failed
        report["houses_skipped"] = stats.skipped
        report["elapsed_sec"] = round(elapsed, 1)
        print_report(report, elapsed_sec=elapsed)
        report_path = args.report or args.output.with_name("smoke_test_report.json")
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Report saved to %s", report_path)

    return 0


def add_smoke_test_parser(sub: argparse._SubParsersAction) -> None:
    smoke = sub.add_parser(
        "smoke-test",
        help="Smoke-тест на случайной выборке домов + анализ",
    )
    smoke.add_argument(
        "--analyze-only",
        type=Path,
        default=None,
        metavar="JSONL",
        help="Только анализ существующего JSONL без нового прогона",
    )
    smoke.add_argument(
        "-i",
        "--input",
        type=Path,
        default=_DIR / "data" / "input_all.json",
    )
    smoke.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DIR / "data" / "smoke_test_result.jsonl",
    )
    smoke.add_argument(
        "--failed",
        type=Path,
        default=_DIR / "data" / "smoke_test_failed.jsonl",
    )
    smoke.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Путь для smoke_test_report.json",
    )
    smoke.add_argument("--sample-size", type=int, default=40)
    smoke.add_argument("--seed", type=int, default=42)
    smoke.add_argument("-w", "--workers", type=int, default=4)
    smoke.add_argument("--detail-workers", type=int, default=16)
    smoke.add_argument("--offers-per-page", type=int, default=50)
    smoke.add_argument(
        "--proxies",
        type=Path,
        default=_DIR / "data" / "proxies.txt",
    )
    smoke.add_argument("--proxy", type=str, default=None)
    smoke.add_argument("--skip-details", action="store_true")
    smoke.add_argument("--all-cities", action="store_true")
    smoke.add_argument("-v", "--verbose", action="store_true")
    smoke.set_defaults(cmd=cmd_smoke_test)
