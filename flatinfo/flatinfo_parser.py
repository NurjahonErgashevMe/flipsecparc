import argparse
import concurrent.futures
import csv
import json
import os
import threading
import time
from itertools import product

import requests
from bs4 import BeautifulSoup

# =============================================================
# НАСТРОЙКИ — границы Москвы (общие для всех режимов)
# =============================================================
LAT_MIN, LAT_MAX = 55.5, 56.0   # границы Москвы по широте
LNG_MIN, LNG_MAX = 37.3, 37.9   # границы Москвы по долготе

# =============================================================
# НАСТРОЙКИ — режим сканирования сетки координат (--scan-grid, legacy)
# =============================================================
# Шаг сетки в градусах.
# ~0.0003° ≈ 30 метров — дома в Москве в среднем занимают ~50м
# Начни с 0.001 для теста, потом уменьши до 0.0003 для полного покрытия
STEP = 0.001

DELAY = 0.15                      # пауза между запросами к API (сек)
OUTPUT_FILE     = "houses.json"
OUTPUT_CSV      = "houses.csv"
CHECKPOINT_FILE = "checkpoint.json"

# =============================================================
# НАСТРОЙКИ — режим bbox-обхода через карту (--scan-bbox)
# =============================================================
MAP_POINTS_URL    = "https://flatinfo.ru/api/map-points-geo.php"
GET_DETAILS_URL   = "https://flatinfo.ru/leaflet/get_details.php"
BBOX_STEP_DEFAULT = 0.02          # ~1.5 км на клетку — обычно укладывается в лимит
BBOX_MAX_POINTS   = 1500          # если в клетке больше — делим на 4
BBOX_MIN_STEP     = 0.001         # глубина рекурсии — не меньше ~75 м
BBOX_CHECKPOINT_FILE = "bbox_checkpoint.json"
BBOX_DELAY        = 0.15

# =============================================================
# НАСТРОЙКИ — режим парсинга HTML страниц домов (--houses-information / --scan-hid)
# =============================================================
HOUSE_PAGE_URL    = "https://flatinfo.ru/h_info1.asp?hid={hid}"
DETAILS_FILE      = "houses_details.json"
DETAILS_CSV       = "houses_details.csv"
DETAILS_DELAY     = 0.4           # пауза между GET-запросами HTML (сек, 1 поток)
DETAILS_SAVE_EVERY = 50           # как часто сохранять промежуточный результат

# Лимиты по умолчанию для перебора hid
HID_MIN_DEFAULT   = 1
HID_MAX_DEFAULT   = 600000
HID_WORKERS_DEFAULT = 8

# =============================================================

# =============================================================
# HEADERS
# =============================================================
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
_SEC_CH_UA = '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"'

# Заголовки для XHR/fetch к /api/* и /leaflet/* — копия того, что шлёт браузер
HEADERS_API = {
    "User-Agent":         _UA,
    "Accept":             "*/*",
    "Accept-Language":    "ru,en;q=0.9,en-US;q=0.8",
    "Content-Type":       "application/json; charset=utf-8",
    "Origin":             "https://flatinfo.ru",
    "Referer":            "https://flatinfo.ru/map.asp?novostroyki=0&dealtype=prodaja",
    "sec-ch-ua":          _SEC_CH_UA,
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-origin",
}

# Заголовки для GET /h_info1.asp?hid=... — как обычная навигация в браузере
HEADERS_HTML = {
    "User-Agent":         _UA,
    "Accept":             ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                           "image/avif,image/webp,image/apng,*/*;q=0.8,"
                           "application/signed-exchange;v=b3;q=0.7"),
    "Accept-Language":    "ru,en;q=0.9,en-US;q=0.8",
    "Cache-Control":      "max-age=0",
    "Referer":            "https://flatinfo.ru/map.asp?novostroyki=0&dealtype=prodaja",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua":          _SEC_CH_UA,
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":     "document",
    "sec-fetch-mode":     "navigate",
    "sec-fetch-site":     "same-origin",
    "sec-fetch-user":     "?1",
}

