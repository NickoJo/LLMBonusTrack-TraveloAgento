# Spec: Observability / Evals

Что и как измеряется в системе: метрики, логи, трейсы и автоматические проверки.

---

## Логирование

### Формат

Все события логируются в JSONL (одна JSON-строка = одно событие). Без PII.

```json
{
  "timestamp": "2026-04-09T14:23:01Z",
  "session_id": "sess_abc123",
  "event": "agent_call",
  "agent": "SearchAgent",
  "duration_ms": 1842,
  "status": "success",
  "meta": {}
}
```

### Типы событий и их поля

| Event | Обязательные поля | Meta поля |
|---|---|---|
| `session_start` | timestamp, session_id | interface (cli/streamlit) |
| `session_end` | timestamp, session_id, status | total_turns, total_llm_calls, total_duration_ms |
| `agent_call` | timestamp, session_id, agent, duration_ms, status | step, retry_count |
| `llm_call` | timestamp, session_id, agent, duration_ms, status | model, input_tokens, output_tokens |
| `tool_call` | timestamp, session_id, tool, duration_ms, status | params_hash (не сами params), results_count |
| `pii_detected` | timestamp, session_id | pii_type (name/email/phone), action=masked |
| `hitl_checkpoint` | timestamp, session_id | checkpoint_type, user_decision |
| `budget_check` | timestamp, session_id | check_type (early/final), status (ok/warning/exceeded) |
| `failover_event` | timestamp, session_id | from_service, to_service, reason |
| `rag_query` | timestamp, session_id | destination, results_count, top_score, used_fallback |
| `circuit_break` | timestamp, session_id | reason, llm_calls_used |
| `error` | timestamp, session_id, agent | error_type, message, stack_trace |

### Правила PII-безопасности в логах

```python
NEVER_LOG = [
    "user_message_raw",      # только sanitized версия
    "pii_token_map",         # никогда
    "trip_profile.traveler_names",  # если добавят в будущем
]

# params_hash для tool_call — sha256 от params без PII полей
def safe_params_hash(params: dict) -> str:
    safe = {k: v for k, v in params.items() if k not in PII_FIELDS}
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()[:8]
```

---

## Метрики

### Продуктовые метрики (измеряются по логам сессий)

| Метрика | Формула | Target |
|---|---|---|
| Plan completeness | доля сессий, завершившихся FinalPlan | 100% |
| Turns to completion | среднее кол-во сообщений до финального плана | ≤ 10 |
| Budget accuracy | доля планов, где total_cost ≤ budget × 1.10 | ≥ 90% |
| User correction rate | доля сессий, где пользователь менял параметры после HITL_CONFIRM | < 30% |

### Агентские метрики

| Метрика | Как измеряется | Target |
|---|---|---|
| IntentAgent accuracy | Доля сессий, где TripProfile заполнен с ≤ 2 уточнениями | ≥ 90% |
| Search result relevance | Доля результатов, прошедших бюджетный фильтр | ≥ 80% |
| Fallback trigger rate | Доля failover при симулированных падениях API | 100% |
| RAG hit rate | Доля запросов с score < threshold (fallback без RAG) | < 20% |
| Edge case handling | Доля edge-кейсов из test suite без краша | 100% |

### Технические метрики (из логов `llm_call`, `tool_call`)

| Метрика | Как измеряется | Target |
|---|---|---|
| p95 latency / шаг агента | duration_ms по event=agent_call | ≤ 8 000 ms |
| p95 latency / полный план | total_duration_ms по event=session_end | ≤ 30 000 ms |
| p95 latency / MCP tool call | duration_ms по event=tool_call | ≤ 5 000 ms |
| LLM input tokens / сессия | сумма input_tokens по session_id | мониторинг (нет hard limit) |
| PII incidents | count(event=pii_detected where action≠masked) | 0 |

---

## Трейсы

В PoC полноценный distributed tracing не используется. Вместо него — `session_id` как correlation ID, пронизывающий все события одной сессии.

### Реконструкция трейса сессии

```bash
# Все события сессии в хронологическом порядке
grep '"session_id": "sess_abc123"' logs/travelo.jsonl | jq -s 'sort_by(.timestamp)'
```

### Линейный трейс успешной сессии (пример)

```
session_start
  └─ agent_call: IntentAgent (success, 2100ms)
       └─ llm_call: claude-sonnet-4-6 (in=450, out=120, 2050ms)
  └─ agent_call: BudgetTracker early (success, 2ms)
  └─ agent_call: SearchAgent (success, 3200ms)
       └─ tool_call: search_flights (success, 1800ms, results=5)
       └─ tool_call: search_hotels (success, 2100ms, results=5)
  └─ agent_call: OptimizationAgent (success, 1900ms)
       └─ llm_call: claude-sonnet-4-6 (in=800, out=80, 1850ms)
  └─ agent_call: BudgetTracker final (success, 1ms)
  └─ hitl_checkpoint: package_confirm (user_decision=confirmed)
  └─ rag_query (results=5, top_score=0.28)
  └─ agent_call: ItineraryAgent (success, 3100ms)
       └─ llm_call: claude-sonnet-4-6 (in=1200, out=600, 3050ms)
  └─ agent_call: ReportFormatter (success, 12ms)
session_end (status=completed, turns=4, llm_calls=3, total=11500ms)
```

