"""
SearchAgent — параллельный поиск рейсов и жилья через MCP Tool Layer.
Реализует retry, failover и degraded mode согласно спецификации.
LLM-вызовов: 0 (полностью детерминированный).
"""
import asyncio
import time
from typing import Optional

from config import cfg
from middleware import logger
from models.schemas import (
    Accommodation, Flight, SearchResults, SessionState, TripProfile,
)
from tools.flights import FlightSearchError, FlightSearchResult, search_flights
from tools.hotels import HotelSearchError, HotelSearchResult, search_hotels

if cfg.USE_REAL_AIRBNB_MCP:
    from tools.rentals_mcp import RentalSearchError, RentalSearchResult, search_rentals
else:
    from tools.rentals import RentalSearchError, RentalSearchResult, search_rentals


async def run(session: SessionState) -> SearchResults:
    """
    Запускает параллельный поиск. Обновляет session.search_results.
    Возвращает SearchResults (может быть degraded).
    """
    profile = session.trip_profile
    t0 = time.time()

    # Параллельный запуск рейсов и жилья
    flights_task = asyncio.create_task(_fetch_flights(profile, session.session_id))
    hotels_task = asyncio.create_task(_fetch_hotels(profile, session.session_id))

    flights_result, hotels_result = await asyncio.gather(
        flights_task, hotels_task, return_exceptions=True
    )

    # --- Рейсы ---
    flights: list[Flight] = []
    if isinstance(flights_result, Exception):
        logger.log_error(session.session_id, "SearchAgent", type(flights_result).__name__, str(flights_result))
        raise RuntimeError(f"Flights search failed: {flights_result}")
    else:
        flights = flights_result.flights

        # Если рейсов нет — пробуем ±3 дня
        if not flights:
            flights = await _search_flexible(profile, session.session_id)

    # --- Жильё ---
    accommodations: list[Accommodation] = []
    accommodation_source = "mock_booking"
    degraded = False

    if isinstance(hotels_result, Exception):
        logger.log_failover(session.session_id, "mock_booking", "mock_airbnb", str(hotels_result))
        # Failover на Airbnb
        rentals_result = await _fetch_rentals(profile, session.session_id)
        if isinstance(rentals_result, Exception):
            logger.log_failover(session.session_id, "mock_airbnb", "none", str(rentals_result))
            degraded = True
            accommodation_source = "none"
        else:
            accommodations = rentals_result.rentals
            accommodation_source = "mock_airbnb"
    else:
        accommodations = hotels_result.hotels

    # Добавляем risk_flags для коротких стыковок
    for f in flights:
        if f.stops > 0 and f.connection_time_min and f.connection_time_min < 50:
            if "short_connection" not in session.risk_flags:
                session.risk_flags.append("short_connection")

    result = SearchResults(
        flights=flights,
        accommodations=accommodations,
        flights_source="mock_flights",
        accommodation_source=accommodation_source,
        accommodation_degraded=degraded,
    )
    session.search_results = result

    duration_ms = int((time.time() - t0) * 1000)
    logger.log_agent_call(
        session.session_id, "SearchAgent", "SEARCH", duration_ms,
        "degraded" if degraded else "success",
    )
    return result


async def _fetch_flights(profile: TripProfile, session_id: str) -> FlightSearchResult:
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            search_flights(
                origin=profile.origin or "",
                destination=profile.destination or "",
                departure_date=str(profile.departure_date or ""),
                return_date=str(profile.return_date or ""),
                passengers=profile.travelers or 1,
                currency=profile.currency.value,
            ),
            timeout=cfg.TOOL_TIMEOUT_SEC,
        )
        logger.log_tool_call(session_id, "search_flights", len(result.flights),
                             int((time.time() - t0) * 1000), "success")
        return result
    except (asyncio.TimeoutError, FlightSearchError) as e:
        logger.log_tool_call(session_id, "search_flights", 0,
                             int((time.time() - t0) * 1000), "error")
        # Один retry
        try:
            result = await asyncio.wait_for(
                search_flights(
                    origin=profile.origin or "",
                    destination=profile.destination or "",
                    departure_date=str(profile.departure_date or ""),
                    return_date=str(profile.return_date or ""),
                    passengers=profile.travelers or 1,
                    currency=profile.currency.value,
                ),
                timeout=cfg.TOOL_TIMEOUT_SEC,
            )
            logger.log_tool_call(session_id, "search_flights", len(result.flights),
                                 int((time.time() - t0) * 1000), "success_retry")
            return result
        except Exception as e2:
            raise RuntimeError(str(e2)) from e2


