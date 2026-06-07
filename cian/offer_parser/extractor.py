"""
CIAN Offer Page Extractor.

Parses HTML of an individual CIAN offer page and extracts structured
data into the requested JSON schema.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
#  Data model
# -------------------------------------------------------------------
@dataclass
class OfferData:
    cian_id: str = ""
    price: int = 0
    price_per_m2: int = 0
    title: str = ""
    description: str = ""
    address: dict[str, str] = field(default_factory=lambda: {
        "full": "",
        "district": "",
        "metro_station": "",
        "okrug": ""
    })
    area: float = 0.0
    rooms: int = 0
    housing_type: str = ""
    building_type: str | None = None
    floor_info: dict[str, int] = field(default_factory=lambda: {
        "current": 0,
        "all": 0
    })
    construction_year: int = 0
    renovation: str = ""
    metro_walk_time: int = 0
    total_views: int = 0
    unique_views: int = 0
    is_active: bool = False
    has_avans_deposit: bool = False
    price_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------------------------------------------------------
#  Helpers
# -------------------------------------------------------------------
def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _extract_number(text: str) -> str:
    text = text.replace("\xa0", " ").replace(" ", "")
    m = re.search(r"[\d]+[.,]?\d*", text)
    if m:
        return m.group(0).replace(",", ".")
    return ""


def _extract_int(text: str) -> int:
    val = _extract_number(text)
    if not val:
        return 0
    try:
        return int(float(val))
    except ValueError:
        return 0


# -------------------------------------------------------------------
#  Parsers
# -------------------------------------------------------------------
def _parse_summary(soup: BeautifulSoup) -> dict[str, str]:
    """Parse data-name='OfferSummaryInfoItem'."""
    result: dict[str, str] = {}
    items = soup.find_all(attrs={"data-name": "OfferSummaryInfoItem"})
    for item in items:
        # they are usually <p> or just text nodes separated. 
        # CIAN puts them as label then value.
        spans = [s for s in item.find_all(["span", "p"]) if s.get_text(strip=True)]
        if len(spans) >= 2:
            label = _clean(spans[0].get_text())
            value = _clean(spans[1].get_text())
            result[label] = value
        else:
            # fallback for text inside div
            text = item.get_text(separator="\n")
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
    return result


def _parse_factoids(soup: BeautifulSoup) -> dict[str, str]:
    """Parse data-name='ObjectFactoidsItem'."""
    result: dict[str, str] = {}
    items = soup.find_all(attrs={"data-name": "ObjectFactoidsItem"})
    for item in items:
        spans = [s for s in item.find_all("span") if s.get_text(strip=True)]
        if len(spans) >= 2:
            label = _clean(spans[0].get_text())
            value = _clean(spans[1].get_text())
            result[label] = value
    return result


def _parse_price_history(soup: BeautifulSoup) -> list[dict[str, Any]]:
    history = []
    el = soup.find(attrs={"data-name": "PriceHistory"})
    if not el:
        return history
    
    # Each row is a tr
    rows = el.find_all("tr")
    for row in rows:
        tds = row.find_all("td")
        if len(tds) >= 2:
            date_text = _clean(tds[0].get_text())
            price_text = _clean(tds[1].get_text())
            price_val = _extract_int(price_text)
            if date_text and price_val > 0:
                history.append({"date": date_text, "price": price_val})
                
    return history


def _parse_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    crumbs = []
    el = soup.find(attrs={"data-name": "Breadcrumbs"})
    if el:
        links = el.find_all("a")
        for link in links:
            crumbs.append(_clean(link.get_text()))
    return crumbs


# -------------------------------------------------------------------
#  Main extractor
# -------------------------------------------------------------------
def extract_offer(html: str, offer_id: int | str = "") -> OfferData:
    soup = BeautifulSoup(html, "html.parser")
    data = OfferData(cian_id=str(offer_id))

    # --- Active / Inactive ---
    data.is_active = True
    desc_el = soup.find(attrs={"data-name": "Description"})
    if desc_el and "снято с публикации" in desc_el.get_text().lower():
        data.is_active = False
    
    # We don't parse description or views per user request
    data.description = ""
    data.total_views = 0
    data.unique_views = 0

    # --- Price ---
    price_el = soup.find(attrs={"data-name": "PriceInfo"})
    if price_el:
        amount_el = price_el.find(attrs={"data-testid": "price-amount"})
        text = amount_el.get_text() if amount_el else price_el.get_text()
        data.price = _extract_int(text)

    facts = soup.find_all(attrs={"data-name": "OfferFactItem"})
    for f in facts:
        text = f.get_text(separator=" ").lower()
        if "цена за метр" in text:
            data.price_per_m2 = _extract_int(text)

    # --- Title & Rooms ---
    title_el = soup.find(attrs={"data-name": "OfferTitleNew"})
    if title_el:
        h1 = title_el.find("h1")
        if h1:
            data.title = _clean(h1.get_text())
            m = re.search(r"(\d+)-комн", data.title)
            if m:
                data.rooms = int(m.group(1))
            elif "студи" in data.title.lower():
                data.rooms = 1 # or 0 based on preference, let's say 1 for studio or leave 0

    # --- Address ---
    addr_parts = [_clean(i.get_text()) for i in soup.find_all(attrs={"data-name": "AddressItem"})]
    if addr_parts:
        data.address["full"] = ", ".join(addr_parts)
        for part in addr_parts:
            part_lower = part.lower()
            if "р-н" in part_lower or "район" in part_lower:
                data.address["district"] = part
            elif "ао" in part_lower and len(part.split()) == 1:
                data.address["okrug"] = part

    # Check breadcrumbs for okrug/district if not found
    crumbs = _parse_breadcrumbs(soup)
    for c in crumbs:
        cl = c.lower()
        if "ао" in cl and len(c) <= 6:
            data.address["okrug"] = c
        if "район" in cl:
            data.address["district"] = c

    # --- Metro ---
    metros = soup.find_all(attrs={"data-name": "UndergroundItem"})
    if metros:
        first_metro = metros[0]
        link = first_metro.find("a")
        if link:
            data.address["metro_station"] = _clean(link.get_text())
        
        # Look for walk time
        time_span = first_metro.find("span", class_=lambda c: c and "underground_time" in c)
        if time_span:
            time_val = _extract_int(time_span.get_text())
            if time_val > 0:
                # Check icon for walk vs transport
                icon_div = time_span.find("div")
                if icon_div:
                    svg_html = str(icon_div)
                    # Walk usually contains "4.475c" or "M8.867"
                    if "4.475c" in svg_html or "M8.867" in svg_html:
                        data.metro_walk_time = time_val

    # --- Summary & Factoids ---
    summary = _parse_summary(soup)
    factoids = _parse_factoids(soup)
    combined = {**summary, **factoids}

    area_str = combined.get("Общая площадь", "")
    if area_str:
        num = _extract_number(area_str)
        if num:
            data.area = float(num)

    data.housing_type = combined.get("Тип жилья", "")
    data.building_type = combined.get("Тип дома", None)
    data.renovation = combined.get("Ремонт", "")
    
    year_str = combined.get("Год постройки", "")
    if year_str:
        data.construction_year = _extract_int(year_str)

    floor_str = combined.get("Этаж", "")
    if floor_str:
        m = re.match(r"(\d+)\s*из\s*(\d+)", floor_str)
        if m:
            data.floor_info["current"] = int(m.group(1))
            data.floor_info["all"] = int(m.group(2))
        else:
            data.floor_info["current"] = _extract_int(floor_str)

    # --- Avans / Deposit ---
    # check WarningLabel
    data.has_avans_deposit = False
    warn_labels = soup.find_all(attrs={"data-name": "WarningLabel"})
    for w in warn_labels:
        wt = w.get_text().lower()
        if "аванс" in wt or "задаток" in wt or "обеспечительный платеж" in wt:
            data.has_avans_deposit = True

    # --- Price History ---
    data.price_history = _parse_price_history(soup)

    return data