# =============================================================
# Лейблы карточки дома → ключи в итоговом dict
# =============================================================
HOUSE_LABEL_FIELDS = {
    "Округ":                          "okrug",
    "Район":                          "rayon",
    "Нас. пункт":                     "city",
    "Почтовый индекс":                "postal_index",
    "Гео-координаты":                 "geo",
    "Типовая серия":                  "ser_name",
    "Год постройки":                  "year",
    "Общая площадь":                  "total_area",
    "Жилая площадь":                  "live_area",
    "Нежилая площадь":                "nonlive_area",
    "Перекрытия":                     "perekrytia",
    "Каркас":                         "karkas",
    "Стены":                          "steny",
    "Назначение":                     "naznachenie",
    "Тип дома":                       "house_type",
    "Категория":                      "category",
    "Квартир":                        "flats",
    "Нежилых помещений":              "nonlive_rooms",
    "Проживает":                      "people",
    "Этажей всего":                   "floors_total",
    "Подвальных этажей":              "floors_basement",
    "Высота потолков":                "ceiling_height",
    "Подъездов":                      "podezd",
    "Подключение газа в квартирах":   "gas",
    "Мусоропровод":                   "garbage_chute",
    "Управляющая компания":           "uk",
    "Состояние":                      "condition",
    "Средства на капремонт":          "funds_capremont",
    "Кадастровый номер дома":         "kadastr",
    "Код ФИАС":                       "fias",
    "Код адреса КЛАДР":               "kladr",
    "Код адреса UNOM":                "unom",
    "Расселение по реновации":        "renovation_stage",
    "Жилой комплекс":                 "jk_name",
    "Застройщик":                     "developer",
    "Лифты пассажирские":             "lifts_passenger",
    "Лифты грузовые":                 "lifts_cargo",
    "Лифты":                          "lifts",
}

# =============================================================
# ОБЩИЕ УТИЛИТЫ
# =============================================================

def normalize_text(s):
    if s is None:
        return ""
    return " ".join(s.replace("\xa0", " ").split()).strip()


def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_csv(rows, filepath):
    """utf-8-sig = UTF-8 с BOM — Excel на Windows открывает без кракозябр,
    разделитель ; — стандарт для русской локали."""
    if not rows:
        return
    # Собираем объединённый набор ключей по всем строкам — на случай разнобоя
    fields = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


# =============================================================
# РЕЖИМ 1. СКАНИРОВАНИЕ СЕТКОЙ — get_details.php
# =============================================================

