# System Design — TraveloAgento PoC

> Версия: 1.0 | Дата: 2026-04-09 | Статус: PoC / Demo

---

## 1. Ключевые архитектурные решения

| Решение | Выбор | Обоснование |
|---|---|---|
| Паттерн оркестрации | Centralized Orchestrator + специализированные агенты | Простой control flow, легко добавить retry/failover без распределённой координации |
| LLM vs детерминированная логика | LLM только для NLU, оценки и генерации текста; арифметика/фильтрация — детерминировано | Снижает hallucination surface, экономит токены |
| Внешние интеграции | MCP Tool Layer (mock в PoC, MCP-совместимый контракт) | Read-only интерфейс; в PoC реализован как mock-функции с JSON fixtures — без изменения агентской логики заменяется на реальный MCP-сервер |
| Память | In-process Session State (Python dict) | Достаточно для PoC; нет персистентного хранения PII |
| RAG | Локальная KB с travel-контентом, векторный поиск | Снижает hallucination в ItineraryAgent, источник верифицирован |
| Безопасность | PII Guard middleware перед каждым LLM-вызовом | Принцип: PII никогда не попадает в LLM |
| Интерфейс | CLI или веб-чат (Streamlit/Gradio) | Минимальный UI для демо, фокус на агентской логике |

---

## 2. Модули и их роли

```
User (Chat UI / CLI)
        │
   ┌────▼──────────────────────────────────────────┐
   │                  Orchestrator                  │
   │  · управляет SessionState                     │
   │  · маршрутизирует вызовы агентов              │
   │  · retry / circuit breaker / failover         │
   │  · Human-in-the-Loop checkpoints              │
   │  · лимит: max 30 LLM-вызовов на сессию        │
   └──┬──────────┬──────────┬──────────┬───────────┘
      │          │          │          │
 ┌────▼───┐ ┌───▼────┐ ┌───▼──────┐ ┌─▼──────────┐
 │ Intent │ │ Search │ │Optimiza- │ │ Itinerary  │
 │ Agent  │ │ Agent  │ │  tion    │ │   Agent    │
 │  (LLM) │ │(Tools) │ │  Agent   │ │ (LLM+RAG) │
 └────────┘ └───┬────┘ │  (LLM+  │ └───┬────────┘
                │      │  determ.)│     │
          ┌─────▼────┐ └───┬──────┘ ┌───▼────────┐
          │MCP Layer │     │        │  Travel KB  │
          │· Flights │ ┌───▼──────┐ │  (Vector   │
          │· Booking │ │  Budget  │ │   Store)   │
          │· Airbnb  │ │ Tracker  │ └────────────┘
          └─────┬────┘ │ (determ.)│
                │      └──────────┘
          ┌─────▼──────┐
          │  PII Guard │
          └────────────┘
```

### Таблица модулей

| Модуль | Тип | Роль |
|---|---|---|
| **Orchestrator** | Control layer | Управляет сессией, маршрутизирует между агентами, обрабатывает ошибки и failover |
| **IntentAgent** | LLM | Диалог с пользователем, уточнение параметров, извлечение структурированного `TripProfile` |
| **SearchAgent** | Tool calls | Параллельные MCP-запросы к Flights/Booking/Airbnb, нормализация и фильтрация результатов |
| **OptimizationAgent** | Deterministic | Взвешенная формула (60% цена, 40% рейтинг жилья); без LLM-вызова |
| **BudgetTracker** | Deterministic | Отслеживает суммарную стоимость, сравнивает с бюджетом, генерирует предупреждения |
| **ItineraryAgent** | LLM + RAG | Строит day-by-day план на основе `SelectedPackage` и POI из Travel KB |
| **ReportFormatter** | Deterministic | Рендерит финальный план в Markdown по шаблону |
| **PII Guard** | Middleware | Маскирует PII (email, телефон, карта) regex-паттернами до LLM-вызовов; хранит token map in-memory |
| **MCP Tool Layer** | Integration | Read-only интерфейс к внешним API. В PoC реализован как mock-функции с JSON fixtures; контракт совместим с MCP-протоколом для последующего масштабирования |
| **Travel KB** | RAG store | Верифицированный контент: POI, рестораны, советы по городам; векторный поиск |

