"""
Конвертация houses.json -> houses.xlsx.

По умолчанию — только жилые дома Москвы
(purpose == "Жилой дом" и settlement == "Москва").

Ровно 14 полей: Адрес, Год постройки, Перекрытия, Тип дома, Этаж/этажность,
Серия дома, Округ, Район, Лифты (пасс. и груз.), Высота потолков (см),
Управляющая компания, Жилой комплекс, Метро (пешком / трансп.), Застройщик.

Использование:
    python houses_to_excel.py
    python houses_to_excel.py --input houses.json --output houses.xlsx
    python houses_to_excel.py --all
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


HERE = Path(__file__).parent
DEFAULT_INPUT = HERE / "houses.json"
DEFAULT_OUTPUT = HERE / "houses.xlsx"


# Описание колонок: (resolver, заголовок, тип, ширина).
# resolver — функция house -> значение; либо имя ключа в словаре дома.
# Типы: str | int | float
def _get(key_path: str):
    """Создаёт резолвер по dot-пути, напр. 'metro.on_foot.name'."""
    parts = key_path.split(".")

    def _resolve(h: dict):
        cur = h
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
            if cur is None:
                return None
        return cur
    return _resolve


def _elevators_passenger(h: dict):
    """Пассажирские лифты: сначала normalized, потом raw_fields."""
    v = h.get("passenger_elevators")
    if v is not None:
        return v
    raw = h.get("raw_fields") or {}
    for key in ("passazhirskih_liftov_v_podezde", "liftov_v_podezde"):
        if key in raw:
            return _to_int(raw[key])
    return None


def _elevators_cargo(h: dict):
    """Грузовые/большие лифты: сначала normalized, потом raw_fields."""
    v = h.get("cargo_elevators")
    if v is not None:
        return v
    raw = h.get("raw_fields") or {}
    for key in ("bolshih_liftov_v_podezde", "gruzovyh_liftov_v_podezde"):
        if key in raw:
            return _to_int(raw[key])
    return None


def _to_int(s):
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return int(s)
    m = re.search(r"-?\d+", str(s).replace("\xa0", " ").replace(" ", ""))
    return int(m.group(0)) if m else None


def _lifts_both(h: dict) -> str | None:
    """Пасс. + груз. в одной строке: «пасс. 2, груз. 1»."""
    p = _elevators_passenger(h)
    c = _elevators_cargo(h)
    if p is None and c is None:
        return None
    parts: list[str] = []
    if p is not None:
        parts.append(f"пасс. {p}")
    if c is not None:
        parts.append(f"груз. {c}")
    return ", ".join(parts)


def _metro_walk_or_transport(h: dict) -> str | None:
    """Ходьба до метро и/или путь на транспорте в одной ячейке."""
    m = h.get("metro")
    if not isinstance(m, dict):
        return None
    bits: list[str] = []
    foot = m.get("on_foot")
    if isinstance(foot, dict) and foot.get("name"):
        t = foot.get("time_min")
        bits.append(
            f"пешком: {foot['name']}"
            + (f", {t} мин" if t is not None else "")
        )
    tr = m.get("on_transport")
    if isinstance(tr, dict) and tr.get("name"):
        s = tr.get("stops")
        bits.append(
            f"трансп.: {tr['name']}"
            + (f", {s} ост." if s is not None else "")
        )
    return "; ".join(bits) if bits else None


# Ровно 14 колонок, порядок = ТЗ
COLUMNS: list = [
    (_get("address"),                       "Адрес",                    "str",   48),
    (_get("built_year"),                    "Год постройки",            "int",   13),
    (_get("overlap_type"),                 "Перекрытия",               "str",   18),
    (_get("construction_type"),            "Тип дома",                 "str",   14),
    (_get("floors_count"),                 "Этаж / этажность",         "int",   12),
    (_get("series"),                      "Серия дома",                "str",   24),
    (_get("okrug"),                        "Округ",                    "str",   14),
    (_get("district"),                     "Район",                    "str",   22),
    (_lifts_both,                          "Лифты (пасс. и груз.)",   "str",   20),
    (_get("ceiling_height_cm"),            "Высота потолков, см",      "int",   16),
    (_get("management_company.name"),      "Управляющая компания",     "str",   32),
    (_get("housing_complex.name"),         "Жилой комплекс",          "str",   26),
    (_metro_walk_or_transport,             "Метро (пешком / трансп.)", "str",   40),
    (_get("developer"),                    "Застройщик",               "str",   28),
]


def is_residential_moscow(h: dict) -> bool:
    """Москва + Жилой дом."""
    if h.get("settlement") != "Москва":
        return False
    if h.get("purpose") != "Жилой дом":
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--all", action="store_true",
                        help="Не фильтровать: экспортировать ВСЕ дома из JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Читаю {args.input}...")
    with args.input.open("r", encoding="utf-8") as fp:
        houses = json.load(fp)
    print(f"Всего домов в JSON: {len(houses)}")

    if args.all:
        rows = houses
        print("Фильтр отключён (--all): экспортирую всё")
    else:
        rows = [h for h in houses if is_residential_moscow(h)]
        skipped = len(houses) - len(rows)
        print(f"Жилых домов Москвы: {len(rows)}  "
              f"(отброшено {skipped} нежилых/не-Москва)")

    # Стабильный порядок: по округу -> району -> адресу
    rows.sort(key=lambda h: (
        h.get("okrug") or "",
        h.get("district") or "",
        h.get("address") or "",
    ))

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Дома Москвы")
    ws.freeze_panes = "A2"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="305496")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="B0B0B0")
    header_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    center_align = Alignment(horizontal="center", vertical="center")
    default_align = Alignment(vertical="center")

    for idx, (_, _, _, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    header = []
    for _, title, _, _ in COLUMNS:
        cell = WriteOnlyCell(ws, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = header_border
        header.append(cell)
    ws.append(header)

    total = len(rows)
    for row_idx, house in enumerate(rows, start=1):
        row_cells = []
        for resolver, _, dtype, _ in COLUMNS:
            raw = resolver(house) if callable(resolver) else house.get(resolver)
            cell = WriteOnlyCell(ws, value=None)
            cell.alignment = default_align

            if raw is None or raw == "":
                cell.value = None
            elif dtype == "int":
                try:
                    cell.value = int(raw)
                    cell.number_format = "0"
                    cell.alignment = center_align
                except (TypeError, ValueError):
                    # fallback: вытащим первое число из строки
                    v = _to_int(raw)
                    if v is not None:
                        cell.value = v
                        cell.number_format = "0"
                        cell.alignment = center_align
                    else:
                        cell.value = str(raw)
            elif dtype == "float":
                try:
                    cell.value = float(raw)
                    cell.number_format = "0.00"
                    cell.alignment = center_align
                except (TypeError, ValueError):
                    cell.value = str(raw)
            else:
                cell.value = str(raw)
            row_cells.append(cell)
        ws.append(row_cells)
        if row_idx % 2000 == 0:
            print(f"  записано {row_idx}/{total}")

    last_col = get_column_letter(len(COLUMNS))
    ws.auto_filter.ref = f"A1:{last_col}{total + 1}"

    print(f"Сохраняю {args.output}...")
    wb.save(args.output)
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Готово! {total} строк, {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
