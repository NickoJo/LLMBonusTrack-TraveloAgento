"""
Orchestrator — центральный управляющий компонент.
State machine: INTENT → BUDGET_CHECK_EARLY → SEARCH → OPTIMIZE →
               BUDGET_CHECK_FINAL → HITL_CONFIRM → ITINERARY → REPORT → DONE

Не принимает содержательных решений — только управляет потоком.
"""
import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from agents import (
    budget_tracker,
    intent_agent,
    itinerary_agent,
    optimization_agent,
    report_formatter,
    search_agent,
)
from config import cfg
from middleware import guardrail, logger
from models.schemas import AgentStep, Message, SessionState, SessionStatus


class StepResult:
    def __init__(
        self,
        reply: str,
        needs_user_input: bool = True,
        is_final: bool = False,
        next_step: Optional[AgentStep] = None,
    ):
        self.reply = reply
        self.needs_user_input = needs_user_input
        self.is_final = is_final
        self.next_step = next_step


class Orchestrator:
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}

    def new_session(self) -> SessionState:
        session_id = uuid.uuid4().hex[:12]
        session = SessionState(
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session_id] = session
        logger.log_session_start(session_id)
        return session

    async def handle_message(self, session: SessionState, raw_message: str) -> str:
        """
        Главная точка входа. Принимает сырое сообщение пользователя,
        прогоняет через state machine, возвращает ответ.
        """
        t0 = time.time()

        # 0. Circuit breaker
        if session.llm_call_count >= cfg.MAX_LLM_CALLS:
            return self._circuit_break(session, "LLM call limit exceeded")

        # 1. PII Guard
        pii_types = guardrail.has_pii(raw_message)
        if pii_types:
            logger.log_pii_detected(session.session_id, pii_types)
        clean_message = guardrail.sanitize(raw_message, session.pii_token_map)

        # 2. Добавляем в историю
        session.dialog_history.append(Message(role="user", content=clean_message))
        self._trim_history(session)

        # 3. State machine — выполняем шаги пока не нужен ответ пользователю
        reply = await self._run_steps(session, clean_message)

        # 4. Логируем
        duration_ms = int((time.time() - t0) * 1000)
        logger.log_agent_call(
            session.session_id, "Orchestrator", session.agent_step.value,
            duration_ms, session.status.value,
        )

        session.turn_count += 1
        return reply

    async def _run_steps(self, session: SessionState, message: str) -> str:
        """
        Выполняет шаги state machine до тех пор, пока не нужен
        ответ пользователю или не достигнут DONE/ERROR.
        """
        while True:
            step = session.agent_step

            if step == AgentStep.INTENT:
                result = await self._step_intent(session, message)

            elif step == AgentStep.BUDGET_CHECK_EARLY:
                result = self._step_budget_early(session)

            elif step == AgentStep.SEARCH:
                result = await self._step_search(session)

            elif step == AgentStep.OPTIMIZE:
                result = self._step_optimize(session)

            elif step == AgentStep.BUDGET_CHECK_FINAL:
                result = self._step_budget_final(session)

            elif step in (AgentStep.HITL_CONFIRM, AgentStep.HITL_BUDGET):
                result = self._step_hitl(session, message)

            elif step == AgentStep.ITINERARY:
                result = await self._step_itinerary(session)

            elif step == AgentStep.REPORT:
                result = self._step_report(session)

            elif step == AgentStep.DONE:
                return session.final_report or "Ваш план готов!"

            elif step == AgentStep.ERROR:
                return session.final_report or "Произошла ошибка. Пожалуйста, начните новую сессию."

            else:
                return "Неизвестное состояние. Начните новую сессию."

            # Обновляем шаг если указан
            if result.next_step is not None:
                session.agent_step = result.next_step

            # Добавляем ответ ассистента в историю
            if result.reply:
                session.dialog_history.append(
                    Message(role="assistant", content=result.reply)
                )

            # Если нужен ответ пользователю — возвращаем
            if result.needs_user_input or result.is_final:
                if result.is_final:
                    session.status = SessionStatus.COMPLETED
                    session.clear_pii()
                    self._log_session_end(session)
                return result.reply

    # ------------------------------------------------------------------
    # Шаги
    # ------------------------------------------------------------------

    async def _step_intent(self, session: SessionState, message: str) -> StepResult:
        t0 = time.time()
        try:
            reply, complete = intent_agent.run(session, message)
            logger.log_agent_call(
                session.session_id, "IntentAgent", "INTENT",
                int((time.time() - t0) * 1000), "success",
            )
            if complete:
                return StepResult(
                    reply=reply,
                    needs_user_input=False,
                    next_step=AgentStep.BUDGET_CHECK_EARLY,
                )
            return StepResult(reply=reply, needs_user_input=True, next_step=AgentStep.INTENT)
        except Exception as e:
            logger.log_error(session.session_id, "IntentAgent", type(e).__name__, str(e))
            return StepResult(
                reply="Извините, возникла проблема с обработкой запроса. Попробуйте переформулировать.",
                needs_user_input=True,
                next_step=AgentStep.INTENT,
            )

    def _step_budget_early(self, session: SessionState) -> StepResult:
        t0 = time.time()
        result = budget_tracker.early_check(session.trip_profile)
        logger.log_budget_check(session.session_id, "early",
                                "ok" if result.ok else "nok")
        logger.log_agent_call(
            session.session_id, "BudgetTracker", "BUDGET_CHECK_EARLY",
            int((time.time() - t0) * 1000), "success",
        )
        if not result.ok:
            return StepResult(
                reply=result.message + "\n\nХотите скорректировать бюджет или параметры поездки?",
                needs_user_input=True,
                next_step=AgentStep.INTENT,
            )
        return StepResult(reply="", needs_user_input=False, next_step=AgentStep.SEARCH)

    async def _step_search(self, session: SessionState) -> StepResult:
        t0 = time.time()
        try:
            results = await search_agent.run(session)
            logger.log_agent_call(
                session.session_id, "SearchAgent", "SEARCH",
                int((time.time() - t0) * 1000), "success",
            )

            warnings = []
            if results.accommodation_degraded:
                warnings.append(
                    "⚠️ Сервис поиска отелей временно недоступен — показываю варианты из альтернативного источника."
                )
            if "short_connection" in session.risk_flags:
                warnings.append(
                    "⚠️ В найденных рейсах есть варианты с короткой стыковкой (менее 50 мин)."
                )

            if not results.flights:
                return StepResult(
                    reply="К сожалению, рейсы по вашим параметрам не найдены. "
                          "Попробуйте изменить даты или направление.",
                    needs_user_input=True,
                    next_step=AgentStep.INTENT,
                )

            prefix = "\n".join(warnings) + "\n\n" if warnings else ""
            return StepResult(
                reply=prefix + f"Найдено {len(results.flights)} рейсов и "
                      f"{len(results.accommodations)} вариантов жилья. Подбираю оптимальный пакет...",
                needs_user_input=False,
                next_step=AgentStep.OPTIMIZE,
            )
        except Exception as e:
            logger.log_error(session.session_id, "SearchAgent", type(e).__name__, str(e))
            session.agent_step = AgentStep.ERROR
            return StepResult(
                reply="Не удалось выполнить поиск. Проверьте параметры и попробуйте снова.",
                needs_user_input=True,
                next_step=AgentStep.ERROR,
            )

    def _step_optimize(self, session: SessionState) -> StepResult:
        t0 = time.time()
        try:
            package = optimization_agent.optimize(
                session.search_results, session.trip_profile
            )
            session.selected_package = package
            logger.log_agent_call(
                session.session_id, "OptimizationAgent", "OPTIMIZE",
                int((time.time() - t0) * 1000), "success",
            )
            return StepResult(reply="", needs_user_input=False,
                              next_step=AgentStep.BUDGET_CHECK_FINAL)
        except Exception as e:
            logger.log_error(session.session_id, "OptimizationAgent", type(e).__name__, str(e))
            return StepResult(
                reply="Не удалось подобрать оптимальный пакет. Попробуйте изменить параметры.",
                needs_user_input=True,
                next_step=AgentStep.INTENT,
            )

    def _step_budget_final(self, session: SessionState) -> StepResult:
        t0 = time.time()
        budget_state = budget_tracker.final_check(
            session.selected_package, session.trip_profile.budget or 0
        )
        session.budget_state = budget_state
        logger.log_budget_check(session.session_id, "final", budget_state.status.value)
        logger.log_agent_call(
            session.session_id, "BudgetTracker", "BUDGET_CHECK_FINAL",
            int((time.time() - t0) * 1000), "success",
        )

        if budget_state.status.value == "exceeded":
            return StepResult(
                reply="", needs_user_input=False, next_step=AgentStep.HITL_BUDGET
            )
        return StepResult(reply="", needs_user_input=False, next_step=AgentStep.HITL_CONFIRM)

    def _step_hitl(self, session: SessionState, message: str) -> StepResult:
        """HiTL checkpoint — показываем пакет и ждём решения пользователя."""
        step = session.agent_step

        # Если это первый заход в HITL (нет pending) — показываем пакет
        if not session.hitl_pending:
            session.hitl_pending = True
            return StepResult(
                reply=self._format_hitl_message(session),
                needs_user_input=True,
                next_step=step,  # остаёмся в том же шаге
            )

        # Это ответ пользователя на checkpoint
        session.hitl_pending = False
        msg_lower = message.lower().strip()

        # Распознаём намерение: подтвердить / другой вариант / сменить город
        if _user_confirms(msg_lower):
            logger.log_hitl(session.session_id, step.value, "confirmed")
            return StepResult(reply="", needs_user_input=False, next_step=AgentStep.ITINERARY)

        elif _user_wants_another(msg_lower):
            logger.log_hitl(session.session_id, step.value, "another_option")
            session.reset_search()
            return StepResult(
                reply="Ищу другие варианты...",
                needs_user_input=False,
                next_step=AgentStep.SEARCH,
            )

        elif _user_changes_city(msg_lower, session):
            logger.log_hitl(session.session_id, step.value, "city_changed")
            session.reset_search()
            return StepResult(
                reply="Принял. Обновляю поиск под новый город...",
                needs_user_input=False,
                next_step=AgentStep.INTENT,
            )

        else:
            # Неясный ответ — переспросить
            session.hitl_pending = True
            return StepResult(
                reply="Пожалуйста, напишите «да» чтобы продолжить, или «другой вариант» / «изменить город».",
                needs_user_input=True,
                next_step=step,
            )

    async def _step_itinerary(self, session: SessionState) -> StepResult:
        t0 = time.time()
        try:
            itinerary_agent.run(session)
            logger.log_agent_call(
                session.session_id, "ItineraryAgent", "ITINERARY",
                int((time.time() - t0) * 1000), "success",
            )
            return StepResult(reply="", needs_user_input=False, next_step=AgentStep.REPORT)
        except Exception as e:
            logger.log_error(session.session_id, "ItineraryAgent", type(e).__name__, str(e))
            # Продолжаем без полного итинерария
            session.itinerary = None
            return StepResult(reply="", needs_user_input=False, next_step=AgentStep.REPORT)

    def _step_report(self, session: SessionState) -> StepResult:
        t0 = time.time()
        report = report_formatter.run(session)
        logger.log_agent_call(
            session.session_id, "ReportFormatter", "REPORT",
            int((time.time() - t0) * 1000), "success",
        )
        return StepResult(
            reply=report,
            needs_user_input=False,
            is_final=True,
            next_step=AgentStep.DONE,
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _format_hitl_message(self, session: SessionState) -> str:
        pkg = session.selected_package
        budget = session.budget_state
        step = session.agent_step

        lines = []
        if step == AgentStep.HITL_BUDGET and budget:
            lines.append(
                f"⚠️ Стоимость пакета ({budget.spent:.0f} {budget.currency.value}) "
                f"превышает ваш бюджет ({budget.budget:.0f} {budget.currency.value}) "
                f"на {budget.overage_pct:.1f}%."
            )
            lines.append("")

        if pkg:
            f = pkg.flight
            h = pkg.accommodation
            lines.append("**Подобранный пакет:**")
            lines.append(f"✈️ {f.airline}: {f.origin} → {f.destination}, "
                         f"{_fmt_short(f.departure)} — {_fmt_short(f.arrival)}")
            lines.append(f"   Стоимость перелёта: {f.price:.0f} {f.currency.value}")
            lines.append(f"🏨 {h.name} (рейтинг {h.rating:.1f}/5)")
            lines.append(f"   Стоимость жилья: {h.total_price:.0f} {h.currency.value}")
            if budget:
                lines.append(f"💰 **Итого: {budget.spent:.0f} {budget.currency.value}**")
                remaining = budget.remaining
                if remaining >= 0:
                    lines.append(f"   Остаток бюджета: {remaining:.0f} {budget.currency.value}")

        if "short_connection" in session.risk_flags:
            lines.append("")
            lines.append("⚠️ В маршруте есть стыковка менее 50 минут.")

        lines.append("")
        if step == AgentStep.HITL_BUDGET:
            lines.append("Хотите продолжить с этим вариантом несмотря на превышение бюджета? "
                         "Или искать **другой вариант**?")
        else:
            lines.append("Подтверждаете этот вариант? Напишите **«да»** для продолжения, "
                         "**«другой вариант»** или **«изменить город»**.")

        return "\n".join(lines)

    def _circuit_break(self, session: SessionState, reason: str) -> str:
        logger.log_circuit_break(session.session_id, reason, session.llm_call_count)
        session.status = SessionStatus.ERROR
        session.clear_pii()
        self._log_session_end(session)
        return ("Достигнут лимит запросов в этой сессии. "
                "Пожалуйста, начните новый диалог.")

    def _trim_history(self, session: SessionState) -> None:
        max_msgs = cfg.MAX_CONTEXT_MESSAGES
        if len(session.dialog_history) > max_msgs:
            old = session.dialog_history[:-max_msgs]
            session.dialog_history = session.dialog_history[-max_msgs:]
            # Простая суммаризация без LLM — конкатенируем первые реплики
            if not session.dialog_summary:
                session.dialog_summary = " | ".join(
                    m.content[:80] for m in old[:5]
                )

    def _log_session_end(self, session: SessionState) -> None:
        from datetime import datetime as dt, timezone as tz
        start = datetime.fromisoformat(session.created_at)
        duration_ms = int(
            (datetime.now(timezone.utc) - start).total_seconds() * 1000
        )
        logger.log_session_end(
            session.session_id,
            status=session.status.value,
            turns=session.turn_count,
            llm_calls=session.llm_call_count,
            duration_ms=duration_ms,
        )


# ------------------------------------------------------------------
# Утилиты распознавания ответа пользователя
# ------------------------------------------------------------------

_CONFIRM_WORDS = {"да", "yes", "ок", "ok", "окей", "okay", "подтверждаю",
                  "подходит", "согласен", "согласна", "давай", "продолжай",
                  "продолжить", "хорошо", "отлично", "👍"}

_ANOTHER_WORDS = {"другой", "другие", "другое", "ещё", "еще", "иной",
                  "другой вариант", "другие варианты", "перебрать", "поменять",
                  "alternative", "another"}

def _user_confirms(msg: str) -> bool:
    return any(w in msg for w in _CONFIRM_WORDS)

def _user_wants_another(msg: str) -> bool:
    return any(w in msg for w in _ANOTHER_WORDS)

def _user_changes_city(msg: str, session: SessionState) -> bool:
    change_words = {"изменить город", "другой город", "change city",
                    "поменять город", "другое место", "другое направление"}
    return any(w in msg for w in change_words)

def _fmt_short(dt_str: str) -> str:
    try:
        from datetime import datetime
        return datetime.fromisoformat(dt_str).strftime("%d.%m %H:%M")
    except Exception:
        return dt_str


from datetime import datetime