---

## 3. Основной Execution Flow

```
[1] User input
      │
      ▼
[2] PII Guard — маскирует PII, возвращает sanitized message
      │
      ▼
[3] IntentAgent (LLM)
      · извлекает TripProfile: {destination, dates, duration,
        travelers, budget, currency, preferences}
      · если поля не заполнены — задаёт уточняющий вопрос → goto [1]
      · валидирует схему (детерминировано)
      │
      ▼
[4] BudgetTracker — ранняя проверка: min рыночная цена vs бюджет
      · если нереалистично — сообщение пользователю → goto [1]
      │
      ▼
[5] SearchAgent (параллельно)
      ├── MCP: search_flights → top-5 рейсов (фильтр: даты, бюджет)
      └── MCP: search_hotels/rentals → top-5 вариантов жилья (фильтр: даты, бюджет)
      · нормализация форматов ответов → SearchResults: JSON
      │
      ▼
[6] OptimizationAgent (deterministic)
      · ранжирует комбинации рейс×жильё по формуле: score = price*0.6 + rating*0.4
      · возвращает SelectedPackage: JSON
      │
      ▼
[7] BudgetTracker — итоговая проверка: стоимость пакета vs бюджет
      · если превышение > 10% — Human-in-the-Loop checkpoint → user confirms
      │
      ▼
[8] Human-in-the-Loop checkpoint #1
      · Orchestrator показывает SelectedPackage пользователю
      · ждёт явного подтверждения перед переходом к итинерарию
      │
      ▼
[9] ItineraryAgent (LLM + RAG)
      · RAG-запрос в Travel KB → POI, рестораны, советы
      · LLM строит day-by-day план
      · явный disclaimer: «маршрут носит рекомендательный характер»
      │
      ▼
[10] ReportFormatter (deterministic)
       · рендерит FinalPlan: Markdown
       · включает: рейс, жильё, итинерарий, бюджет, sources
       │
       ▼
[11] User получает финальный план
```

**Ветки возврата:**
- Пользователь меняет город → Orchestrator сбрасывает SearchResults, сохраняет TripProfile (даты, бюджет) → goto [5]
- Рейсов на точные даты нет → SearchAgent предлагает ±3 дня → goto [6]

---

## 4. State / Memory / Context Handling

### Session State (in-memory, Python dict)

```python
SessionState = {
    "session_id": str,
    "turn_count": int,               # лимит: 10 сообщений к пользователю
    "llm_call_count": int,           # лимит: 30 LLM-вызовов
    "pii_token_map": dict,           # PII → токен (очищается при завершении)
    "trip_profile": TripProfile,     # заполняется IntentAgent
    "search_results": SearchResults, # заполняется SearchAgent, сбрасывается при смене города
    "selected_package": Package,     # заполняется OptimizationAgent
    "budget_state": BudgetState,     # текущий потраченный бюджет
    "itinerary": Itinerary,          # заполняется ItineraryAgent
    "dialog_history": List[Message], # последние ~20 сообщений
    "summary": str,                  # суммаризация старых сообщений (LLM)
}
```

### Управление контекстным окном

- История диалога хранит последние **20 сообщений**
- Более старые сообщения суммаризируются LLM-вызовом и хранятся в `summary`
- `summary` + последние 20 сообщений = контекст для следующего LLM-вызова
- TripProfile и SelectedPackage передаются как структурированные JSON-блоки, не как история

### Жизненный цикл данных

| Данные | Хранение | Очистка |
|---|---|---|
| PII token map | In-memory сессии | Сразу после завершения сессии |
| Dialog history | In-memory сессии | При завершении сессии |
| SearchResults | In-memory сессии | При смене города или завершении |
| Логи (без PII) | Файл / stdout | 30–90 дней |

---

## 5. Retrieval-контур (RAG)

### Архитектура

