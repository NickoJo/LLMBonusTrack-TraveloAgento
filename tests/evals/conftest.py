"""
Общие фикстуры и вспомогательные функции для eval-тестов TraveloAgento.
"""
import asyncio
import os
from datetime import date, timedelta
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# STANDARD_REQUEST — эталонный запрос для eval-тестов
# ---------------------------------------------------------------------------

STANDARD_REQUEST = (
    "Хочу слетать в Тюмень на 5 дней в мае, нас двое, бюджет 150000 рублей"
)

BARCELONA_REQUEST = (
    "Хочу поехать в Барселону на неделю в июле, вылет из Москвы, двое взрослых, бюджет 3000 евро"
)

ISTANBUL_REQUEST = (
    "Планирую поездку в Стамбул на 4 дня с 10 по 14 мая, вылет из Москвы, один человек, бюджет 1500 евро"
)


# ---------------------------------------------------------------------------
# Фикстура: будущие даты (чтобы тесты не зависели от текущей даты)
# ---------------------------------------------------------------------------

def _next_month_dates(nights: int = 5) -> tuple[date, date]:
    today = date.today()
    departure = today.replace(day=1) + timedelta(days=32)
    departure = departure.replace(day=15)  # середина следующего месяца
    return departure, departure + timedelta(days=nights)


# ---------------------------------------------------------------------------
# Мок TripProfile — уже полный (complete)
# ---------------------------------------------------------------------------

def make_complete_profile(**kwargs):
    from models.schemas import Currency, TripProfile
    dep, ret = _next_month_dates(5)
    defaults = dict(
        destination="Тюмень",
        origin="Москва",
        departure_date=dep,
        return_date=ret,
        travelers=2,
        budget=150000,
        currency=Currency.RUB,
        preferences=["музеи", "рестораны"],
    )
    defaults.update(kwargs)
    return TripProfile(**defaults)


# ---------------------------------------------------------------------------
# Вспомогательная функция: мок IntentAgent (сразу complete)
# ---------------------------------------------------------------------------

def mock_intent_complete(profile=None):
    """Возвращает patch для intent_agent.run, который сразу возвращает complete=True."""
    _profile = profile or make_complete_profile()

    def _run(session, message):
        session.trip_profile = _profile
        return "Отлично! Начинаю поиск...", True

    return patch("agents.intent_agent.run", side_effect=_run)


# ---------------------------------------------------------------------------
# Вспомогательная функция: мок SearchResults
# ---------------------------------------------------------------------------

def make_mock_search_results(
    *,
    flights_count: int = 3,
    hotels_count: int = 3,
    degraded: bool = False,
    no_flights: bool = False,
):
    """Создаёт SearchResults с минимально необходимыми данными."""
    from models.schemas import (
        Accommodation, Currency, Flight, SearchResults,
    )

    dep, ret = _next_month_dates(5)

    flights = [] if no_flights else [
        Flight(
            id=f"FL{i:03d}",
            airline="Аэрофлот",
            origin="Москва",
            destination="Тюмень",
            departure=f"{dep}T10:00:00",
            arrival=f"{dep}T13:30:00",
            price=12000.0 + i * 500,
            currency=Currency.RUB,
            stops=0,
        )
        for i in range(flights_count)
    ]

    accommodations = [] if degraded else [
        Accommodation(
            id=f"H{i:03d}",
            name=f"Отель {i+1}",
            type="hotel",
            city="Тюмень",
            price_per_night=2500.0 + i * 200,
            total_price=(2500.0 + i * 200) * 5,
            currency=Currency.RUB,
            rating=4.0 + i * 0.2,
            amenities=["WiFi", "завтрак"],
        )
        for i in range(hotels_count)
    ]

    return SearchResults(
        flights=flights,
        accommodations=accommodations,
        flights_source="mock_flights",
        accommodation_source="none" if degraded else "mock_booking",
        accommodation_degraded=degraded,
    )


# ---------------------------------------------------------------------------
# Полный async run_session: пропускает LLM, использует готовый profile
# ---------------------------------------------------------------------------

async def run_session(
    user_message: str,
    profile=None,
    search_results=None,
    mock_itinerary: bool = True,
) -> tuple:
    """
    Запускает полную сессию с заглушками для LLM-вызовов.
    Возвращает (session, reply) после финального REPORT шага.
    """
    from agents.orchestrator import Orchestrator
    from models.schemas import Itinerary, DayPlan, ActivityItem

    _profile = profile or make_complete_profile()
    _search = search_results or make_mock_search_results()

    def _mock_intent(session, message):
        session.trip_profile = _profile
        return "Отлично! Начинаю поиск...", True

    async def _mock_search(session):
        session.search_results = _search
        from models.schemas import SearchResults
        return _search

    def _mock_itinerary_run(session):
        session.itinerary = Itinerary(
            destination=_profile.destination or "Тюмень",
            days=[
                DayPlan(
                    day=1,
                    date=str(_profile.departure_date or ""),
                    title="Прибытие",
                    activities=[
                        ActivityItem(
                            time="14:00",
                            description="Заселение в отель",
                            location="Отель",
                        )
                    ],
                )
            ],
            sources=["mock_kb"],
            rag_used=False,
        )
        return session.itinerary

    patches = [
        patch("agents.intent_agent.run", side_effect=_mock_intent),
        patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)),
    ]
    if mock_itinerary:
        patches.append(patch("agents.itinerary_agent.run", side_effect=_mock_itinerary_run))

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    with patches[0], patches[1], patches[2] if mock_itinerary else _noop_ctx():
        # 1. Первый msg → intent complete → budget_check → search → optimize → budget_final → HITL
        reply1 = await orchestrator.handle_message(session, user_message)
        # 2. Подтверждаем HITL → itinerary → report → DONE
        reply2 = await orchestrator.handle_message(session, "да")

    return session, reply2


class _noop_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def complete_profile():
    return make_complete_profile()


@pytest.fixture
def mock_search():
    return make_mock_search_results()


@pytest.fixture
def orchestrator():
    from agents.orchestrator import Orchestrator
    return Orchestrator()


@pytest.fixture
def session(orchestrator):
    return orchestrator.new_session()
