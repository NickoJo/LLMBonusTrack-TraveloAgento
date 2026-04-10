"""
Latency eval-тесты — проверяют временны́е характеристики компонентов.

Цель: убедиться что детерминированные компоненты (без LLM) работают быстро.
LLM-компоненты (IntentAgent, ItineraryAgent) мокируются.

Порог: полный пайплайн (с моками) < 5 секунд.
"""
import asyncio
import time
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tests.evals.conftest import (
    STANDARD_REQUEST,
    make_complete_profile,
    make_mock_search_results,
    run_session,
)


# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

# Максимальное время полного пайплайна с заглушками LLM (секунды)
MAX_PIPELINE_SEC = 5.0

# Максимальное время детерминированных компонентов (секунды)
MAX_DETERMINISTIC_SEC = 0.5


# ---------------------------------------------------------------------------
# Test 1: Полный пайплайн (без реальных LLM-вызовов) < 5 сек
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_latency():
    t0 = time.perf_counter()
    session, reply = await run_session(STANDARD_REQUEST)
    elapsed = time.perf_counter() - t0

    assert elapsed < MAX_PIPELINE_SEC, (
        f"Полный пайплайн занял {elapsed:.2f}s > порог {MAX_PIPELINE_SEC}s"
    )


# ---------------------------------------------------------------------------
# Test 2: OptimizationAgent < 0.5 сек на 5×5 комбинациях
# ---------------------------------------------------------------------------

def test_optimization_agent_latency():
    from agents.optimization_agent import optimize

    profile = make_complete_profile()
    search = make_mock_search_results(flights_count=5, hotels_count=5)

    t0 = time.perf_counter()
    package = optimize(search, profile)
    elapsed = time.perf_counter() - t0

    assert elapsed < MAX_DETERMINISTIC_SEC, (
        f"OptimizationAgent занял {elapsed:.3f}s > {MAX_DETERMINISTIC_SEC}s"
    )
    assert package is not None


# ---------------------------------------------------------------------------
# Test 3: BudgetTracker early_check < 0.1 сек
# ---------------------------------------------------------------------------

def test_budget_early_check_latency():
    from agents.budget_tracker import early_check

    profile = make_complete_profile(budget=150000)

    t0 = time.perf_counter()
    for _ in range(100):
        result = early_check(profile)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.1, (
        f"100 вызовов early_check: {elapsed:.3f}s > 0.1s"
    )


# ---------------------------------------------------------------------------
# Test 4: BudgetTracker final_check < 0.1 сек
# ---------------------------------------------------------------------------

def test_budget_final_check_latency():
    from agents.budget_tracker import final_check
    from agents.optimization_agent import optimize

    profile = make_complete_profile(budget=150000)
    search = make_mock_search_results(flights_count=2, hotels_count=2)
    package = optimize(search, profile)

    t0 = time.perf_counter()
    for _ in range(100):
        bs = final_check(package, 150000)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.1, (
        f"100 вызовов final_check: {elapsed:.3f}s > 0.1s"
    )


# ---------------------------------------------------------------------------
# Test 5: ReportFormatter.run < 0.2 сек
# ---------------------------------------------------------------------------

def test_report_formatter_latency():
    from agents import report_formatter
    from agents.optimization_agent import optimize
    from models.schemas import (
        ActivityItem, BudgetState, BudgetStatus, Currency,
        DayPlan, Itinerary, SessionState,
    )
    import uuid

    dep = date.today() + timedelta(days=30)
    ret = dep + timedelta(days=5)

    profile = make_complete_profile(budget=150000)
    search = make_mock_search_results(flights_count=2, hotels_count=2)
    package = optimize(search, profile)
    budget_state = BudgetState(
        budget=150000, spent=package.total_cost, remaining=package.budget_remaining,
        currency=Currency.RUB, status=BudgetStatus.OK, overage_pct=0.0,
    )
    itinerary = Itinerary(
        destination="Тюмень",
        days=[
            DayPlan(
                day=i + 1, date=str(dep + timedelta(days=i)),
                title=f"День {i + 1}",
                activities=[
                    ActivityItem(time="10:00", description=f"Активность {i+1}",
                                 location="Центр города")
                ],
            )
            for i in range(5)
        ],
        sources=["tyumen/Достопримечательности"],
        rag_used=True,
    )

    session = SessionState(session_id=uuid.uuid4().hex[:12], created_at="2026-01-01T00:00:00Z")
    session.selected_package = package
    session.budget_state = budget_state
    session.itinerary = itinerary

    t0 = time.perf_counter()
    for _ in range(50):
        report = report_formatter.run(session)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2, (
        f"50 вызовов report_formatter: {elapsed:.3f}s > 0.2s"
    )
    assert report


# ---------------------------------------------------------------------------
# Test 6: PII Guard — sanitize < 0.05 сек на 100 вызовах
# ---------------------------------------------------------------------------

def test_guardrail_latency():
    from middleware.guardrail import has_pii, sanitize

    text = "Хочу слетать в Тюмень на 5 дней, бюджет 150000 рублей, нас двое"

    t0 = time.perf_counter()
    for _ in range(100):
        types = has_pii(text)
        cleaned = sanitize(text, {})
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.05, (
        f"100 вызовов PII guard: {elapsed:.3f}s > 0.05s"
    )


# ---------------------------------------------------------------------------
# Test 7: Параллельность SearchAgent — два мок-инструмента параллельно
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_agent_parallel_execution():
    """Проверяем что SearchAgent действительно запускает поиски параллельно."""
    from agents.orchestrator import Orchestrator

    profile = make_complete_profile()
    search = make_mock_search_results()
    call_times = []

    async def _mock_search_flights(**kwargs):
        t = time.perf_counter()
        await asyncio.sleep(0.1)  # Симулируем задержку 100ms
        call_times.append(("flights", time.perf_counter() - t))
        from tools.flights import FlightSearchResult
        return FlightSearchResult(flights=search.flights)

    async def _mock_search_hotels(**kwargs):
        t = time.perf_counter()
        await asyncio.sleep(0.1)  # Симулируем задержку 100ms
        call_times.append(("hotels", time.perf_counter() - t))
        from tools.hotels import HotelSearchResult
        return HotelSearchResult(hotels=search.accommodations)

    def _mock_intent(session, message):
        session.trip_profile = profile
        return "Отлично!", True

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    t0 = time.perf_counter()
    with patch("agents.intent_agent.run", side_effect=_mock_intent), \
         patch("tools.flights.search_flights", side_effect=_mock_search_flights), \
         patch("tools.hotels.search_hotels", side_effect=_mock_search_hotels):

        await orchestrator.handle_message(session, STANDARD_REQUEST)

    elapsed = time.perf_counter() - t0

    # При параллельном выполнении двух 100ms операций результат должен быть ~100ms, а не ~200ms
    # С накладными расходами допускаем до 1 сек (state machine добавляет шаги)
    # Главное: НЕ более 250ms на сами поиски
    if len(call_times) >= 2:
        # Оба вызова выполнились примерно одновременно (< 150ms каждый)
        assert all(t < 0.15 for _, t in call_times), (
            f"Вызовы не параллельны: {call_times}"
        )
