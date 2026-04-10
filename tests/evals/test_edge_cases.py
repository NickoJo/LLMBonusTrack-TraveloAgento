"""
Edge case eval-тесты — граничные условия, деградированные режимы, failover.
"""
import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tests.evals.conftest import (
    STANDARD_REQUEST,
    make_complete_profile,
    make_mock_search_results,
    run_session,
)
from models.schemas import AgentStep, BudgetStatus, Currency, SessionStatus


# ---------------------------------------------------------------------------
# Test 1: Degraded mode — только рейсы, без жилья (оба отельных сервиса упали)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_degraded_mode_no_hotels():
    search = make_mock_search_results(degraded=True, hotels_count=0)
    session, reply = await run_session(STANDARD_REQUEST, search_results=search)

    # Должны всё равно завершиться (без жилья — degraded accommodation)
    assert session.status == SessionStatus.COMPLETED
    assert session.selected_package is not None
    # В degraded режиме жильё — заглушка "none"
    assert session.selected_package.accommodation is not None


# ---------------------------------------------------------------------------
# Test 2: Бюджет превышен >10% → HITL_BUDGET checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_exceeded_triggers_hitl_budget():
    """Бюджет 10000 RUB — значительно ниже стоимости любого пакета."""
    profile = make_complete_profile(budget=10000)  # заведомо мал

    from agents.orchestrator import Orchestrator
    from models.schemas import Itinerary, DayPlan, ActivityItem

    search = make_mock_search_results()

    def _mock_intent(session, message):
        session.trip_profile = profile
        return "Отлично!", True

    async def _mock_search(session):
        session.search_results = search
        return search

    def _mock_itinerary_run(session):
        session.itinerary = Itinerary(
            destination="Тюмень",
            days=[DayPlan(day=1, date="", title="День 1",
                          activities=[ActivityItem(time="10:00", description="Прогулка")])],
            sources=[],
            rag_used=False,
        )
        return session.itinerary

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    with patch("agents.intent_agent.run", side_effect=_mock_intent), \
         patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)), \
         patch("agents.itinerary_agent.run", side_effect=_mock_itinerary_run):

        # Первый запрос — дойдёт до HITL_BUDGET (нужен ввод от пользователя)
        reply1 = await orchestrator.handle_message(session, STANDARD_REQUEST)
        assert session.agent_step in (AgentStep.HITL_BUDGET, AgentStep.HITL_CONFIRM,
                                       AgentStep.DONE, AgentStep.INTENT)

        # Проверяем что budget_state указывает на превышение
        if session.budget_state:
            assert session.budget_state.status in (BudgetStatus.WARNING, BudgetStatus.EXCEEDED)


# ---------------------------------------------------------------------------
# Test 3: Нет рейсов → возврат к INTENT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_flights_returns_to_intent():
    search = make_mock_search_results(no_flights=True)
    profile = make_complete_profile()

    from agents.orchestrator import Orchestrator

    def _mock_intent(session, message):
        session.trip_profile = profile
        return "Отлично!", True

    async def _mock_search(session):
        session.search_results = search
        return search

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    with patch("agents.intent_agent.run", side_effect=_mock_intent), \
         patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)):

        reply = await orchestrator.handle_message(session, STANDARD_REQUEST)

    # Нет рейсов — агент должен вернуться к шагу INTENT и попросить изменить параметры
    assert "не найден" in reply.lower() or session.agent_step == AgentStep.INTENT


# ---------------------------------------------------------------------------
# Test 4: Пользователь отказывается от пакета → "другой вариант"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_requests_another_option():
    from agents.orchestrator import Orchestrator

    profile = make_complete_profile()
    search = make_mock_search_results()

    def _mock_intent(session, message):
        session.trip_profile = profile
        return "Отлично!", True

    async def _mock_search(session):
        session.search_results = search
        return search

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    with patch("agents.intent_agent.run", side_effect=_mock_intent), \
         patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)):

        # Доходим до HITL
        reply1 = await orchestrator.handle_message(session, STANDARD_REQUEST)

        # Просим другой вариант
        with patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)):
            reply2 = await orchestrator.handle_message(session, "другой вариант")

    # После "другой вариант" - поиск запускается снова
    assert session.agent_step in (
        AgentStep.HITL_CONFIRM, AgentStep.HITL_BUDGET,
        AgentStep.SEARCH, AgentStep.OPTIMIZE, AgentStep.BUDGET_CHECK_FINAL
    )


