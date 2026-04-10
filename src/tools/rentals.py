"""
MCP Tool: search_rentals (Airbnb-like)
Используется как failover когда search_hotels недоступен.
"""
import json
from pathlib import Path

from config import cfg
from models.schemas import Accommodation, Currency

MOCKS_DIR = Path(__file__).parent.parent.parent / "data" / "mocks"

_CITY_MAP = {
    "тюмень": "tyumen",
    "барселона": "barcelona",
    "стамбул": "istanbul",
    "бали": "bali",
}


def _city_key(name: str) -> str:
    lower = name.lower().replace(" ", "_")
    return _CITY_MAP.get(lower, lower)


class RentalSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class RentalSearchResult:
    def __init__(self, rentals: list[Accommodation]):
        self.rentals = rentals


def _load_fixture(city: str) -> list[dict]:
    key = _city_key(city)
    path = MOCKS_DIR / f"rentals_{key}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


async def search_rentals(
    city: str,
    checkin: str,
    checkout: str,
    guests: int,
    nights: int,
    max_total_price: float | None = None,
    currency: str = "EUR",
) -> RentalSearchResult:
    """
    MCP-совместимый интерфейс поиска аренды.
    """
    if cfg.MOCK_AIRBNB_FAIL:
        raise RentalSearchError("SERVICE_UNAVAILABLE", "Airbnb MCP server is unavailable")

    raw = _load_fixture(city)
    if not raw:
        raise RentalSearchError("NO_RESULTS", f"No rentals found in {city}")

    rentals = []
    for item in raw:
        ppn = item.get("price_per_night", 0)
        total = round(ppn * nights, 2)
        if max_total_price and total > max_total_price:
            continue
        rentals.append(Accommodation(
            id=item["id"],
            name=item["name"],
            type=item.get("type", "apartment"),
            city=city,
            price_per_night=ppn,
            total_price=total,
            currency=Currency(currency),
            rating=item.get("rating", 4.0),
            amenities=item.get("amenities", []),
            source="mock_airbnb",
        ))

    rentals = sorted(rentals, key=lambda r: (-r.rating, r.total_price))
    rentals = rentals[: cfg.MAX_SEARCH_RESULTS]

    if not rentals:
        raise RentalSearchError("NO_RESULTS", "No rentals within budget")

    return RentalSearchResult(rentals=rentals)