---

## Evals — тест-сьют

Набор автоматических проверок, которые запускаются перед демо.

### Запуск

```bash
python -m pytest tests/evals/ -v
```

### Категории тестов

#### 1. Happy path eval

```python
# tests/evals/test_happy_path.py
def test_tyumen_5_days():
    """Полный план за ≤ 10 сообщений, бюджет не превышен"""
    session = run_session("Хочу слетать в Тюмень на 5 дней в мае, нас двое, бюджет 150000 рублей")
    assert session.status == "completed"
    assert session.turn_count <= 10
    assert session.budget_state.status in ("ok", "warning")
    assert session.itinerary is not None
    assert session.selected_package is not None
```

#### 2. Edge case evals

```python
# tests/evals/test_edge_cases.py

def test_unrealistic_budget():
    """Нереалистичный бюджет — осмысленный ответ, не краш"""
    session = run_session("Хочу в Лондон на неделю, бюджет 200 евро на двоих")
    assert session.status != "error"
    assert "бюджет" in session.last_message.lower()

def test_booking_api_down():
    """Booking недоступен → failover на Airbnb"""
    with mock_env(MOCK_BOOKING_FAIL="true"):
        session = run_session(STANDARD_REQUEST)
    assert any(e["event"] == "failover_event" for e in session.events)
    assert session.status == "completed"

def test_both_accommodation_apis_down():
    """Оба сервиса жилья недоступны → degraded mode"""
    with mock_env(MOCK_BOOKING_FAIL="true", MOCK_AIRBNB_FAIL="true"):
        session = run_session(STANDARD_REQUEST)
    assert session.status != "error"   # не краш
    assert "жилья" in session.last_message.lower()  # объяснение

def test_city_change_mid_dialog():
    """Смена города → сброс поиска, сохранение дат и бюджета"""
    session = run_multiturn_session([
        "Хочу в Тюмень на 5 дней, бюджет 100000 рублей",
        "Нет, лучше в Казань"
    ])
    assert session.trip_profile.destination == "Казань"
    assert session.trip_profile.budget == 100000   # бюджет сохранён

def test_open_ended_request():
    """Открытый запрос → уточняющие вопросы, не случайный результат"""
    session = run_session("Хочу куда-нибудь отдохнуть")
    assert session.agent_step == "INTENT"   # ждёт уточнений
    assert "?" in session.last_message      # задаёт вопрос

def test_short_connection():
    """Рейс с коротой стыковкой → предупреждение видно"""
    session = run_session_with_mock_flight(connection_time_min=30)
    assert "short_connection" in session.risk_flags
    assert "стыков" in session.last_message.lower()

def test_circuit_breaker():
    """30 LLM-вызовов → circuit breaker, graceful завершение"""
    session = exhaust_llm_calls()
    assert session.status == "error"
    assert session.pii_token_map == {}   # PII очищен
```

#### 3. PII safety evals

```python
# tests/evals/test_pii_safety.py

def test_pii_not_in_logs():
    """PII не попадает в логи"""
    run_session("Меня зовут Иван Петров, хочу в Тюмень")
    logs = read_log_file()
    assert "Иван Петров" not in str(logs)
    assert "ivan" not in str(logs).lower()

def test_pii_not_in_llm_prompt():
    """PII не передаётся в LLM"""
    captured_prompts = []
    with capture_llm_calls(captured_prompts):
        run_session("email: user@example.com, хочу в Тюмень")
    assert "user@example.com" not in str(captured_prompts)

def test_pii_cleared_after_session():
    """PII token map очищается после сессии"""
    session = run_session("Телефон +7-999-123-45-67, хочу в Тюмень")
    assert session.pii_token_map == {}
```

#### 4. Latency evals

```python
# tests/evals/test_latency.py

def test_full_plan_under_30s():
    """End-to-end за ≤ 30 секунд"""
    start = time.time()
    session = run_session(STANDARD_REQUEST)
    elapsed = time.time() - start
    assert elapsed < 30

def test_single_agent_step_under_8s():
    """Каждый шаг агента ≤ 8 секунд"""
    session = run_session(STANDARD_REQUEST)
    agent_calls = [e for e in session.events if e["event"] == "agent_call"]
    for call in agent_calls:
        assert call["duration_ms"] < 8000, f"{call['agent']} too slow: {call['duration_ms']}ms"
```

---

## Чеклист перед демо

```
[ ] python -m pytest tests/evals/ -v               # все тесты green
[ ] MOCK_BOOKING_FAIL=true → failover работает
[ ] MOCK_BOOKING_FAIL=true MOCK_AIRBNB_FAIL=true → degraded mode работает
[ ] PII-тест: имя/email не в логах
[ ] Полный happy path < 30 сек (замерить руками)
[ ] logs/travelo.jsonl не содержит PII (grep -i "email\|phone\|@" logs/travelo.jsonl)
[ ] circuit breaker: 30 вызовов → graceful завершение
[ ] Streamlit UI запускается и отображает plan корректно
```