def get_house_detail(session, lat, lng):
    """Стучимся в get_details.php — возвращает дом по любым координатам,
    даже если объявлений нет."""
    try:
        r = session.post(
            "https://flatinfo.ru/leaflet/get_details.php",
            json={"lat": lat, "lng": lng},
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        # Если дома по этим координатам нет — house_id будет пустым/None
        return data if data.get("house_id") else None
    except Exception as e:
        print(f"  [!] Ошибка {lat:.6f},{lng:.6f}: {e}")
        return None


def is_residential_from_grid(detail):
    """Эвристика жилого дома по ответу get_details.php.

    На сайте jil_type приходит как строка типа 'Жилой' / 'Нежилой' / ''.
    Стратегия:
      - если в jil_type/type явно сказано 'нежил' — значит нежилой;
      - если jil_type явно начинается с 'жил' — жилой;
      - дополнительный сигнал: type содержит 'многокварт';
      - если ничего нет — пропускаем дом (считаем НЕжилым), чтобы не тащить
        мусор. Это безопаснее, потому что у настоящих жилых домов
        jil_type почти всегда заполнен."""
    jt = str(detail.get("jil_type", "")).strip().lower()
    tp = str(detail.get("type", "")).strip().lower()

    if "нежил" in jt or "не жил" in jt or "нежил" in tp or "не жил" in tp:
        return False
    if jt.startswith("жил"):
        return True
    if "многокварт" in tp:
        return True
    if not jt and not tp:
        return False
    return True


def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        visited = set(tuple(x) for x in data.get("visited", []))
        houses  = data.get("houses", {})
        print(f"[v] Checkpoint: {len(houses)} домов, {len(visited)} точек уже обработано")
        return houses, visited
    except FileNotFoundError:
        return {}, set()


def save_checkpoint(houses, visited):
    save_json({
        "houses":  houses,
        "visited": [list(x) for x in visited],
    }, CHECKPOINT_FILE)


def cmd_scan_grid(args):
    houses, visited = load_checkpoint()

    # Генерируем все точки сетки
    lats = []
    lat = LAT_MIN
    while lat < LAT_MAX:
        lats.append(round(lat, 7))
        lat += STEP

    lngs = []
    lng = LNG_MIN
    while lng < LNG_MAX:
        lngs.append(round(lng, 7))
        lng += STEP

    grid = list(product(lats, lngs))
    total = len(grid)

    print(f"\nСетка: {len(lats)} x {len(lngs)} = {total:,} точек")
    print(f"Уже обработано: {len(visited):,}")
    print(f"Осталось: {total - len(visited):,}")
    if args.residential_only:
        print("Фильтр: только жилые дома (по jil_type/type)")
    print()

    session = requests.Session()
    session.headers.update(HEADERS_API)

    found_since_checkpoint = 0

    for idx, (lat, lng) in enumerate(grid, 1):
        key = (lat, lng)

        if key in visited:
            continue

        detail = get_house_detail(session, lat, lng)
        visited.add(key)

        if detail:
            if args.residential_only and not is_residential_from_grid(detail):
                pass
            else:
                house_id = str(detail["house_id"])

                # Не перезаписываем если уже есть — первая запись точнее
                if house_id not in houses:
                    houses[house_id] = {
                        "house_id":    house_id,
                        "city":        detail.get("city", ""),
                        "street":      detail.get("street", ""),
                        "house_num":   detail.get("house_num", ""),
                        "jk_name":     detail.get("jk_name", ""),
                        "jk_id":       detail.get("jk_id", ""),
                        "type":        detail.get("type", ""),
                        "jil_type":    detail.get("jil_type", ""),
                        "floor":       detail.get("floor", ""),
                        "year":        detail.get("year", ""),
                        "flats":       detail.get("flats", ""),
                        "podezd":      detail.get("podezd", ""),
                        "ser_name":    detail.get("ser_name", ""),
                        "ser_id":      detail.get("ser_id", ""),
                        "house_sales": detail.get("house_sales", "0"),
                        "house_rents": detail.get("house_rents", "0"),
                        "lat":         lat,
                        "lng":         lng,
                    }
                    found_since_checkpoint += 1
                    print(f"  [{idx:>8,}/{total:,}] +ДОМ #{house_id}: {detail.get('street','')} {detail.get('house_num','')}")

        # Checkpoint каждые 500 точек
        if idx % 500 == 0:
            save_checkpoint(houses, visited)
            pct = idx / total * 100
            print(f"\n  [v] {pct:.1f}% | Точек: {idx:,}/{total:,} | Домов: {len(houses):,} | +{found_since_checkpoint} новых\n")
            found_since_checkpoint = 0

        time.sleep(DELAY)

    # Финальное сохранение
    result = list(houses.values())
    save_json(result, OUTPUT_FILE)
    save_csv(result, OUTPUT_CSV)

    print(f"\n{'='*50}")
    print("  ГОТОВО")
    print(f"  Домов найдено:  {len(result):,}")
    print(f"  JSON: {OUTPUT_FILE}")
    print(f"  CSV:  {OUTPUT_CSV}")
    print('='*50)


# =============================================================
# РЕЖИМ 2. ПАРСИНГ HTML СТРАНИЦ ДОМОВ — h_info1.asp?hid=...
# =============================================================

def fetch_house_html(session, hid):
    url = HOUSE_PAGE_URL.format(hid=hid)
    r = session.get(url, timeout=20)
    r.raise_for_status()
    # На сервере иногда возвращается windows-1251; requests обычно угадывает
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def _clean_address(h1_text):
    """Убирает хвост 'Все о доме.' и 'Дом по адресу '."""
    s = normalize_text(h1_text)
    for tail in ("Все о доме.", "Все о доме"):
        if s.endswith(tail):
            s = s[: -len(tail)].strip().rstrip(".")
            break
    if s.startswith("Дом по адресу "):
        s = s[len("Дом по адресу "):]
    return normalize_text(s)


def _parse_metro(soup):
    """Извлекаем ближайшие станции метро/электрички/остановки автобуса.
    Возвращает список dict с name/duration/way/distance."""
    stations = []
    for h2 in soup.find_all("h2"):
        if "Ближайшие станции" not in h2.get_text():
            continue
        container = h2.find_parent("div", class_="fi-infobox")
        if not container:
            continue
        ul = container.find("ul", class_="underground")
        if not ul:
            continue
        for li in ul.find_all("li", class_="fi-list-item"):
            label = li.find("span", class_="fi-list-item__label")
            value = li.find("span", class_="fi-list-item__value")
            if not label or not value:
                continue
            name = normalize_text(label.get_text(" ", strip=True))

            time_node = value.find("span", class_="location__time")
            duration = ""
            way = ""
            if time_node:
                duration = normalize_text(time_node.get_text(" ", strip=True))
                use = time_node.find("use")
                if use is not None:
                    href = use.get("xlink:href") or use.get("href") or ""
                    if "walking" in href:
                        way = "пешком"
                    elif "bus" in href or "trolley" in href or "tram" in href:
                        way = "транспорт"

            full_value = normalize_text(value.get_text(" ", strip=True))
            distance = ""
            if duration and full_value.startswith(duration):
                distance = normalize_text(full_value[len(duration):])
            else:
                distance = full_value
                if duration:
                    distance = normalize_text(distance.replace(duration, "", 1))

            stations.append({
                "name":     name,
                "duration": duration,
                "way":      way,
                "distance": distance,
            })
        break
    return stations


def parse_house_html(html, hid):
    soup = BeautifulSoup(html, "html.parser")

    record = {"house_id": str(hid)}

    h1 = soup.find("h1", class_="page__title")
    record["address_full"] = _clean_address(h1.get_text(" ", strip=True)) if h1 else ""

    # Все label/value пары на странице (берём первое вхождение лейбла)
    pairs = {}
    for li in soup.find_all("li", class_="fi-list-item"):
        label_node = li.find("span", class_="fi-list-item__label")
        value_node = li.find("span", class_="fi-list-item__value")
        if not label_node or not value_node:
            continue
        # Не считаем элементы внутри блока метро (там label содержит metro-label)
        if label_node.find("span", class_="metro-label"):
            continue
        label_text = normalize_text(label_node.get_text(" ", strip=True))
        value_text = normalize_text(value_node.get_text(" ", strip=True))
        if not label_text:
            continue
        if label_text not in pairs:
            pairs[label_text] = value_text

    for label, key in HOUSE_LABEL_FIELDS.items():
        record[key] = pairs.get(label, "")

    # Если на странице есть отдельная ссылка на ЖК — добавим её
    if not record.get("jk_name"):
        a_jk = soup.find("a", href=lambda h: bool(h) and "jk" in h.lower())
        if a_jk and a_jk.get_text(strip=True):
            record["jk_name"] = normalize_text(a_jk.get_text(" ", strip=True))

    record["metro_stations"] = _parse_metro(soup)

    naznach = (record.get("naznachenie") or "").lower()
    htype   = (record.get("house_type") or "").lower()
    record["is_residential"] = ("жил" in naznach) or ("многокварт" in htype)

    # Сохраняем словарь всех "сырых" пар на случай новых полей в будущем
    record["_raw_pairs"] = pairs

    return record


def _flatten_for_csv(record):
    """Готовит запись к записи в CSV — без вложенных списков/словарей."""
    flat = {}
    for k, v in record.items():
        if k == "metro_stations":
            metro = v or []
            flat[k] = " | ".join(
                " ".join(part for part in [
                    m.get("name", ""),
                    f"({m.get('duration','')} {m.get('way','')})".strip(" ()"),
                    m.get("distance", ""),
                ] if part).strip()
                for m in metro
            )
        elif k == "_raw_pairs":
            continue
        elif isinstance(v, (list, dict)):
            flat[k] = json.dumps(v, ensure_ascii=False)
        else:
            flat[k] = v
    return flat


def save_details(details_dict):
    rows = list(details_dict.values())
    save_json(rows, DETAILS_FILE)
    save_csv([_flatten_for_csv(r) for r in rows], DETAILS_CSV)


def load_existing_details():
    if not os.path.exists(DETAILS_FILE):
        return {}
    try:
        with open(DETAILS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(r.get("house_id")): r for r in data if r.get("house_id")}
    except Exception:
        return {}


def cmd_houses_information(args):
    if not os.path.exists(OUTPUT_FILE):
        print(f"[!] Не найден {OUTPUT_FILE}. Сначала запустите сканирование сетки.")
        return

    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        houses = json.load(f)

    details = load_existing_details()
    workers = max(1, getattr(args, "workers", 1))
    print(f"[v] Загружено домов:        {len(houses):,}")
    print(f"[v] Уже спарсено ранее:     {len(details):,}")
    print(f"[v] Потоков:                {workers}")
    if args.residential_only:
        print("[v] Фильтр: только жилые дома (по полю 'Назначение' / 'Тип дома')")
    print()

    todo = []
    for h in houses:
        hid = str(h.get("house_id") or "").strip()
        if not hid or hid in details:
            continue
        todo.append((hid, h))
    print(f"[v] К обработке: {len(todo):,} домов\n")
    total = len(todo)

    def _worker(item):
        hid, h = item
        s = _thread_session(HEADERS_HTML)
        try:
            html = fetch_house_html(s, hid)
        except Exception as e:
            return ("error", hid, h, f"http: {e}")
        try:
            record = parse_house_html(html, hid)
        except Exception as e:
            return ("error", hid, h, f"parse: {e}")
        return ("ok", hid, h, record)

    processed = skipped = failed = 0
    last_save_at = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker, item) for item in todo]
        try:
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                status, hid, h, payload = fut.result()

                if status == "ok":
                    record = payload
                    if args.residential_only and not record.get("is_residential", False):
                        skipped += 1
                    else:
                        record.setdefault("lat",          h.get("lat", ""))
                        record.setdefault("lng",          h.get("lng", ""))
                        record.setdefault("street_api",   h.get("street", ""))
                        record.setdefault("house_num_api", h.get("house_num", ""))
                        details[hid] = record
                        processed += 1
                else:
                    failed += 1
                    if failed <= 20:
                        with _print_lock:
                            print(f"  [!] hid={hid}: {payload}")

                if i - last_save_at >= DETAILS_SAVE_EVERY * 4:
                    with _save_lock:
                        save_details(details)
                    last_save_at = i
                    extras = f"+{processed} карточек, {skipped} нежилых, {failed} ошибок"
                    with _print_lock:
                        print("  " + _format_progress(i, total, extras))
        except KeyboardInterrupt:
            print("\n[!] Прерывание — сохраняем то, что есть...")
            for f in futures:
                f.cancel()

    save_details(details)

    print(f"\n{'='*50}")
    print("  ГОТОВО (--houses-information)")
    print(f"  Спарсено в этом запуске:    {processed:,}")
    print(f"  Пропущено (не жилые):       {skipped:,}")
    print(f"  Ошибок:                     {failed:,}")
    print(f"  Всего в файле деталей:      {len(details):,}")
    print(f"  JSON: {DETAILS_FILE}")
    print(f"  CSV:  {DETAILS_CSV}")
    print('='*50)