async def _fetch_hotels(profile: TripProfile, session_id: str) -> HotelSearchResult:
    t0 = time.time()
    nights = profile.nights() or 1
    try:
        result = await asyncio.wait_for(
            search_hotels(
                city=profile.destination or "",
                checkin=str(profile.departure_date or ""),
                checkout=str(profile.return_date or ""),
                guests=profile.travelers or 1,
                nights=nights,
                currency=profile.currency.value,
            ),
            timeout=cfg.TOOL_TIMEOUT_SEC,
        )
        logger.log_tool_call(session_id, "search_hotels", len(result.hotels),
                             int((time.time() - t0) * 1000), "success")
        return result
    except (asyncio.TimeoutError, HotelSearchError) as e:
        logger.log_tool_call(session_id, "search_hotels", 0,
                             int((time.time() - t0) * 1000), "error")
        # Один retry
        try:
            result = await asyncio.wait_for(
                search_hotels(
                    city=profile.destination or "",
                    checkin=str(profile.departure_date or ""),
                    checkout=str(profile.return_date or ""),
                    guests=profile.travelers or 1,
                    nights=nights,
                    currency=profile.currency.value,
                ),
                timeout=cfg.TOOL_TIMEOUT_SEC,
            )
            logger.log_tool_call(session_id, "search_hotels", len(result.hotels),
                                 int((time.time() - t0) * 1000), "success_retry")
            return result
        except Exception as e2:
            raise HotelSearchError("SERVICE_UNAVAILABLE", str(e2)) from e2


async def _fetch_rentals(profile: TripProfile, session_id: str) -> RentalSearchResult | Exception:
    t0 = time.time()
    nights = profile.nights() or 1
    timeout = cfg.MCP_AIRBNB_TIMEOUT_SEC if cfg.USE_REAL_AIRBNB_MCP else cfg.TOOL_TIMEOUT_SEC
    try:
        result = await asyncio.wait_for(
            search_rentals(
                city=profile.destination or "",
                checkin=str(profile.departure_date or ""),
                checkout=str(profile.return_date or ""),
                guests=profile.travelers or 1,
                nights=nights,
                currency=profile.currency.value,
            ),
            timeout=timeout,
        )
        logger.log_tool_call(session_id, "search_rentals", len(result.rentals),
                             int((time.time() - t0) * 1000), "success")
        return result
    except Exception as e:
        logger.log_tool_call(session_id, "search_rentals", 0,
                             int((time.time() - t0) * 1000), "error")
        return e


async def _search_flexible(profile: TripProfile, session_id: str) -> list[Flight]:
    """Пробуем ±3 дня если нет рейсов на точные даты."""
    from datetime import timedelta
    dep = profile.departure_date
    if not dep:
        return []
    for delta in [1, -1, 2, -2, 3, -3]:
        new_dep = dep + timedelta(days=delta)
        new_ret = (profile.return_date + timedelta(days=delta)) if profile.return_date else None
        try:
            result = await asyncio.wait_for(
                search_flights(
                    origin=profile.origin or "",
                    destination=profile.destination or "",
                    departure_date=str(new_dep),
                    return_date=str(new_ret) if new_ret else "",
                    passengers=profile.travelers or 1,
                    currency=profile.currency.value,
                ),
                timeout=cfg.TOOL_TIMEOUT_SEC,
            )
            if result.flights:
                # Помечаем что даты изменены
                for f in result.flights:
                    f.departure = f.departure.replace(str(dep), str(new_dep))
                logger.log_tool_call(session_id, "search_flights_flexible",
                                     len(result.flights), 0, f"success_delta{delta:+d}")
                return result.flights
        except Exception:
            continue
    return []
