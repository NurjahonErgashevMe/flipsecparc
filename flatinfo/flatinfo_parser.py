import requests
import json
import time
import csv
from itertools import product

# =============================================================
# НАСТРОЙКИ
# =============================================================
LAT_MIN, LAT_MAX = 55.5, 56.0   # границы Москвы по широте
LNG_MIN, LNG_MAX = 37.3, 37.9   # границы Москвы по долготе

# Шаг сетки в градусах.
# ~0.0003° ≈ 30 метров — дома в Москве в среднем занимают ~50м
# Начни с 0.001 для теста, потом уменьши до 0.0003 для полного покрытия
STEP = 0.001

DELAY = 0.15             # пауза между запросами (сек)
OUTPUT_FILE     = "houses.json"
OUTPUT_CSV      = "houses.csv"
CHECKPOINT_FILE = "checkpoint.json"

HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Origin": "https://flatinfo.ru",
    "Referer": "https://flatinfo.ru/",
}

# =============================================================
# ЗАПРОСЫ
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


# =============================================================
# СОХРАНЕНИЕ
# =============================================================

def save_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_csv(data, filepath):
    """utf-8-sig = UTF-8 с BOM — Excel на Windows открывает без кракозябр,
    разделитель ; — стандарт для русской локали."""
    if not data:
        return
    fields = list(data[0].keys())
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(data)


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


# =============================================================
# ОСНОВНАЯ ЛОГИКА
# =============================================================

def main():
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
    print(f"Осталось: {total - len(visited):,}\n")

    session = requests.Session()
    session.headers.update(HEADERS)

    found_since_checkpoint = 0

    for idx, (lat, lng) in enumerate(grid, 1):
        key = (lat, lng)

        if key in visited:
            continue

        detail = get_house_detail(session, lat, lng)
        visited.add(key)

        if detail:
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
    print(f"  ГОТОВО")
    print(f"  Домов найдено:  {len(result):,}")
    print(f"  JSON: {OUTPUT_FILE}")
    print(f"  CSV:  {OUTPUT_CSV}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()