# =============================================================
# РЕЖИМ 3. ОБХОД ЧЕРЕЗ КАРТУ — /api/map-points-geo.php (--scan-bbox)
# Это то же, что делает leaflet на сайте: один POST с боксом возвращает
# СПИСОК всех точек (домов) внутри. Намного эффективнее сетки координат.
# =============================================================

def fetch_map_points(session, sw_lat, sw_lng, ne_lat, ne_lng, json_query=None):
    """Возвращает список точек (домов) внутри bbox. Каждая точка — dict
    с ключами crd:[lat,lng], ids:[...], num:N."""
    payload = {
        "boundings": {
            "northEast": {"lat": ne_lat, "lng": ne_lng},
            "soutWest":  {"lat": sw_lat, "lng": sw_lng},
        },
        "jsonQuery": json_query or {},
        "visible": 0,
    }
    r = session.post(MAP_POINTS_URL, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("points") or []


def _collect_points_quadtree(session, sw_lat, sw_lng, ne_lat, ne_lng, json_query, out, depth=0):
    """Рекурсивный обход bbox: если в клетке слишком много точек, делим на 4.
    Кладёт все точки в словарь out по ключу (round(lat,6), round(lng,6))."""
    try:
        points = fetch_map_points(session, sw_lat, sw_lng, ne_lat, ne_lng, json_query)
    except Exception as e:
        print(f"  [!] bbox {sw_lat:.4f},{sw_lng:.4f}..{ne_lat:.4f},{ne_lng:.4f}: {e}")
        time.sleep(BBOX_DELAY * 2)
        return

    width = ne_lng - sw_lng
    height = ne_lat - sw_lat

    # Если слишком плотно — делим (на сервере может быть лимит на ответ)
    if len(points) >= BBOX_MAX_POINTS and min(width, height) > BBOX_MIN_STEP:
        mid_lat = (sw_lat + ne_lat) / 2
        mid_lng = (sw_lng + ne_lng) / 2
        for (a_lat, a_lng, b_lat, b_lng) in (
            (sw_lat, sw_lng, mid_lat, mid_lng),
            (sw_lat, mid_lng, mid_lat, ne_lng),
            (mid_lat, sw_lng, ne_lat, mid_lng),
            (mid_lat, mid_lng, ne_lat, ne_lng),
        ):
            time.sleep(BBOX_DELAY)
            _collect_points_quadtree(session, a_lat, a_lng, b_lat, b_lng,
                                     json_query, out, depth + 1)
        return

    added = 0
    for p in points:
        crd = p.get("crd")
        if not crd or len(crd) < 2:
            continue
        try:
            key = (round(float(crd[0]), 6), round(float(crd[1]), 6))
        except (TypeError, ValueError):
            continue
        if key not in out:
            out[key] = p
            added += 1
    if added:
        print(f"  bbox {sw_lat:.4f},{sw_lng:.4f}..{ne_lat:.4f},{ne_lng:.4f}: "
              f"+{added} (всего {len(out)})")


def _save_bbox_checkpoint(points_dict):
    save_json(
        {"points": [
            {"lat": k[0], "lng": k[1], "data": v} for k, v in points_dict.items()
        ]},
        BBOX_CHECKPOINT_FILE,
    )


def _load_bbox_checkpoint():
    if not os.path.exists(BBOX_CHECKPOINT_FILE):
        return {}
    try:
        with open(BBOX_CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {(p["lat"], p["lng"]): p.get("data", {}) for p in data.get("points", [])}
    except Exception:
        return {}


def cmd_scan_bbox(args):
    """Собрать дома через bbox-карту: 2 фазы.
    1) обход bbox → собираем уникальные координаты;
    2) для каждой координаты get_details.php → данные дома."""
    step = args.bbox_step

    bboxes = []
    lat = LAT_MIN
    while lat < LAT_MAX:
        lng = LNG_MIN
        while lng < LNG_MAX:
            bboxes.append((
                round(lat, 6), round(lng, 6),
                round(min(lat + step, LAT_MAX), 6),
                round(min(lng + step, LNG_MAX), 6),
            ))
            lng += step
        lat += step

    print(f"\n[Фаза 1] BBox-сетка: {len(bboxes)} клеток по {step}°")
    print(f"          (рекурсивно делим клетку на 4, если в ней >= {BBOX_MAX_POINTS} точек)")
    if args.residential_only:
        print("          Фильтр: только жилые (применяется во второй фазе)")
    print()

    session = requests.Session()
    session.headers.update(HEADERS_API)

    points_dict = _load_bbox_checkpoint()
    if points_dict:
        print(f"[v] Восстановлено из чекпойнта: {len(points_dict)} точек")

    for idx, (sw_lat, sw_lng, ne_lat, ne_lng) in enumerate(bboxes, 1):
        _collect_points_quadtree(session, sw_lat, sw_lng, ne_lat, ne_lng,
                                 json_query={}, out=points_dict)
        if idx % 20 == 0:
            _save_bbox_checkpoint(points_dict)
            print(f"  [v] {idx}/{len(bboxes)} клеток | точек собрано: {len(points_dict):,}")
        time.sleep(BBOX_DELAY)

    _save_bbox_checkpoint(points_dict)
    print(f"\n[Фаза 1] Готово. Уникальных точек на карте: {len(points_dict):,}")

    workers = max(1, getattr(args, "workers", 1))
    print(f"\n[Фаза 2] Запрашиваем get_details.php по точкам в {workers} потоков...")

    houses, _visited = load_checkpoint()
    print(f"[v] Уже было домов в чекпойнте: {len(houses)}")

    def _details_worker(point_key):
        lat, lng = point_key
        s = _thread_session(HEADERS_API)
        try:
            r = s.post(GET_DETAILS_URL, json={"lat": lat, "lng": lng}, timeout=15)
            r.raise_for_status()
            data = r.json()
            if not data.get("house_id"):
                return ("empty", lat, lng, None)
            return ("ok", lat, lng, data)
        except Exception as e:
            return ("error", lat, lng, str(e))

    total_pts = len(points_dict)
    point_keys = list(points_dict.keys())

    found = empty = skipped = errors = 0
    last_save_at = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_details_worker, k) for k in point_keys]
        try:
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                status, lat, lng, payload = fut.result()

                if status == "ok":
                    detail = payload
                    if args.residential_only and not is_residential_from_grid(detail):
                        skipped += 1
                    else:
                        hid = str(detail["house_id"])
                        if hid not in houses:
                            houses[hid] = {
                                "house_id":    hid,
                                "city":        detail.get("city", ""),
                                "street":      detail.get("street", ""),
                                "house_num":   detail.get("house_num", ""),
                                "jk_name":     detail.get("jk_name", ""),
                                "jk_id":       detail.get("jk_id", ""),
                                "type":        detail.get("type", ""),
                                "jil_type":    detail.get("jil_type", ""),
                                "floor":       detail.get("floor", ""),
                                "year":        detail.get("year", ""),
                                "flats":       detail.get("flats", ""),
                                "podezd":      detail.get("podezd", ""),
                                "ser_name":    detail.get("ser_name", ""),
                                "ser_id":      detail.get("ser_id", ""),
                                "house_sales": detail.get("house_sales", "0"),
                                "house_rents": detail.get("house_rents", "0"),
                                "lat":         lat,
                                "lng":         lng,
                            }
                            found += 1
                elif status == "empty":
                    empty += 1
                else:
                    errors += 1

                if i - last_save_at >= 500:
                    with _save_lock:
                        save_json(list(houses.values()), OUTPUT_FILE)
                        save_csv(list(houses.values()), OUTPUT_CSV)
                    last_save_at = i
                    extras = f"+{found} домов, {skipped} нежилых, {empty} пусто, {errors} ошибок"
                    with _print_lock:
                        print("  " + _format_progress(i, total_pts, extras))
        except KeyboardInterrupt:
            print("\n[!] Прерывание — сохраняем то, что есть...")
            for f in futures:
                f.cancel()

    result = list(houses.values())
    save_json(result, OUTPUT_FILE)
    save_csv(result, OUTPUT_CSV)

    print(f"\n{'='*50}")
    print("  ГОТОВО (--scan-bbox)")
    print(f"  Точек на карте:           {total_pts:,}")
    print(f"  Домов получено:           {len(result):,}")
    print(f"  Пропущено (не жилые):     {skipped:,}")
    print(f"  Пустых ответов:           {empty:,}")
    print(f"  Ошибок:                   {errors:,}")
    print(f"  JSON: {OUTPUT_FILE}")
    print(f"  CSV:  {OUTPUT_CSV}")
    print('='*50)


