from __future__ import annotations

import re

from .models import InputHouse

_HOUSE_PREFIX = re.compile(r"^д\.?\s*", re.IGNORECASE)


def normalize_house_number(house_num: str) -> str:
    """д.13 -> 13"""
    return _HOUSE_PREFIX.sub("", house_num.strip())


def build_raw_address(house: InputHouse) -> str:
    """Адрес как в исходном JSON: «Москва, улица … д.13»."""
    return f"{house.city}, {house.street} {house.house_num}".strip()


def build_yandex_query(house: InputHouse) -> str:
    """Запрос для Yandex Suggest (как в curl-примере)."""
    return f"Россия, {house.city}, {house.street} {house.house_num}"


def build_geocode_cached_request(house: InputHouse) -> str:
    """Запрос geocode-cached: «Москва, улица …, 13»."""
    number = normalize_house_number(house.house_num)
    return f"{house.city}, {house.street}, {number}"
