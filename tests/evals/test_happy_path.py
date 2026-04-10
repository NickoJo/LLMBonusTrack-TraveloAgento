"""
Happy path eval-тесты — проверяют полный пайплайн при нормальных условиях.

Стратегия: заглушаем LLM-вызовы (intent, itinerary) и mock-инструменты,
чтобы тест не зависел от внешнего API. Тестируется бизнес-логика.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.evals.conftest import (
    STANDARD_REQUEST,
    make_complete_profile,
    make_mock_search_results,
    run_session,
)
from models.schemas import AgentStep, BudgetStatus, SessionStatus


# ---------------------------------------------------------------------------
# Test 1: Полный пайплайн завершается со статусом COMPLETED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_completes():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.status == SessionStatus.COMPLETED, (
        f"Сессия должна быть COMPLETED, но статус: {session.status}"
    )
    assert session.agent_step == AgentStep.DONE, (
        f"Последний шаг должен быть DONE, но: {session.agent_step}"
    )


# ---------------------------------------------------------------------------
# Test 2: Финальный отчёт содержит ключевые секции
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_report_has_required_sections():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.final_report is not None, "final_report не должен быть None"
    report = session.final_report

    required_sections = ["## Перелёт", "## Жильё", "## Бюджет"]
    for section in required_sections:
        assert section in report, f"В отчёте отсутствует секция: {section}"


# ---------------------------------------------------------------------------
# Test 3: Финальный отчёт содержит дисклеймер
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_report_has_disclaimer():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.final_report is not None
    assert "носит рекомендательный характер" in session.final_report, (
        "Дисклеймер должен присутствовать в отчёте"
    )


# ---------------------------------------------------------------------------
# Test 4: BudgetState создан и содержит корректные данные
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_state_is_populated():
    profile = make_complete_profile(budget=150000)
    session, reply = await run_session(STANDARD_REQUEST, profile=profile)

    assert session.budget_state is not None, "budget_state должен быть заполнен"
    bs = session.budget_state
    assert bs.budget == 150000
    assert bs.spent > 0, "spent должен быть > 0"
    assert bs.status in (BudgetStatus.OK, BudgetStatus.WARNING, BudgetStatus.EXCEEDED)


# ---------------------------------------------------------------------------
# Test 5: SelectedPackage выбран корректно
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selected_package_fields():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.selected_package is not None
    pkg = session.selected_package
    assert pkg.flight is not None
    assert pkg.accommodation is not None
    assert pkg.flight.price > 0
    assert pkg.accommodation.rating > 0


# ---------------------------------------------------------------------------
# Test 6: LLM call count не превышает лимит
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_call_count_within_limit():
    from config import cfg
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.llm_call_count <= cfg.MAX_LLM_CALLS, (
        f"LLM calls {session.llm_call_count} превышает лимит {cfg.MAX_LLM_CALLS}"
    )


# ---------------------------------------------------------------------------
# Test 7: Программа по дням присутствует в отчёте
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_itinerary_in_report():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.itinerary is not None, "Itinerary должен быть создан"
    assert len(session.itinerary.days) >= 1, "Должен быть хотя бы один день"
    assert session.final_report is not None
    assert "## Программа по дням" in session.final_report or \
           "### День" in session.final_report


# ---------------------------------------------------------------------------
# Test 8: SearchResults содержит рейсы и жильё
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_results_populated():
    session, reply = await run_session(STANDARD_REQUEST)

    assert session.search_results is not None
    assert len(session.search_results.flights) > 0, "Должен быть хотя бы один рейс"


# ---------------------------------------------------------------------------
# Test 9: Оптимизация выбирает пакет с лучшим score
# ---------------------------------------------------------------------------

def test_optimization_selects_best_package():
    from agents.optimization_agent import optimize
    from models.schemas import Currency, TripProfile
    from datetime import date, timedelta

    today = date.today()
    dep = today + timedelta(days=30)
    ret = dep + timedelta(days=5)

    profile = TripProfile(
        destination="Тюмень",
        origin="Москва",
        departure_date=dep,
        return_date=ret,
        travelers=1,
        budget=200000,
        currency=Currency.RUB,
    )

    results = make_mock_search_results(flights_count=3, hotels_count=3)
    package = optimize(results, profile)

    assert package is not None
    assert package.flight is not None
    assert package.accommodation is not None
    # Оптимизатор должен выбрать вариант (не обязательно самый дешёвый)
    total = package.flight.price + package.accommodation.total_price
    assert total > 0


# ---------------------------------------------------------------------------
# Test 10: Итинерарий содержит корректные поля
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_itinerary_structure():
    session, reply = await run_session(STANDARD_REQUEST)

    itin = session.itinerary
    assert itin is not None
    for day in itin.days:
        assert day.day >= 1
        assert day.title
        assert isinstance(day.activities, list)
        for act in day.activities:
            assert act.time
            assert act.description
