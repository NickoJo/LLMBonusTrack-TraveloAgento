"""
MCP Tool: search_rentals через @openbnb/mcp-server-airbnb
Запускает MCP-сервер как subprocess (npx), вызывает инструмент airbnb_search.

Требования:
  - Node.js + npx в PATH
  - pip install mcp>=1.0
  - Флаг: USE_REAL_AIRBNB_MCP=true

Airbnb меняет структуру ответа без предупреждений — парсинг устойчив к отсутствию полей.
"""
import json
import re

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import cfg
from models.schemas import Accommodation, Currency


class RentalSearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class RentalSearchResult:
    def __init__(self, rentals: list[Accommodation]):
        self.rentals = rentals


def _parse_price(raw) -> float:
    """Извлекает число из цены любого формата: 120, '$120', '1 200 ₽', '1,200.50'."""
    if raw is None:
        return 0.0
    s = str(raw)
    # Убираем всё кроме цифр, точки и запятой
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return 0.0
    # Если есть и точка и запятая — запятая тысячный разделитель
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s:
        # Может быть десятичный разделитель (европейский формат)
        parts = s.split(",")
        if len(parts) == 2 and len(parts[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_price_per_night(item: dict) -> float:
    """Пробует несколько мест где Airbnb прячет цену за ночь."""
    # Вариант 1: structuredContent.primaryLine
    sc = item.get("structuredContent", {})
    primary = sc.get("primaryLine", {})
    price = _parse_price(primary.get("price"))
    if price > 0:
        return price

    # Вариант 2: price.amount
    price_obj = item.get("price", {})
    if isinstance(price_obj, dict):
        price = _parse_price(price_obj.get("amount") or price_obj.get("rate"))
        if price > 0:
            return price

    # Вариант 3: priceString (строка вида "$120 / ночь")
    price = _parse_price(item.get("priceString") or item.get("price"))
    return price


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
    Вызывает @openbnb/mcp-server-airbnb через MCP stdio.
    Возвращает RentalSearchResult совместимый с mock-версией.
    """
    server = StdioServerParameters(
        command="npx",
        args=["-y", "@openbnb/mcp-server-airbnb", "--ignore-robots-txt"],
        env=None,
    )

    try:
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool(
                    "airbnb_search",
                    arguments={
                        "location": city,
                        "checkin": checkin,
                        "checkout": checkout,
                        "adults": guests,
                        "currency": currency,
                    },
                )
    except Exception as e:
        raise RentalSearchError("MCP_ERROR", f"Airbnb MCP server error: {e}") from e

    # Парсим ответ — MCP возвращает список TextContent
    if not result.content:
        raise RentalSearchError("EMPTY_RESPONSE", "Airbnb MCP returned empty response")

    try:
        raw_text = result.content[0].text
        raw = json.loads(raw_text)
    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        raise RentalSearchError("PARSE_ERROR", f"Cannot parse Airbnb response: {e}") from e

    # Airbnb кладёт результаты под разными ключами в зависимости от версии сервера
    listings = (
        raw.get("searchResults")
        or raw.get("listings")
        or raw.get("results")
        or (raw if isinstance(raw, list) else [])
    )

    if not isinstance(listings, list):
        raise RentalSearchError("NO_RESULTS", f"No Airbnb listings found in {city}")

    rentals = []
    for item in listings:
        ppn = _extract_price_per_night(item)
        total = round(ppn * nights, 2)

        if max_total_price and total > max_total_price:
            continue

        rating_raw = item.get("avgRating") or item.get("rating") or 4.0
        try:
            rating = max(1.0, min(5.0, float(rating_raw)))
        except (TypeError, ValueError):
            rating = 4.0

        rentals.append(Accommodation(
            id=str(item.get("id", f"airbnb_{len(rentals)}")),
            name=item.get("name") or item.get("title") or "Airbnb listing",
            type=item.get("roomType", "apartment"),
            city=city,
            price_per_night=ppn,
            total_price=total,
            currency=Currency(currency),
            rating=rating,
            amenities=item.get("amenities", []),
            source="airbnb_mcp",
        ))

    if not rentals:
        raise RentalSearchError("NO_RESULTS", f"No Airbnb listings found in {city}")

    return RentalSearchResult(
        rentals=sorted(rentals, key=lambda r: (-r.rating, r.total_price))[: cfg.MAX_SEARCH_RESULTS]
    )
