# Spec: Memory / Context

Спецификация управления состоянием сессии, памятью агентов и бюджетом контекстного окна.

---

## Session State

Единственное хранилище состояния в PoC. Живёт в памяти процесса — один объект на сессию.

### Полная схема

```python
@dataclass
class SessionState:
    # Идентификация
    session_id: str                    # UUID4, генерируется при старте
    created_at: str                    # ISO8601
    status: str                        # "active" | "completed" | "error"

    # Счётчики и лимиты
    turn_count: int = 0                # сообщений пользователю (лимит: 10)
    llm_call_count: int = 0            # LLM-вызовов (лимит: 30)
    agent_step: str = "INTENT"         # текущий шаг state machine

    # Данные поездки (заполняются по ходу)
    trip_profile: TripProfile | None = None
    search_results: SearchResults | None = None
    selected_package: Package | None = None
    budget_state: BudgetState | None = None
    itinerary: Itinerary | None = None

    # Диалог
    dialog_history: List[Message] = field(default_factory=list)
    dialog_summary: str = ""           # суммаризация сообщений старше 20

    # PII (только in-memory, удаляется при завершении)
    pii_token_map: Dict[str, str] = field(default_factory=dict)

    # Контрольные флаги
    hitl_pending: bool = False         # ожидается ли подтверждение пользователя
    risk_flags: List[str] = field(default_factory=list)  # "short_connection", etc.
```

### Жизненный цикл

```
Session.create()
    │
    ├─ Инициализация SessionState
    ├─ Логирование session_start
    │
    ▼ [агентский loop]
    │
    Session.complete() или Session.error()
    │
    ├─ pii_token_map.clear()       ← немедленно
    ├─ dialog_history.clear()      ← немедленно
    ├─ Логирование session_end
    └─ GC собирает объект          ← вся state уходит из памяти
```

**Персистентность:** нет. При перезапуске процесса — сессия потеряна.

---

## Memory Policy

### Что сохраняется и когда сбрасывается

| Поле | Когда заполняется | Когда сбрасывается |
|---|---|---|
| `trip_profile` | IntentAgent шаг 1 | Никогда в рамках сессии |
| `trip_profile.destination` | IntentAgent | При смене города → повторный поиск |
| `search_results` | SearchAgent | При смене города / при повторном поиске |
| `selected_package` | OptimizationAgent | При повторном поиске |
| `budget_state` | BudgetTracker | Обновляется при каждом изменении пакета |
| `itinerary` | ItineraryAgent | При повторном поиске / смене пакета |
| `pii_token_map` | PII Guard | При завершении сессии |
| `dialog_history` | Каждый ответ агента | Скользящее окно: 20 сообщений |
| `dialog_summary` | Оркестратор (при > 20 msg) | Заменяется новой суммаризацией |

### Правило частичного сброса при смене города

```python
def handle_destination_change(state: SessionState, new_destination: str):
    state.trip_profile.destination = new_destination
    state.search_results = None       # сбрасываем результаты поиска
    state.selected_package = None     # сбрасываем выбранный пакет
    state.itinerary = None            # сбрасываем маршрут
    state.budget_state = None         # пересчитаем после нового поиска
    # trip_profile.dates, .budget, .travelers — НЕ сбрасываем
    state.agent_step = "SEARCH"       # возврат к поиску
```

---

## Context Budget (контекстное окно)

### Состав промпта для LLM-вызова

```
┌─────────────────────────────────────────────────────┐
│ SYSTEM PROMPT                              ~500 tok  │
│ (роль агента, правила, guardrails)                   │
├─────────────────────────────────────────────────────┤
│ DIALOG SUMMARY                             ~300 tok  │
│ (суммаризация сообщений старше 20)                   │
├─────────────────────────────────────────────────────┤
│ TRIP PROFILE JSON                          ~200 tok  │
│ (структурированные данные поездки)                   │
├─────────────────────────────────────────────────────┤
│ DIALOG HISTORY (последние 20 сообщений)   ~2000 tok  │
├─────────────────────────────────────────────────────┤
│ AGENT-SPECIFIC DATA                       ~1500 tok  │
│ • SearchResults JSON (для OptimizationAgent)         │
│ • SelectedPackage JSON (для ItineraryAgent)          │
│ • <external_data> RAG chunks (для Itinerary)         │
├─────────────────────────────────────────────────────┤
│ TOTAL (estimate)                          ~4500 tok  │
│ Лимит модели: 200k; в PoC используем ~5% окна       │
└─────────────────────────────────────────────────────┘
```

### Управление размером истории

```python
MAX_HISTORY_MESSAGES = 20
SUMMARY_TRIGGER = 20       # суммаризировать при достижении лимита

def trim_history(state: SessionState, llm_client):
    if len(state.dialog_history) <= MAX_HISTORY_MESSAGES:
        return

    # Сообщения старше 20 → суммаризировать
    old_messages = state.dialog_history[:-MAX_HISTORY_MESSAGES]
    state.dialog_summary = llm_client.summarize(old_messages)
    state.dialog_history = state.dialog_history[-MAX_HISTORY_MESSAGES:]
    state.llm_call_count += 1     # суммаризация тоже считается
```

### Лимиты на размер данных в промпте

| Данные | Лимит | Что происходит при превышении |
|---|---|---|
| SearchResults в промпте | top-5 вариантов каждого типа | Обрезается на уровне SearchAgent |
| RAG chunks | 1 500 токенов суммарно | Обрезается Retriever |
| Dialog summary | 300 токенов | Повторная суммаризация суммаризации |
| TripProfile JSON | ~200 токенов | Не превысит (фиксированная схема) |

---

## Счётчики и stop conditions

```python
LIMITS = {
    "max_llm_calls": 30,        # circuit breaker
    "max_turns": 10,            # рекомендация (не hard stop)
    "max_session_minutes": 30,  # таймаут сессии
}

def check_limits(state: SessionState) -> Optional[str]:
    if state.llm_call_count >= LIMITS["max_llm_calls"]:
        return "LLM call limit exceeded"
    if time_elapsed(state.created_at) > LIMITS["max_session_minutes"] * 60:
        return "Session timeout"
    return None   # лимиты не достигнуты
```

При срабатывании circuit breaker:
1. Сессия переводится в статус `error`
2. Пользователь получает сообщение: "Достигнут лимит запросов. Пожалуйста, начните новую сессию."
3. PII token map очищается
4. Событие логируется

---

## Параллельные сессии

| Параметр | Значение |
|---|---|
| Максимум одновременных сессий | 5 |
| Изоляция | Каждая сессия — отдельный объект в памяти |
| Разделяемые ресурсы | Travel KB (read-only, shared), LLM API client (shared) |
| Конкурентный доступ к KB | Безопасен (только чтение) |
| Конкурентные LLM вызовы | Ограничены rate limit DeepSeek API (не в scope PoC) |
