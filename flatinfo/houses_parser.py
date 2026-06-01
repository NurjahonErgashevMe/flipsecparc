import json

INPUT_FILE = "result.json"


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

        moscow_city_id = "1"
        disallowed_streets = {"3745"}

        filtered = list(
            filter(
                lambda item: str(item.get("city_id", "")).strip() == moscow_city_id
                and item.get("street_id") not in disallowed_streets,
                data,
            )
        )
        print(f"Всего домов: {len(data)}")
        print(f"Домов после фильтрации : {len(filtered)}")


if __name__ == "__main__":
    main()
