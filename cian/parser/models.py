from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


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
class DeactivatedOffer:
    id: int
    title: str
    prices: OfferPrices
    exposition: str
    status: str
    date_start: str
    date_end: str
    preview_photo: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> DeactivatedOffer | None:
        if raw.get("status") != "deactivated":
            return None
        date_end = raw.get("dateEnd")
        if not date_end:
            return None
        prices_raw = raw.get("prices") or {}
        return cls(
            id=int(raw["id"]),
            title=str(raw.get("title", "")),
            prices=OfferPrices.from_dict(prices_raw),
            exposition=str(raw.get("exposition", "")),
            status="deactivated",
            date_start=str(raw.get("dateStart", "")),
            date_end=str(date_end),
            preview_photo=str(raw.get("previewPhoto", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prices": self.prices.to_dict(),
            "exposition": self.exposition,
            "status": self.status,
            "dateStart": self.date_start,
            "dateEnd": self.date_end,
            "previewPhoto": self.preview_photo,
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
