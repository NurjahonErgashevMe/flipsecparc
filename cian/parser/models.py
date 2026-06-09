from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

_AREA_RE = re.compile(r"([\d,]+)\s*м²")
_ROOMS_RE = re.compile(r"(\d+)-комн")
_FLOOR_RE = re.compile(r"(\d+)/(\d+)\s*этаж")

HISTORY_FEATURE_MAP: dict[str, str] = {
    "Тип дома": "building_type",
    "Год постройки": "build_year",
    "Жилая площадь": "living_area_sqm",
    "Кухня": "kitchen_area_sqm",
    "Общая площадь": "total_area_sqm",
    "Ремонт": "renovation",
    "Балкон": "balcony",
    "Санузел": "bathroom",
    "Этаж": "floor",
}

HISTORY_FEATURE_FLOAT_KEYS = frozenset({
    "living_area_sqm",
    "kitchen_area_sqm",
    "total_area_sqm",
})


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace("\u00a0", " ").replace(",", ".").strip()
    m = re.search(r"[\d.]+", text)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    m = re.search(r"\d+", text)
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


@dataclass(frozen=True)
class InputHouse:
    house_id: int
    city: str
    city_id: str
    street: str
    street_id: str
    house_num: str
    year: str
    flats: str
    lat: str
    lng: str
    jil_type: str = ""
    type_name: str = field(default="", metadata={"json_key": "type"})
    levels: str = ""
    ser_name: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InputHouse:
        return cls(
            house_id=int(data["house_id"]),
            city=str(data.get("city", "")),
            city_id=str(data.get("city_id", "")),
            street=str(data.get("street", "")),
            street_id=str(data.get("street_id", "")),
            house_num=str(data.get("house_num", "")),
            year=str(data.get("year", "")),
            flats=str(data.get("flats", "")),
            lat=str(data.get("lat", "")),
            lng=str(data.get("lng", "")),
            jil_type=str(data.get("jil_type", "")),
            type_name=str(data.get("type", "")),
            levels=str(data.get("levels", "")),
            ser_name=str(data.get("ser_name", "")),
        )

    def is_moscow(self) -> bool:
        return self.city_id == "1" or self.city.strip() == "Москва"

    def source_snapshot(self) -> dict[str, Any]:
        return {
            "house_id": self.house_id,
            "city": self.city,
            "street": self.street,
            "house_num": self.house_num,
            "year": self.year,
            "flats": self.flats,
            "lat": self.lat,
            "lng": self.lng,
            "jil_type": self.jil_type,
            "type": self.type_name,
            "levels": self.levels,
            "ser_name": self.ser_name,
        }


@dataclass(frozen=True)
class TitleParsed:
    total_area_sqm: float | None = None
    rooms: int | None = None
    floor_current: int | None = None
    floor_total: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_area_sqm": self.total_area_sqm,
            "rooms": self.rooms,
            "floor_current": self.floor_current,
            "floor_total": self.floor_total,
        }


def parse_title(title: str) -> TitleParsed:
    if not title:
        return TitleParsed()

    total_area_sqm: float | None = None
    rooms: int | None = None
    floor_current: int | None = None
    floor_total: int | None = None

    area_m = _AREA_RE.search(title)
    if area_m:
        total_area_sqm = _to_float(area_m.group(1))

    rooms_m = _ROOMS_RE.search(title)
    if rooms_m:
        rooms = int(rooms_m.group(1))

    floor_m = _FLOOR_RE.search(title)
    if floor_m:
        floor_current = int(floor_m.group(1))
        floor_total = int(floor_m.group(2))

    return TitleParsed(
        total_area_sqm=total_area_sqm,
        rooms=rooms,
        floor_current=floor_current,
        floor_total=floor_total,
    )


