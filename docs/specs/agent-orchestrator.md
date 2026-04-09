# Spec: Agent / Orchestrator

Спецификация Orchestrator и каждого агента: шаги, правила переходов, stop conditions, retry и fallback.

---

## Orchestrator

### Роль

Единственный управляющий компонент. Не принимает решений по содержанию — только управляет потоком: какой агент вызвать, в каком порядке, что делать при ошибке.

### Алгоритм главного цикла

```python
async def run_session(session: SessionState, user_message: str):
    while True:
        # 0. Проверка лимитов
        if limit_exceeded := check_limits(session):
            return circuit_break(session, reason=limit_exceeded)

        # 1. PII Guard — всегда первый
        clean_message = pii_guard.sanitize(user_message, session.pii_token_map)

        # 2. Добавить в историю
        session.dialog_history.append(Message(role="user", content=clean_message))
        trim_history(session)

        # 3. Определить следующий шаг
        step = router.next_step(session)

        # 4. Выполнить шаг
        result = await execute_step(step, session, clean_message)

        # 5. Если нужен ответ пользователю — вернуть и ждать следующего сообщения
        if result.needs_user_input:
            session.turn_count += 1
            return result.message

        # 6. Если сессия завершена — вернуть финальный план
        if result.is_final:
            return finalize_session(session, result)
```

### Правила переходов (AgentRouter)

| Текущий шаг | Условие | Следующий шаг |
|---|---|---|
| `INTENT` | TripProfile неполный | `INTENT` (уточняющий вопрос → пользователь) |
| `INTENT` | TripProfile заполнен | `BUDGET_CHECK_EARLY` |
| `BUDGET_CHECK_EARLY` | budget нереалистичен | `INTENT` (сообщение → пользователь) |
| `BUDGET_CHECK_EARLY` | budget OK | `SEARCH` |
| `SEARCH` | destination изменился | `SEARCH` (сброс результатов) |
| `SEARCH` | результаты получены | `OPTIMIZE` |
| `SEARCH` | оба API недоступны | `ERROR` |
| `OPTIMIZE` | пакет собран | `BUDGET_CHECK_FINAL` |
| `BUDGET_CHECK_FINAL` | превышение ≤ 10% | `HITL_CONFIRM` |
| `BUDGET_CHECK_FINAL` | превышение > 10% | `HITL_BUDGET` |
| `HITL_BUDGET` | пользователь → другой вариант | `SEARCH` |
| `HITL_BUDGET` | пользователь → подтвердил | `ITINERARY` |
| `HITL_CONFIRM` | пользователь → другой вариант | `SEARCH` |
| `HITL_CONFIRM` | пользователь → сменил город | `SEARCH` (partial reset) |
| `HITL_CONFIRM` | пользователь → подтвердил | `ITINERARY` |
| `ITINERARY` | план построен | `REPORT` |
| `REPORT` | отформатировано | `DONE` |

### Stop conditions

| Условие | Тип | Поведение |
|---|---|---|
| `agent_step == "DONE"` | Success | Возврат FinalPlan пользователю |
| `llm_call_count >= 30` | Circuit breaker | Сообщение о лимите, очистка PII |
| `session_age > 30 мин` | Timeout | Сессия завершается, очистка PII |
| `agent_step == "ERROR"` | Fatal error | Graceful degradation message |

---

## IntentAgent

**Тип:** LLM-агент

**Входные данные:** sanitized user message + dialog history + существующий TripProfile (если есть)

**Системный промпт (ключевые инструкции):**
- Роль: ассистент для планирования поездок
- Задача: извлечь или уточнить все поля TripProfile
- Правило: задавать максимум 1–2 уточняющих вопроса за раз
- Правило: не начинать поиск самостоятельно

**Обязательные поля TripProfile для перехода:**

```python
REQUIRED_FIELDS = ["destination", "origin", "departure_date", "return_date", "travelers", "budget", "currency"]
```

**Выход:** обновлённый TripProfile JSON + сообщение пользователю (если нужны уточнения)

**Retry/Fallback:**
- Если LLM вернул невалидный JSON → повторный вызов с просьбой переформатировать (1 retry)
- Если retry не помог → сессия переходит в `ERROR`

**LLM-вызовы:** 1–3 (в зависимости от количества уточняющих шагов)

---

## SearchAgent

**Тип:** Tool-агент (без LLM-вызовов)

**Входные данные:** TripProfile из SessionState

**Алгоритм:**

```python
async def search(trip_profile: TripProfile) -> SearchResults:
    # 1. Параллельный запуск
    flights_result, hotels_result = await asyncio.gather(
        call_with_retry(search_flights, build_flight_params(trip_profile)),
        call_with_retry(search_hotels, build_hotel_params(trip_profile)),
        return_exceptions=True
    )

    # 2. Обработка ошибок жилья
    if isinstance(hotels_result, Exception):
        hotels_result = await call_with_retry(search_rentals, build_rental_params(trip_profile))

    # 3. Проверка рейсов на точные даты
    if not flights_result.flights:
        flights_result = await search_flights_flexible(trip_profile, delta_days=3)

    # 4. Нормализация
    return normalize(flights_result, hotels_result)
```

**Retry:** 1 попытка, немедленно, без backoff

**Fallback цепочка:**
```
search_hotels → (fail) → search_rentals → (fail) → Degraded mode (только рейсы)
search_flights → (fail) → ERROR (нет рейсов — нет смысла продолжать)
```

**LLM-вызовы:** 0

---

## OptimizationAgent

**Тип:** Deterministic (без LLM)

**Входные данные:** SearchResults + TripProfile (budget, preferences)

