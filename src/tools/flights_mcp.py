"""
MCP Tool: search_flights через maratsarbasov/flights-mcp (Aviasales API)
Запускает MCP-сервер через uvx, выполняет двухшаговый поиск:
  1. search_flights  → search_id
  2. get_flight_options(search_id) → список рейсов

Требования:
  - uv / uvx в PATH: https://docs.astral.sh/uv/getting-started/installation/
  - AVIASALES_API_TOKEN и AVIASALES_MARKER в .env (partner.aviasales.ru)
  - Флаг: USE_REAL_AVIASALES_MCP=true

Документация API: https://github.com/maratsarbasov/flights-mcp
"""
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import cfg
from models.schemas import Currency, Flight

# ── IATA-коды ──────────────────────────────────────────────────────────────
# city.lower() → IATA код ближайшего аэропорта / авиаузла
_IATA: dict[str, str] = {
    # Россия
    "москва": "MOW",
    "санкт-петербург": "LED",
    "спб": "LED",
    "новосибирск": "OVB",
    "екатеринбург": "SVX",
    "тюмень": "TJM",
    "казань": "KZN",
    "сочи": "AER",
    "краснодар": "KRR",
    "уфа": "UFA",
    "самара": "KUF",
    "ростов-на-дону": "ROV",
    "нижний новгород": "GOJ",
    "владивосток": "VVO",
    "иркутск": "IKT",
    "красноярск": "KJA",
    "омск": "OMS",
    "пермь": "PEE",
    "воронеж": "VOZ",
    "волгоград": "VOG",
    # Европа
    "барселона": "BCN",
    "мадрид": "MAD",
    "париж": "PAR",
    "лондон": "LON",
    "рим": "ROM",
    "милан": "MIL",
    "берлин": "BER",
    "амстердам": "AMS",
    "прага": "PRG",
    "вена": "VIE",
    "варшава": "WAW",
    "будапешт": "BUD",
    "афины": "ATH",
    "лиссабон": "LIS",
    "брюссель": "BRU",
    "цюрих": "ZRH",
    "стокгольм": "STO",
    "хельсинки": "HEL",
    "копенгаген": "CPH",
    "осло": "OSL",
    # Турция и ближний восток
    "стамбул": "IST",
    "анкара": "ANK",
    "анталья": "AYT",
    "дубай": "DXB",
    "абу-даби": "AUH",
    "доха": "DOH",
    "тель-авив": "TLV",
    "каир": "CAI",
    # Азия
    "бали": "DPS",
    "бангкок": "BKK",
    "сингапур": "SIN",
    "токио": "TYO",
    "пекин": "BJS",
    "шанхай": "SHA",
    "гонконг": "HKG",
    "сеул": "SEL",
    "куала-лумпур": "KUL",
    "дели": "DEL",
    "мумбаи": "BOM",
    "пхукет": "HKT",
    "колумбо": "CMB",
    # Америка
    "нью-йорк": "NYC",
    "лос-анджелес": "LAX",
    "майами": "MIA",
    "чикаго": "CHI",
    "торонто": "YTO",
    "монреаль": "YMQ",
    "мехико": "MEX",
    "сан-паулу": "SAO",
    "буэнос-айрес": "BUE",
    "богота": "BOG",
    # Африка и Океания
    "сидней": "SYD",
    "мельбурн": "MEL",
    "окленд": "AKL",
    "найроби": "NBO",
    "йоханнесбург": "JNB",
    "касабланка": "CAS",
}


def to_iata(city: str) -> str:
    """Возвращает IATA-код города. Если неизвестен — возвращает city как есть
    (Aviasales может принять название города напрямую)."""
    return _IATA.get(city.lower().strip(), city)


class FlightSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class FlightSearchResult:
    def __init__(self, flights: list[Flight]):
        self.flights = flights


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
    Вызывает flights-mcp (Aviasales) через MCP stdio.
    Двухшаговый поиск: search_flights → get_flight_options.
    """
    origin_iata = to_iata(origin)
    dest_iata = to_iata(destination)
    trip_type = "round_trip" if return_date else "one_way"

    server = StdioServerParameters(
        command="uvx",
        args=["flights-mcp"],
        env={
            "FLIGHTS_AVIASALES_API_TOKEN": cfg.AVIASALES_API_TOKEN,
            "FLIGHTS_AVIASALES_MARKER": cfg.AVIASALES_MARKER,
            "FLIGHTS_TRANSPORT": "stdio",
        },
    )

    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Шаг 1: запускаем поиск, получаем search_id
                search_resp = await session.call_tool(
                    "search_flights",
                    arguments={
                        "origin": origin_iata,
                        "destination": dest_iata,
                        "departure_date": departure_date,
                        "return_date": return_date or None,
                        "adults": passengers,
                        "trip_type": trip_type,
                    },
                )
                search_data = json.loads(search_resp.content[0].text)
                search_id = search_data.get("search_id")
                if not search_id:
                    raise FlightSearchError("NO_SEARCH_ID", "Aviasales did not return search_id")

                # Шаг 2: получаем список рейсов
                options_resp = await session.call_tool(
                    "get_flight_options",
                    arguments={
                        "search_id": search_id,
                        "sort_by": "price",
                    },
                )
                options_data = json.loads(options_resp.content[0].text)

    except FlightSearchError:
        raise
    except Exception as e:
        raise FlightSearchError("MCP_ERROR", f"Aviasales MCP server error: {e}") from e

    options = (
        options_data.get("flight_options")
        or options_data.get("results")
        or (options_data if isinstance(options_data, list) else [])
    )

    if not options:
        raise FlightSearchError("NO_RESULTS", f"No flights found for {origin} → {destination}")

    flights: list[Flight] = []
    for item in options:
        price_raw = item.get("price", 0)
        try:
            price = float(price_raw) * passengers
        except (TypeError, ValueError):
            price = 0.0

        if max_price and price > max_price:
            continue

        airlines = item.get("airlines", [])
        airline = airlines[0] if airlines else item.get("airline", "Unknown")

        flights.append(Flight(
            id=str(item.get("option_id", f"av_{len(flights)}")),
            origin=origin_iata,
            destination=dest_iata,
            departure=item.get("departure_time", f"{departure_date}T08:00:00"),
            arrival=item.get("arrival_time", f"{return_date or departure_date}T10:00:00"),
            price=price,
            currency=Currency(currency),
            airline=airline if isinstance(airline, str) else str(airline),
            stops=item.get("stops", 0),
            connection_time_min=item.get("duration"),
            source="aviasales_mcp",
        ))

    flights = flights[: cfg.MAX_SEARCH_RESULTS]

    if not flights:
        raise FlightSearchError("NO_RESULTS", "No flights within budget")

    return FlightSearchResult(flights=sorted(flights, key=lambda f: f.price))