```
ItineraryAgent запрос
        │
        ▼
[Query Builder] — формирует query из TripProfile:
  "{destination} top attractions restaurants tips"
        │
        ▼
[Vector Store] — локальная коллекция (ChromaDB / FAISS)
  · индексированный Travel KB (POI, рестораны, локальные советы)
  · top-5 чанков по cosine similarity
        │
        ▼
[Context Injector] — вставляет retrieved контент в промпт
  в блоке <external_data> (untrusted zone)
        │
        ▼
[ItineraryAgent LLM] — генерирует план с опорой на KB
```

### Состав Travel KB (PoC)

- Статические JSON/Markdown файлы: топ-50 городов, POI, рестораны
- Источник: верифицированные данные (WikiTravel, Lonely Planet-style)
- Обновление KB: вне scope PoC (статический индекс)

### Guardrails RAG

- Все retrieved данные — в блоке `<external_data>` (prompt injection защита)
- Если KB не возвращает релевантных результатов (score < threshold) — ItineraryAgent генерирует план без RAG и добавляет disclaimer

---

## 6. Tool / API-интеграции

### MCP Layer (все интеграции — read-only)

> **PoC-реализация:** инструменты реализованы как Python-функции с JSON-фикстурами, соблюдающие MCP-совместимый контракт (единый интерфейс вызова, структурированный ответ, коды ошибок). При необходимости заменяются на реальный MCP-сервер без изменения агентской логики.

| Инструмент | Production-источник | PoC-реализация | Параметры вызова |
|---|---|---|---|
| `search_flights` | Skyscanner MCP Server | JSON fixture | origin, destination, date, passengers, max_price |
| `search_hotels` | Booking.com MCP Server | JSON fixture | city, checkin, checkout, guests, max_price_per_night |
| `search_rentals` | Airbnb MCP Server | JSON fixture | city, checkin, checkout, guests, max_total_price |

### Failover схема

```
SearchAgent вызывает search_hotels (Booking MCP) → Timeout/Error
        │
        ▼
Retry 1 раз (timeout 5s)
        │
        ▼ (если снова ошибка)
Failover → search_rentals (Airbnb MCP)
        │
        ▼ (если тоже недоступен)
Degraded mode: только рейсы, уведомление пользователю:
"Сервис поиска жилья временно недоступен. Показываю только варианты рейсов."
```

### Параметры MCP-вызовов

- **Timeout:** 5 секунд на каждый вызов
- **Retry:** 1 попытка перед failover
- **Параллельность:** Flights и Accommodation запускаются одновременно (asyncio)
- **Результаты:** top-5 вариантов от каждого источника, не более

---

## 7. Failure Modes, Fallbacks и Guardrails

### Failure Modes

| Ситуация | Детектор | Поведение |
|---|---|---|
| MCP Tool недоступен | Timeout 5s + ошибка | Retry → Failover → Degraded mode с уведомлением |
| Нереалистичный бюджет | BudgetTracker: min_market_price > budget | Честное сообщение + предложение альтернатив до поиска |
| Рейсов на точные даты нет | SearchAgent: empty results | Предложение ±3 дня с объяснением разницы в цене |
| Стыковка < 50 минут | SearchAgent: connection_time < 50m | Risk-флаг в результатах, предупреждение пользователю |
| Смена города в середине | Orchestrator: destination changed | Сброс SearchResults, сохранение TripProfile (даты, бюджет) |
| Превышение бюджета > 10% | BudgetTracker | HiTL checkpoint: ждёт разрешения пользователя |
| Зацикливание агента | Orchestrator: llm_call_count > 30 | Circuit breaker: завершение сессии с сообщением |
| Prompt injection в API данных | PII Guard + санитизация | Удаление паттернов инъекций из external_data |
| Галлюцинация маршрута | — | Disclaimer + RAG-источники видны пользователю |
| Запрос открыт («куда-нибудь») | IntentAgent: missing required fields | Уточняющие вопросы, не random ответ |

### Guardrails