**Обоснование отказа от LLM:** mock-данные не содержат реального контекста района, качества окружения или деталей, необходимых для осмысленного LLM-ранжирования. Детерминированная формула надёжнее и быстрее.

**Алгоритм:**

```python
def optimize(search_results: SearchResults, trip_profile: TripProfile) -> Package:
    # 1. Фильтрация по бюджету
    valid_combos = [
        (f, h) for f in search_results.flights
                for h in search_results.accommodations
                if f.price + h.total_price <= trip_profile.budget
    ]

    if not valid_combos:
        # Смягчаем фильтр: берём минимальную суммарную стоимость
        valid_combos = get_cheapest_combo(search_results)

    # 2. Взвешенная формула: 60% цена, 40% рейтинг жилья
    def score(f, h) -> float:
        max_price = max(f.price + h.total_price for f, h in valid_combos)
        price_norm = 1 - (f.price + h.total_price) / max_price  # 0..1, меньше цена = лучше
        rating_norm = h.rating / 5.0                            # 0..1
        return price_norm * 0.6 + rating_norm * 0.4

    winner_f, winner_h = max(valid_combos, key=lambda c: score(*c))
    total = winner_f.price + winner_h.total_price
    return Package(
        flight=winner_f,
        accommodation=winner_h,
        total_cost=total,
        budget_remaining=trip_profile.budget - total,
    )
```

**LLM-вызовы:** 0

**Fallback:** если valid_combos пуст (бюджет не покрывает ни одну комбинацию) → берём минимально дорогой вариант из всех и помечаем в BudgetState статус `exceeded`

---

## BudgetTracker

**Тип:** Deterministic (без LLM)

**Ранняя проверка (до поиска):**

```python
# Минимальные рыночные цены — ориентир для ранней проверки бюджета
MIN_MARKET_PRICES = {
    "EUR": {"flight_per_person": 80, "accommodation_per_night": 40},
    "USD": {"flight_per_person": 90, "accommodation_per_night": 45},
    "RUB": {"flight_per_person": 5000, "accommodation_per_night": 2500},
}

def early_check(trip_profile: TripProfile) -> BudgetCheckResult:
    currency = trip_profile.currency.upper()
    prices = MIN_MARKET_PRICES.get(currency, MIN_MARKET_PRICES["EUR"])
    # round trip: × 2 на перелёт
    min_flight = prices["flight_per_person"] * trip_profile.travelers * 2
    nights = (trip_profile.return_date - trip_profile.departure_date).days
    min_hotel = prices["accommodation_per_night"] * nights
    min_total = min_flight + min_hotel

    # Предупреждаем если бюджет < 80% от минимума (фактор 0.8 = достаточный запас)
    if trip_profile.budget < min_total * 0.8:
        return BudgetCheckResult(ok=False, min_estimate=min_total)
    return BudgetCheckResult(ok=True)
```

**Финальная проверка (после оптимизации):**

```python
def final_check(package: Package, budget: float) -> BudgetState:
    overage_pct = (package.total_cost - budget) / budget * 100
    status = "ok" if overage_pct <= 0 else ("warning" if overage_pct <= 10 else "exceeded")
    return BudgetState(spent=package.total_cost, remaining=budget - package.total_cost,
                       overage_pct=max(0, overage_pct), status=status)
```

**LLM-вызовы:** 0

---

## ItineraryAgent

**Тип:** LLM + RAG

**Входные данные:** SelectedPackage + RAG chunks (max 1500 токенов)

**Структура промпта:**
```
[SYSTEM] Ты travel-планировщик. Построй day-by-day итинерарий.
         Используй данные из <external_data> для конкретных рекомендаций.
         Всегда добавляй disclaimer: "Маршрут носит рекомендательный характер."

[TRIP]   {SelectedPackage JSON}

<external_data>
{RAG chunks}
</external_data>

[USER]   Построй план на {N} дней для {destination}.
```

**Выход:** Itinerary JSON с полями `days: List[DayPlan]`, `sources: List[str]`

**LLM-вызовы:** 1

**Fallback:** если RAG пустой → LLM генерирует план без конкретики + усиленный disclaimer

---

## ReportFormatter

**Тип:** Deterministic (шаблон, без LLM)

**Входные данные:** SelectedPackage + Itinerary + BudgetState

**Шаблон вывода:**

```markdown
## ✈️ Ваш план поездки

**Рейс:** {origin} → {destination}, {departure}, {airline}
**Жильё:** {accommodation.name}, {nights} ночей — {accommodation.total_price} {currency}
**Итого:** {total_cost} {currency} (остаток бюджета: {remaining})

---

### День 1: {date}
{day_plan}

### День 2: {date}
...

---
> ⚠️ Маршрут носит рекомендательный характер. Информация о часах работы может устареть.
> Источники: {sources}
```

**LLM-вызовы:** 0

---

## Retry и Fallback — сводная таблица

| Агент | Что может упасть | Retry | Fallback |
|---|---|---|---|
| IntentAgent | LLM некорректный JSON | 1 retry с prompt correction | ERROR |
| SearchAgent / flights | MCP timeout | 1 retry | ERROR (нет рейсов) |
| SearchAgent / hotels | MCP timeout | 1 retry → failover Airbnb | Degraded (только рейсы) |
| OptimizationAgent | Пустой valid_combos | нет retry | Взять минимально дорогую комбинацию, статус exceeded |
| ItineraryAgent | RAG нет результатов | нет retry | Без RAG + disclaimer |
| ItineraryAgent | LLM ошибка | 1 retry | ERROR |
| BudgetTracker | — | — | Нет (deterministic) |
| ReportFormatter | — | — | Нет (deterministic) |