# =============================================================
# РЕЖИМ 4. ПРЯМОЙ ПЕРЕБОР HID — h_info1.asp?hid=N (--scan-hid)
# Самый полный, но самый долгий путь — сразу пишет полную карточку
# дома в houses_details.json/.csv. Можно запускать в N потоков.
# =============================================================

def is_valid_house_page(html):
    """Признак того, что вернулась реальная карточка дома, а не заглушка."""
    if not html:
        return False
    if "page__title" not in html:
        return False
    return "Дом по адресу" in html


_print_lock = threading.Lock()
_save_lock  = threading.Lock()
_thread_local = threading.local()


def _thread_session(headers):
    """Своя requests.Session() на каждый поток (Session не потокобезопасна)."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(headers)
        _thread_local.session = s
    return s


def _format_progress(done, total, extras=""):
    pct = (done / total * 100) if total else 0.0
    return f"[{pct:5.1f}%] {done:>7,}/{total:,}" + (f" | {extras}" if extras else "")


def _hid_worker(hid, session, residential_only):
    try:
        html = fetch_house_html(session, hid)
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return ("notfound", hid, None)
        return ("error", hid, str(e))
    except Exception as e:
        return ("error", hid, str(e))

    if not is_valid_house_page(html):
        return ("empty", hid, None)

    try:
        record = parse_house_html(html, hid)
    except Exception as e:
        return ("error", hid, f"parse: {e}")

    if residential_only and not record.get("is_residential", False):
        return ("nonres", hid, None)

    return ("ok", hid, record)


def cmd_scan_hid(args):
    hid_min = args.hid_min
    hid_max = args.hid_max
    workers = max(1, args.workers)

    details = load_existing_details()
    print(f"[v] Уже спарсено ранее: {len(details)} карточек")
    print(f"[v] Диапазон hid: {hid_min}..{hid_max}, потоков: {workers}")
    if args.residential_only:
        print("[v] Фильтр: только жилые дома (по 'Назначение'/'Тип дома')")
    print()

    todo = [hid for hid in range(hid_min, hid_max + 1) if str(hid) not in details]
    print(f"[v] К обработке: {len(todo):,} hid")

    session = requests.Session()
    session.headers.update(HEADERS_HTML)

    found = notfound = nonres = errors = 0
    processed_since_save = 0

    def submit(hid):
        return _hid_worker(hid, session, args.residential_only)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(submit, hid): hid for hid in todo}
        try:
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                hid = futures[fut]
                try:
                    status, _, payload = fut.result()
                except Exception as e:
                    status, payload = "error", str(e)

                if status == "ok":
                    found += 1
                    details[str(hid)] = payload
                    addr = (payload.get("address_full") or "")[:80]
                    with _print_lock:
                        print(f"  [{i:>7,}/{len(todo):,}] +#{hid}: {addr}")
                elif status == "notfound":
                    notfound += 1
                elif status == "empty":
                    notfound += 1
                elif status == "nonres":
                    nonres += 1
                else:
                    errors += 1
                    if errors <= 20:
                        with _print_lock:
                            print(f"  [!] hid={hid}: {payload}")

                processed_since_save += 1
                if processed_since_save >= DETAILS_SAVE_EVERY * 4:
                    with _save_lock:
                        save_details(details)
                    with _print_lock:
                        print(f"  [v] Сохранено: всего {len(details):,} карточек "
                              f"(найдено {found}, пусто {notfound}, нежилых {nonres}, ошибок {errors})")
                    processed_since_save = 0
        except KeyboardInterrupt:
            print("\n[!] Прерывание — сохраняем то, что есть...")
            for f in futures:
                f.cancel()

    save_details(details)
    print(f"\n{'='*50}")
    print("  ГОТОВО (--scan-hid)")
    print(f"  Найдено домов:            {found:,}")
    print(f"  Пустых/несуществующих:    {notfound:,}")
    print(f"  Пропущено (не жилые):     {nonres:,}")
    print(f"  Ошибок:                   {errors:,}")
    print(f"  Всего в файле:            {len(details):,}")
    print(f"  JSON: {DETAILS_FILE}")
    print(f"  CSV:  {DETAILS_CSV}")
    print(f"{'='*50}")


# =============================================================
# CLI
# =============================================================

def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="flatinfo_parser",
        description="Парсер flatinfo.ru. Несколько режимов сбора домов и парсинга карточек.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--scan-grid",
        action="store_true",
        help="LEGACY: обход равномерной сетки координат через get_details.php "
             "(медленно, многое пропускает). Пишет houses.json/houses.csv.",
    )
    mode.add_argument(
        "--scan-bbox",
        action="store_true",
        help="РЕКОМЕНДУЕТСЯ: обход карты bbox-ами (как сам сайт через "
             "/api/map-points-geo.php). Сначала собираются все точки внутри "
             "Москвы, потом по каждой делается get_details.php. "
             "Пишет houses.json/houses.csv.",
    )
    mode.add_argument(
        "--scan-hid",
        action="store_true",
        help="МАКСИМАЛЬНАЯ ПОЛНОТА: перебираем h_info1.asp?hid=N от --hid-min "
             "до --hid-max в N потоков. Сразу парсим HTML и пишем подробную "
             "карточку в houses_details.json/houses_details.csv.",
    )
    mode.add_argument(
        "--houses-information",
        action="store_true",
        help="Берёт house_id из houses.json и для каждого делает GET "
             "https://flatinfo.ru/h_info1.asp?hid={id}, парсит HTML и "
             "сохраняет подробные данные в houses_details.json/.csv. "
             "Поддерживает --workers.",
    )
    mode.add_argument(
        "--scan-all",
        action="store_true",
        help="ВСЁ В ОДНОЙ КОМАНДЕ: сначала --scan-bbox (собирает все точки "
             "Москвы и тащит данные домов), затем --houses-information (для "
             "каждого дома качает HTML страницу и парсит подробности). Обе "
             "фазы используют --workers.",
    )

    p.add_argument(
        "--residential-only",
        action="store_true",
        help="Сохранять только жилые дома. В --scan-grid/--scan-bbox — фильтр "
             "по jil_type/type из API. В --scan-hid/--houses-information — "
             "по полю 'Назначение'/'Тип дома' из HTML страницы дома.",
    )

    p.add_argument(
        "--bbox-step",
        type=float,
        default=BBOX_STEP_DEFAULT,
        help=f"(--scan-bbox) Размер клетки bbox-сетки в градусах. "
             f"По умолчанию {BBOX_STEP_DEFAULT} (~1.5 км).",
    )
    p.add_argument(
        "--hid-min",
        type=int,
        default=HID_MIN_DEFAULT,
        help=f"(--scan-hid) Нижняя граница перебора hid. По умолчанию {HID_MIN_DEFAULT}.",
    )
    p.add_argument(
        "--hid-max",
        type=int,
        default=HID_MAX_DEFAULT,
        help=f"(--scan-hid) Верхняя граница перебора hid. По умолчанию {HID_MAX_DEFAULT}.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=HID_WORKERS_DEFAULT,
        help=f"Количество параллельных потоков для --scan-bbox/--scan-hid/"
             f"--houses-information/--scan-all. По умолчанию {HID_WORKERS_DEFAULT}.",
    )
    return p


def cmd_scan_all(args):
    """Полный конвейер: bbox-обход → детальный HTML-парсинг.
    Идёт в две фазы, каждая с --workers."""
    print("\n" + "#" * 60)
    print("# ЭТАП 1/2: --scan-bbox  (карта → houses.json)")
    print("#" * 60)
    cmd_scan_bbox(args)

    print("\n" + "#" * 60)
    print("# ЭТАП 2/2: --houses-information  (HTML → houses_details.json)")
    print("#" * 60)
    cmd_houses_information(args)

    print("\n" + "=" * 60)
    print("  ВСЁ ГОТОВО (--scan-all)")
    print("  houses.json         — найденные дома (минимум полей из API)")
    print("  houses_details.json — полные карточки со всеми данными")
    print("=" * 60)


def main():
    args = build_arg_parser().parse_args()
    if args.scan_all:
        cmd_scan_all(args)
    elif args.houses_information:
        cmd_houses_information(args)
    elif args.scan_hid:
        cmd_scan_hid(args)
    elif args.scan_bbox:
        cmd_scan_bbox(args)
    else:
        # По умолчанию и при --scan-grid — старый режим сетки
        cmd_scan_grid(args)


if __name__ == "__main__":
    main()