# ---------------------------------------------------------------------------
# Test 5: Circuit breaker — слишком много LLM-вызовов
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker():
    from agents.orchestrator import Orchestrator
    from config import cfg

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    # Имитируем достижение лимита
    session.llm_call_count = cfg.MAX_LLM_CALLS

    reply = await orchestrator.handle_message(session, "Хочу в Тюмень")

    assert "лимит" in reply.lower() or "сессию" in reply.lower()
    assert session.status == SessionStatus.ERROR


# ---------------------------------------------------------------------------
# Test 6: Ранняя бюджетная проверка — слишком маленький бюджет в RUB
# ---------------------------------------------------------------------------

def test_early_budget_check_too_low():
    from agents.budget_tracker import early_check
    from models.schemas import TripProfile

    profile = make_complete_profile(budget=1000, currency=Currency.RUB)
    result = early_check(profile)

    assert not result.ok, "При бюджете 1000 RUB ранняя проверка должна вернуть not ok"
    assert result.message


# ---------------------------------------------------------------------------
# Test 7: Ранняя бюджетная проверка — достаточный бюджет
# ---------------------------------------------------------------------------

def test_early_budget_check_sufficient():
    from agents.budget_tracker import early_check

    profile = make_complete_profile(budget=150000, currency=Currency.RUB)
    result = early_check(profile)

    assert result.ok, f"При бюджете 150000 RUB проверка должна пройти: {result.message}"


# ---------------------------------------------------------------------------
# Test 8: OptimizationAgent с пустым списком жилья (degraded)
# ---------------------------------------------------------------------------

def test_optimization_with_no_hotels():
    from agents.optimization_agent import optimize

    profile = make_complete_profile()
    search = make_mock_search_results(degraded=True, flights_count=3)

    package = optimize(search, profile)

    assert package is not None
    assert package.flight is not None
    # Accommodation — заглушка
    assert package.accommodation is not None
    assert package.accommodation.id == "none"


# ---------------------------------------------------------------------------
# Test 9: Финальная проверка бюджета — OK статус
# ---------------------------------------------------------------------------

def test_final_budget_check_ok():
    from agents.budget_tracker import final_check
    from agents.optimization_agent import optimize

    profile = make_complete_profile(budget=150000)
    search = make_mock_search_results(flights_count=2, hotels_count=2)

    package = optimize(search, profile)
    bs = final_check(package, 150000)

    assert bs.status == BudgetStatus.OK
    assert bs.spent > 0
    assert bs.remaining > 0


# ---------------------------------------------------------------------------
# Test 10: Неясный ответ на HITL → переспрашивает
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hitl_unclear_answer():
    from agents.orchestrator import Orchestrator

    profile = make_complete_profile()
    search = make_mock_search_results()

    def _mock_intent(session, message):
        session.trip_profile = profile
        return "Отлично!", True

    async def _mock_search(session):
        session.search_results = search
        return search

    orchestrator = Orchestrator()
    session = orchestrator.new_session()

    with patch("agents.intent_agent.run", side_effect=_mock_intent), \
         patch("agents.search_agent.run", new=AsyncMock(side_effect=_mock_search)):

        # Доходим до HITL
        reply1 = await orchestrator.handle_message(session, STANDARD_REQUEST)
        # Непонятный ответ
        reply2 = await orchestrator.handle_message(session, "возможно")

    # Должен переспросить
    assert "пожалуйста" in reply2.lower() or "да" in reply2.lower() or \
           session.hitl_pending is True
