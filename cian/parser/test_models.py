from __future__ import annotations

import unittest

from .models import RoomCount, parse_history_features, parse_title


class ParseTitleTests(unittest.TestCase):
    def test_full_title_dot_separator(self) -> None:
        parsed = parse_title("73,4 м² · 3-комн. · 13/15 этаж")
        self.assertEqual(parsed.total_area_sqm, 73.4)
        self.assertEqual(parsed.rooms, 3)
        self.assertEqual(parsed.floor_current, 13)
        self.assertEqual(parsed.floor_total, 15)

    def test_comma_separator(self) -> None:
        parsed = parse_title("59,3 м², 3-комн., 8/9 этаж")
        self.assertEqual(parsed.total_area_sqm, 59.3)
        self.assertEqual(parsed.rooms, 3)
        self.assertEqual(parsed.floor_current, 8)
        self.assertEqual(parsed.floor_total, 9)

    def test_integer_area(self) -> None:
        parsed = parse_title("45 м², 2-комн., 3/9 этаж")
        self.assertEqual(parsed.total_area_sqm, 45.0)
        self.assertEqual(parsed.rooms, 2)
        self.assertEqual(parsed.floor_current, 3)
        self.assertEqual(parsed.floor_total, 9)

    def test_empty_title(self) -> None:
        parsed = parse_title("")
        self.assertIsNone(parsed.total_area_sqm)
        self.assertIsNone(parsed.rooms)
        self.assertIsNone(parsed.floor_current)
        self.assertIsNone(parsed.floor_total)

    def test_garbage_title(self) -> None:
        parsed = parse_title("неизвестный формат")
        self.assertIsNone(parsed.total_area_sqm)
        self.assertIsNone(parsed.rooms)


class RoomCountTests(unittest.TestCase):
    def test_from_api(self) -> None:
        rc = RoomCount.from_api({"offersCount": 32, "roomsCount": "two"})
        self.assertEqual(rc.offers_count, 32)
        self.assertEqual(rc.rooms_count, "two")
        self.assertEqual(rc.to_dict(), {"offersCount": 32, "roomsCount": "two"})


class ParseHistoryFeaturesTests(unittest.TestCase):
    def test_mapping_and_types(self) -> None:
        features = [
            {"title": "Тип дома", "value": "Монолитный"},
            {"title": "Год постройки", "value": "1981"},
            {"title": "Жилая площадь", "value": "50 м²"},
            {"title": "Кухня", "value": "8 м²"},
            {"title": "Ремонт", "value": "Без ремонта"},
        ]
        parsed = parse_history_features(features)
        self.assertEqual(parsed["building_type"], "Монолитный")
        self.assertEqual(parsed["build_year"], 1981)
        self.assertEqual(parsed["living_area_sqm"], 50.0)
        self.assertEqual(parsed["kitchen_area_sqm"], 8.0)
        self.assertEqual(parsed["renovation"], "Без ремонта")

    def test_floor_parsing(self) -> None:
        features = [{"title": "Этаж", "value": "5 из 9"}]
        parsed = parse_history_features(features)
        self.assertEqual(parsed["floor"], "5 из 9")
        self.assertEqual(parsed["floor_current"], 5)
        self.assertEqual(parsed["floor_total"], 9)

    def test_empty(self) -> None:
        self.assertEqual(parse_history_features([]), {})
        self.assertEqual(parse_history_features(None), {})


if __name__ == "__main__":
    unittest.main()
