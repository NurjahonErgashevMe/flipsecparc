#!/usr/bin/env python3
"""
Экспорт result.json (массив объектов домов flatinfo) в Excel .xlsx.
Требуется: pip install openpyxl (уже в flatinfo/requirements.txt).

Флаг --only-target оставляет только дома с нужными city_id и без запрещённых street_id
(тот же отбор, что в houses_parser.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter
except ImportError:
    print("Нужен пакет openpyxl: pip install openpyxl", file=sys.stderr)
    raise SystemExit(1)

COLUMNS: list[str] = [
    "house_id",
    "city",
    "city_id",
    "street",
    "street_id",
    "jk_id",
    "jk_name",
    "ser_id",
    "ser_name",
    "subser_id",
    "subser_name",
    "house_num",
    "year",
    "flats",
    "podezd",
    "type",
    "type_id",
    "floor",
    "levels",
    "lift_p",
    "lift_g",
    "jil_type",
    "house_sales",
    "house_rents",
    "lat",
    "lng",
    "garbage",
]

_DIR = Path(__file__).resolve().parent

ALLOWED_CITY_IDS = {"1", "12552", "12565"}
DISALLOWED_STREET_IDS = {"3745"}


def matches_target(item: dict[str, Any]) -> bool:
    return str(item.get("city_id")) in ALLOWED_CITY_IDS and str(
        item.get("street_id")
    ) not in DISALLOWED_STREET_IDS


WIDTHS: dict[str, float] = {
    "house_id": 10,
    "city": 18,
    "city_id": 8,
    "street": 36,
    "street_id": 10,
    "jk_id": 8,
    "jk_name": 24,
    "ser_id": 8,
    "ser_name": 28,
    "subser_id": 10,
    "subser_name": 24,
    "house_num": 12,
    "year": 8,
    "flats": 8,
    "podezd": 8,
    "type": 16,
    "type_id": 8,
    "floor": 14,
    "levels": 8,
    "lift_p": 8,
    "lift_g": 8,
    "jil_type": 12,
    "house_sales": 18,
    "house_rents": 18,
    "lat": 12,
    "lng": 12,
    "garbage": 10,
}


def load_items(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        return list(data["items"])
    raise ValueError("Ожидается JSON-массив или объект с ключом «items»")


def cell_value(v: Any) -> Any:
    if v is None:
        return ""
    return v


def main() -> None:
    p = argparse.ArgumentParser(description="flatinfo result.json → Excel (.xlsx)")
    p.add_argument(
        "-i",
        "--input",
        type=Path,
        default=_DIR / "result.json",
        help="Входной JSON (по умолчанию result.json рядом со скриптом)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Выходной .xlsx (по умолчанию: имя входа с расширением .xlsx)",
    )
    p.add_argument(
        "--only-target",
        action="store_true",
        help=(
            "Только дома: city_id из {1, 12552, 12565} и street_id не из запрещённых "
            "(как в houses_parser)"
        ),
    )
    args = p.parse_args()
    in_path: Path = args.input
    if not in_path.is_file():
        print(f"Файл не найден: {in_path}", file=sys.stderr)
        raise SystemExit(2)
    out_path: Path = args.output or in_path.with_suffix(".xlsx")

    items = load_items(in_path)
    total = len(items)
    if args.only_target:
        items = [it for it in items if matches_target(it)]
        print(f"Всего в JSON: {total}, после --only-target: {len(items)}")

    wb = Workbook()
    ws = wb.active
    ws.title = "houses"

    header_font = Font(bold=True)
    top = Alignment(vertical="top")
    wrap = Alignment(wrap_text=True, vertical="top")
    header_align = Alignment(wrap_text=True, vertical="center")

    for col_idx, name in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=col_idx, value=name)
        c.font = header_font
        c.alignment = header_align

    ser_col = COLUMNS.index("ser_name") + 1
    subser_col = COLUMNS.index("subser_name") + 1

    for row_idx, row in enumerate(items, start=2):
        for col_idx, key in enumerate(COLUMNS, start=1):
            val = cell_value(row.get(key))
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            if col_idx in (ser_col, subser_col):
                cell.alignment = wrap
            else:
                cell.alignment = top

    last_row = len(items) + 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{last_row}"

    for i, name in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 14)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"Записано строк: {len(items)}, файл: {out_path.resolve()}")


if __name__ == "__main__":
    main()
