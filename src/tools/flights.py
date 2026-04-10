"""
MCP Tool: search_flights
PoC-реализация: читает JSON-фикстуры из data/mocks/.
Контракт совместим с MCP — замена на реальный MCP-сервер не требует изменений в SearchAgent.
"""
import asyncio
import json
import os
from pathlib import Path

from config import cfg
from models.schemas import Currency, Flight

MOCKS_DIR = Path(__file__).parent.parent / "data" / "mocks"


class FlightSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class FlightSearchResult:
    def __init__(self, flights: list[Flight]):
        self.flights = flights


def _load_fixture(destination: str) -> list[dict]:
    key = destination.lower().replace(" ", "_")
    for fname in [f"flights_{key}.json"]:
        path = MOCKS_DIR / fname
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return []


async def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    passengers: int,
    max_price: float | None = None,
    currency: str = "EUR",
) -> FlightSearchResult:
    """
    MCP-совместимый интерфейс поиска авиабилетов.
    В PoC возвращает данные из JSON-фикстур.
    """
    # Симуляция задержки (демо-флаг)
    if cfg.MOCK_FLIGHTS_DELAY_SEC > 0:
        await asyncio.sleep(cfg.MOCK_FLIGHTS_DELAY_SEC)

    raw = _load_fixture(destination)
    if not raw:
        # Попробуем по origin
        raw = _load_fixture(f"{origin}_{destination}")

    if not raw:
        raise FlightSearchError("NO_RESULTS", f"No flights found for {destination}")

    flights = []
    for item in raw:
        price = item.get("price", 0) * passengers
        if max_price and price > max_price:
            continue
        flights.append(Flight(
            id=item["id"],
            origin=item.get("origin", origin),
            destination=item.get("destination", destination),
            departure=item.get("departure", f"{departure_date}T08:00:00"),
            arrival=item.get("arrival", f"{departure_date}T10:00:00"),
            price=price,
            currency=Currency(currency),
            airline=item.get("airline", "Unknown"),
            stops=item.get("stops", 0),
            connection_time_min=item.get("connection_time_min"),
            source="mock_flights",
        ))

    flights = flights[: cfg.MAX_SEARCH_RESULTS]

    if not flights:
        raise FlightSearchError("NO_RESULTS", "No flights within budget")

    return FlightSearchResult(flights=flights)