def parse_history_features(features: list[dict[str, Any]] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not features:
        return result

    for feat in features:
        if not isinstance(feat, dict):
            continue
        title = str(feat.get("title", "") or "")
        value = feat.get("value", "")
        key = HISTORY_FEATURE_MAP.get(title)
        if not key:
            continue

        if key == "build_year":
            parsed = _to_int(value)
            if parsed is not None:
                result[key] = parsed
        elif key in HISTORY_FEATURE_FLOAT_KEYS:
            parsed = _to_float(value)
            if parsed is not None:
                result[key] = parsed
        elif key == "floor":
            text = str(value).strip()
            if text:
                result[key] = text
                m = re.match(r"(\d+)\s*из\s*(\d+)", text)
                if m:
                    result["floor_current"] = int(m.group(1))
                    result["floor_total"] = int(m.group(2))
        else:
            text = str(value).strip()
            if text:
                result[key] = text

    return result


@dataclass(frozen=True)
class OfferPrices:
    price: str
    price_sqm: str
    price_diff: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OfferPrices:
        return cls(
            price=str(data.get("price", "")),
            price_sqm=str(data.get("priceSqm", "")),
            price_diff=str(data.get("priceDiff", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "price": self.price,
            "priceSqm": self.price_sqm,
            "priceDiff": self.price_diff,
        }


@dataclass(frozen=True)
class OfferFeature:
    title: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "value": self.value}


@dataclass(frozen=True)
class OfferDetails:
    address: str
    images: tuple[str, ...]
    features: tuple[OfferFeature, ...]
    features_parsed: dict[str, Any]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> OfferDetails:
        features_raw = raw.get("features") or []
        features: list[OfferFeature] = []
        for item in features_raw:
            if not isinstance(item, dict):
                continue
            features.append(
                OfferFeature(
                    title=str(item.get("title", "")),
                    value=str(item.get("value", "")),
                )
            )
        images = tuple(str(u) for u in (raw.get("images") or []) if u)
        features_dicts = [f.to_dict() for f in features]
        return cls(
            address=str(raw.get("address", "")),
            images=images,
            features=tuple(features),
            features_parsed=parse_history_features(features_dicts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "images": list(self.images),
            "features": [f.to_dict() for f in self.features],
            "features_parsed": self.features_parsed,
        }


@dataclass(frozen=True)
class DeactivatedOffer:
    id: int
    title: str
    prices: OfferPrices
    exposition: str
    status: str
    date_start: str
    date_end: str
    preview_photo: str
    link: str = ""
    title_parsed: TitleParsed = field(default_factory=TitleParsed)
    details: OfferDetails | None = None
    details_error: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> DeactivatedOffer | None:
        if raw.get("status") != "deactivated":
            return None
        date_end = raw.get("dateEnd")
        if not date_end:
            return None
        title = str(raw.get("title", ""))
        prices_raw = raw.get("prices") or {}
        return cls(
            id=int(raw["id"]),
            title=title,
            prices=OfferPrices.from_dict(prices_raw),
            exposition=str(raw.get("exposition", "")),
            status="deactivated",
            date_start=str(raw.get("dateStart", "")),
            date_end=str(date_end),
            preview_photo=str(raw.get("previewPhoto", "")),
            link=str(raw.get("link", "")),
            title_parsed=parse_title(title),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "prices": self.prices.to_dict(),
            "exposition": self.exposition,
            "status": self.status,
            "dateStart": self.date_start,
            "dateEnd": self.date_end,
            "previewPhoto": self.preview_photo,
            "title_parsed": self.title_parsed.to_dict(),
        }
        if self.link:
            out["link"] = self.link
        if self.details is not None:
            out["details"] = self.details.to_dict()
        else:
            out["details"] = None
        if self.details_error:
            out["details_error"] = self.details_error
        return out


@dataclass(frozen=True)
class RoomCount:
    offers_count: int
    rooms_count: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> RoomCount:
        return cls(
            offers_count=int(raw.get("offersCount") or 0),
            rooms_count=str(raw.get("roomsCount", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "offersCount": self.offers_count,
            "roomsCount": self.rooms_count,
        }


@dataclass(frozen=True)
class CianHouseGeo:
    cian_house_id: int
    lat: float
    lng: float
    address: str
    region_id: int | None
    country_id: int | None
    street_id: int | None
    location_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedHouse:
    source: dict[str, Any]
    yandex_formatted_address: str
    geocode_text: str
    geocode_kind: str
    cian: CianHouseGeo
    offers: list[DeactivatedOffer] = field(default_factory=list)
    offers_total_count: int = 0
    room_counts: list[RoomCount] = field(default_factory=list)
    parsed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "yandex_formatted_address": self.yandex_formatted_address,
            "geocode": {
                "text": self.geocode_text,
                "kind": self.geocode_kind,
            },
            "cian": self.cian.to_dict(),
            "offers_total_count": self.offers_total_count,
            "roomCounts": [rc.to_dict() for rc in self.room_counts],
            "deactivated_offers": [o.to_dict() for o in self.offers],
            "parsed_at": self.parsed_at,
        }


@dataclass
class FailedHouse:
    source: dict[str, Any]
    stage: str
    error: str
    parsed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
