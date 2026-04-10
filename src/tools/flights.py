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

MOCKS_DIR = Path(__file__).parent.parent.parent / "data" / "mocks"

# Таблица: русское название → ключ файла (латиница)
_CITY_MAP = {
    "тюмень": "tyumen",
    "барселона": "barcelona",
    "стамбул": "istanbul",
    "бали": "bali",
}

# Города, для которых есть mock-данные (используется в сообщениях пользователю)
SUPPORTED_CITIES = ["Тюмень", "Барселона", "Стамбул", "Бали"]


def _city_key(name: str) -> str:
    """Нормализует название города в ключ файла-фикстуры."""
    lower = name.lower().replace(" ", "_")
    return _CITY_MAP.get(lower, lower)


class FlightSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class FlightSearchResult:
    def __init__(self, flights: list[Flight]):
        self.flights = flights


def _load_fixture(destination: str) -> list[dict]:
    key = _city_key(destination)
    path = MOCKS_DIR / f"flights_{key}.json"
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
        raise FlightSearchError("NO_RESULTS", f"No flights found for {destination}")

    flights = []
    for item in raw:
        price = item.get("price", 0) * passengers
        if max_price and price > max_price:
            continue
        # Подставляем дату пользователя, сохраняя только время из фикстуры
        dep_time = item.get("departure", "T08:00:00").split("T")[-1]
        arr_time = item.get("arrival", "T10:00:00").split("T")[-1]
        flights.append(Flight(
            id=item["id"],
            origin=item.get("origin", origin),
            destination=item.get("destination", destination),
            departure=f"{departure_date}T{dep_time}",
            arrival=f"{return_date}T{arr_time}",
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