1. **PII Guard** — regex маскировка перед каждым LLM-вызовом (email, phone, card)
2. **Read-only MCP Tools** — в PoC реализованы как mock-функции; write-операций нет ни в mock, ни в production-контракте
3. **`<external_data>` блок** — все данные из API изолированы от system prompt
4. **Санитизация** — фильтр паттернов инъекций в строках из внешних API
5. **Budget hard check** — BudgetTracker блокирует переход к итинерарию при критическом превышении
6. **LLM call limiter** — max 30 LLM-вызовов на сессию (circuit breaker)
7. **HiTL checkpoints** — обязательное подтверждение перед переходом к итинерарию и при превышении бюджета

---

## 8. Технические и операционные ограничения

### Latency (SLO)

| Операция | Target p95 | Источник задержки |
|---|---|---|
| Один агент-шаг (LLM) | ≤ 8 сек | LLM API latency |
| Параллельный MCP-поиск | ≤ 5 сек | внешний API / mock |
| Полный план (end-to-end) | ≤ 30 сек | сумма шагов + overhead |
| RAG-поиск | ≤ 1 сек | локальный vector store |

### Cost (PoC)

| Параметр | Ограничение |
|---|---|
| LLM вызовы на сессию | max 30 (circuit breaker) |
| Сообщений пользователю | ≤ 10 до финального плана (product target) |
| Токены в контексте | ~20 последних сообщений + summary + TripProfile JSON (~4 500 tok из 64k окна DeepSeek) |
| Модель | deepseek-chat (DeepSeek API, OpenAI-compatible) |

### Reliability

| Параметр | Ограничение |
|---|---|
| Uptime PoC-стенда | ≥ 95% в период демо |
| Одновременных пользователей | до 5 сессий |
| Внешние API | все — mock/sandbox в PoC |
| Персистентность данных | только логи (без PII); session state — in-memory |
| Recovery | при падении сессии — потеря state (restart required) |

### Scope PoC (out-of-scope для демо)

- Реальное бронирование и оплата (требует PCI DSS)
- Визовая поддержка
- Мобильное приложение
- Уведомления об изменении цен
- Более 5 одновременных пользователей
- Persistent storage сессий

---

## 9. Human-in-the-Loop Checkpoints

| Точка | Триггер | Действие системы |
|---|---|---|
| После сборки пакета | SelectedPackage готов | Показывает рейс + жильё + стоимость, ждёт «OK» |
| Превышение бюджета | cost > budget × 1.10 | Останавливается, сообщает сумму превышения, ждёт разрешения |
| Risk-флаг (стыковка < 50 мин) | connection_time < 50m | Выводит предупреждение в ответе, не скрывает |
| Финальный план | Всегда | Помечается как «рекомендация», не как «бронирование» |

---

## 10. Contracts (JSON-схемы ключевых объектов)

### TripProfile

```json
{
  "destination": "string",         // required
  "origin": "string",              // required
  "departure_date": "YYYY-MM-DD",  // required
  "return_date": "YYYY-MM-DD",     // required
  "travelers": 2,                  // required, int
  "budget": 1000,                  // required, float
  "currency": "EUR",               // required
  "accommodation_type": "hotel",   // optional: hotel | rental | any
  "preferences": ["beach", "wifi"] // optional
}
```

### SearchResults

```json
{
  "flights": [
    {
      "id": "string",
      "origin": "string",
      "destination": "string",
      "departure": "ISO8601",
      "arrival": "ISO8601",
      "price": 250.0,
      "currency": "EUR",
      "connection_time_min": 90,
      "source": "mock_flights"
    }
  ],
  "accommodations": [
    {
      "id": "string",
      "name": "string",
      "type": "hotel",
      "price_per_night": 80.0,
      "total_price": 560.0,
      "currency": "EUR",
      "rating": 4.2,
      "source": "mock_booking"
    }
  ]
}
```

### SelectedPackage

```json
{
  "flight": { /* Flight объект */ },
  "accommodation": { /* Accommodation объект */ },
  "total_cost": 810.0,
  "currency": "EUR",
  "budget_remaining": 190.0,
  "optimization_notes": "string"
}
```

### BudgetState

```json
{
  "budget": 1000.0,
  "currency": "EUR",
  "spent": 810.0,
  "remaining": 190.0,
  "overage_pct": 0.0,
  "status": "ok"  // ok | warning | exceeded
}
```
