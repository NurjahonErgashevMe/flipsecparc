"""
CIAN Offer Parser — CLI entry point.

Usage:
    python -m cian.offer_parser.main --input ids.txt --workers 5 --limit 10
    python -m cian.offer_parser.main --input ids.txt --output docs/result.xlsx
    python -m cian.offer_parser.main --test-local cian/examples/cian-sale-flat.html
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .client import CianOfferClient, CookieManager, ProxyManager
from .extractor import OfferData, extract_offer

log = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent.parent  # cian/


# -------------------------------------------------------------------
#  Logging setup
# -------------------------------------------------------------------
def _setup_logging(verbose: bool) -> None:
    # Fix Windows console encoding for Cyrillic / Unicode symbols
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace"
        )

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# -------------------------------------------------------------------
#  Load input IDs
# -------------------------------------------------------------------
def _load_offer_ids(path: Path, limit: int | None = None, offset: int = 0) -> list[int]:
    """
    Load offer IDs from a file. Supports:
    1. Plain text with IDs on each line.
    2. JSON array of houses (result.json) with deactivated_offers.
    3. JSONL file of houses (result.jsonl) with deactivated_offers.
    """
    ids: list[int] = []
    
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data:
                for offer in item.get("deactivated_offers", []):
                    ids.append(int(offer["id"]))
        except Exception as e:
            log.error("Failed to parse JSON file: %s", e)
    else:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            # Try JSONL format first
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    for offer in data.get("deactivated_offers", []):
                        ids.append(int(offer["id"]))
                    continue
                except json.JSONDecodeError:
                    pass
            
            # Plain text fallback
            try:
                ids.append(int(line.split(",")[0].split("\t")[0].strip()))
            except ValueError:
                continue

    if offset > 0:
        ids = ids[offset:]

    if limit is not None:
        ids = ids[:limit]

    return ids


# -------------------------------------------------------------------
#  Process a single offer
# -------------------------------------------------------------------
def _process_offer(
    offer_id: int,
    client: CianOfferClient,
) -> OfferData | dict[str, str]:
    """Fetch and parse one offer, return OfferData or error dict."""
    try:
        html = client.fetch_offer_page(offer_id)
        data = extract_offer(html, offer_id)
        return data
    except Exception as exc:
        log.error("[%s] FAILED: %s", offer_id, exc)
        return {"offer_id": str(offer_id), "error": str(exc)}


# -------------------------------------------------------------------
#  Export to JSON
# -------------------------------------------------------------------
def _export_to_json(
    results: list[OfferData],
    output_path: Path,
    append: bool = False,
) -> None:
    """Export parsed offers to JSON."""
    data = [r.to_dict() for r in results]
    
    if append and output_path.is_file():
        try:
            with output_path.open("r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if isinstance(existing_data, list):
                existing_data.extend(data)
                data = existing_data
        except Exception as e:
            log.warning("Failed to load existing JSON for append, overwriting: %s", e)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    log.info("Saved %d offers to %s", len(results) if not append else len(data), output_path)


# -------------------------------------------------------------------
#  Local test mode
# -------------------------------------------------------------------
def _cmd_test_local(html_path: Path) -> int:
    """Parse a local HTML file and print results."""
    if not html_path.is_file():
        log.error("File not found: %s", html_path)
        return 1

    html = html_path.read_text(encoding="utf-8")
    data = extract_offer(html, offer_id="local_test")

    print("\n" + "=" * 60)
    print("  PARSED DATA")
    print("=" * 60)
    for key, val in data.to_dict().items():
        print(f"  {key:30s} | {val}")
    print("=" * 60 + "\n")
    return 0


# -------------------------------------------------------------------
#  Main run
# -------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> int:
    """Main parsing run."""

    # Load offer IDs
    ids = _load_offer_ids(args.input, limit=args.limit, offset=args.offset)
    
    resume_mode = getattr(args, "resume", False)
    if resume_mode and args.output.is_file():
        try:
            existing_data = json.loads(args.output.read_text(encoding="utf-8"))
            if isinstance(existing_data, list):
                existing_ids = {int(item["cian_id"]) for item in existing_data if item.get("cian_id")}
                original_len = len(ids)
                ids = [i for i in ids if i not in existing_ids]
                log.info("Resume: skipped %d already parsed offers", original_len - len(ids))
        except Exception as e:
            log.warning("Failed to load existing output for resume: %s", e)
            
    if not ids:
        log.error("No offer IDs loaded from %s", args.input)
        return 1
    log.info("Loaded %d offer IDs for processing", len(ids))

    # Initialize components
    proxy_mgr = ProxyManager(args.proxies)
    cookie_mgr = CookieManager()
    client = CianOfferClient(
        proxy_manager=proxy_mgr,
        cookie_manager=cookie_mgr,
        max_retries=args.retries,
        retry_delay=args.delay,
        timeout=args.timeout,
    )

    # Process offers in parallel
    results: list[OfferData] = []
    errors: list[dict[str, str]] = []
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_id = {
            executor.submit(_process_offer, oid, client): oid
            for oid in ids
        }

        done_count = 0
        for future in as_completed(future_to_id):
            oid = future_to_id[future]
            done_count += 1

            try:
                result = future.result()
            except Exception as exc:
                result = {"offer_id": str(oid), "error": str(exc)}

            if isinstance(result, OfferData):
                results.append(result)
                log.info(
                    "[%d/%d] OK: %s — %s, %s",
                    done_count, len(ids), result.cian_id,
                    result.address.get("full", ""), result.price,
                )
            else:
                errors.append(result)
                log.warning(
                    "[%d/%d] FAIL: %s — %s",
                    done_count, len(ids),
                    result.get("offer_id", "?"),
                    result.get("error", "unknown"),
                )

    elapsed = time.monotonic() - started

    # Export results
    if results:
        _export_to_json(results, args.output, append=resume_mode)

    # Export errors
    if errors:
        err_path = args.output.with_name(
            args.output.stem + "_errors" + ".json"
        )
        with err_path.open("w", encoding="utf-8") as f:
            json.dump(errors, f, ensure_ascii=False, indent=2)
        log.info("Saved %d errors to %s", len(errors), err_path)

    log.info(
        "Done: %d OK, %d failed, %.1fs elapsed",
        len(results), len(errors), elapsed,
    )
    return 0


# -------------------------------------------------------------------
#  CLI
# -------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cian-offer-parser",
        description="Парсер страниц объявлений CIAN",
    )
    sub = p.add_subparsers(dest="command")

    # --- run command ---
    run = sub.add_parser("run", help="Запустить парсинг объявлений")
    run.add_argument(
        "-i", "--input",
        type=Path,
        required=True,
        help="Файл со списком ID объявлений (один ID на строку)",
    )
    run.add_argument(
        "-o", "--output",
        type=Path,
        default=_DIR.parent / "docs" / "parsed_offers.json",
        help="Путь для JSON-файла с результатами",
    )
    run.add_argument(
        "--proxies",
        type=Path,
        default=_DIR / "data" / "proxies.txt",
        help="Файл со списком прокси",
    )
    run.add_argument(
        "-w", "--workers",
        type=int,
        default=5,
        help="Количество параллельных воркеров (по умолчанию 5)",
    )
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Обработать только N объявлений",
    )
    run.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Пропустить первые N объявлений из входного файла",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Пропустить ID, которые уже спарсены в выходном JSON (и дописать новые)",
    )
    run.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Количество повторных попыток на объявление",
    )
    run.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Базовая задержка между повторами (сек)",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Таймаут HTTP-запроса (сек)",
    )
    run.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Подробный вывод (DEBUG)",
    )

    # --- test-local command ---
    test = sub.add_parser("test-local", help="Тест парсера на локальном HTML")
    test.add_argument(
        "html_file",
        type=Path,
        help="Путь к HTML-файлу",
    )
    test.add_argument(
        "-v", "--verbose",
        action="store_true",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    _setup_logging(getattr(args, "verbose", False))

    if args.command == "test-local":
        return _cmd_test_local(args.html_file)
    elif args.command == "run":
        return _cmd_run(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
