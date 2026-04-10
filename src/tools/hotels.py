"""
MCP Tool: search_hotels (Booking.com-like)
PoC-реализация: читает JSON-фикстуры.
При MOCK_BOOKING_FAIL=true симулирует недоступность MCP-сервера.
"""
import json
from pathlib import Path

from config import cfg
from models.schemas import Accommodation, Currency

MOCKS_DIR = Path(__file__).parent.parent / "data" / "mocks"


class HotelSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class HotelSearchResult:
    def __init__(self, hotels: list[Accommodation]):
        self.hotels = hotels


def _load_fixture(city: str) -> list[dict]:
    key = city.lower().replace(" ", "_")
    path = MOCKS_DIR / f"hotels_{key}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


async def search_hotels(
    city: str,
    checkin: str,
    checkout: str,
    guests: int,
    nights: int,
    max_price_per_night: float | None = None,
    currency: str = "EUR",
) -> HotelSearchResult:
    """
    MCP-совместимый интерфейс поиска отелей.
    """
    if cfg.MOCK_BOOKING_FAIL:
        raise HotelSearchError("SERVICE_UNAVAILABLE", "Booking MCP server is unavailable")

    raw = _load_fixture(city)
    if not raw:
        raise HotelSearchError("NO_RESULTS", f"No hotels found in {city}")

    hotels = []
    for item in raw:
        ppn = item.get("price_per_night", 0)
        if max_price_per_night and ppn > max_price_per_night:
            continue
        hotels.append(Accommodation(
            id=item["id"],
            name=item["name"],
            type=item.get("type", "hotel"),
            city=city,
            price_per_night=ppn,
            total_price=round(ppn * nights, 2),
            currency=Currency(currency),
            rating=item.get("rating", 3.5),
            amenities=item.get("amenities", []),
            source="mock_booking",
        ))

    hotels = sorted(hotels, key=lambda h: (-h.rating, h.total_price))
    hotels = hotels[: cfg.MAX_SEARCH_RESULTS]

    if not hotels:
        raise HotelSearchError("NO_RESULTS", "No hotels within budget")

    return HotelSearchResult(hotels=hotels)
