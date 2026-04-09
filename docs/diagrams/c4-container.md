# C4 Container — TraveloAgento

Как система устроена изнутри: основные исполняемые блоки, их технологии и взаимодействие.

```mermaid
C4Container
    title TraveloAgento — Container Diagram

    Person(user, "Путешественник")

    System_Boundary(travelo, "TraveloAgento") {

        Container(ui, "Chat Interface", "Streamlit / CLI", "Принимает ввод пользователя, отображает ответы агентов и финальный план")

        Container(orchestrator, "Orchestrator", "Python / asyncio", "Управляет сессией и состоянием. Маршрутизирует между агентами. Retry, circuit breaker, HiTL checkpoints")

        Container(agent_core, "Agent Core", "Python", "Специализированные агенты: IntentAgent, SearchAgent, OptimizationAgent, ItineraryAgent, BudgetTracker, ReportFormatter")

        Container(pii_guard, "PII Guard", "Python / regex", "Middleware: маскирует PII (email, телефон, карта) regex-паттернами до LLM-вызовов. Хранит token map in-memory")

        Container(mcp_layer, "MCP Tool Layer", "MCP-совместимый контракт\n(mock в PoC: Python / JSON fixtures)", "Read-only интерфейс к внешним API. В PoC — mock-функции с JSON fixtures. Параллельные вызовы (asyncio), timeout, failover. Env-флаги для симуляции сбоев на демо")

        Container(retriever, "RAG Retriever", "Python / ChromaDB", "Векторный поиск по Travel KB. Возвращает top-5 чанков для ItineraryAgent")

        ContainerDb(session_store, "Session State", "In-memory dict", "TripProfile, SearchResults, SelectedPackage, BudgetState, dialog history, PII token map")

        ContainerDb(travel_kb, "Travel KB", "ChromaDB", "Статический индекс: POI, рестораны, советы по городам (4 направления для демо)")

        ContainerDb(log_store, "Structured Logs", "File / stdout", "Логи без PII: agent calls, tool calls, LLM metrics, errors. Хранение 30–90 дней")
    }

    System_Ext(llm_api, "DeepSeek Chat API", "LLM (deepseek-chat)\nOpenAI-compatible")
    System_Ext(ext_apis, "External APIs", "Flights / Booking / Airbnb\n(mock JSON fixtures в PoC)")

    Rel(user, ui, "Отправляет сообщения, читает план", "Chat / CLI")
    Rel(ui, orchestrator, "Передаёт user input, получает ответ", "Python call")
    Rel(orchestrator, agent_core, "Вызывает агентов по очереди / параллельно", "Python call")
    Rel(orchestrator, session_store, "Читает и пишет SessionState", "in-process")
    Rel(agent_core, pii_guard, "Пропускает все сообщения через PII Guard до LLM", "Python call")
    Rel(pii_guard, llm_api, "Отправляет sanitized промпты", "HTTPS")
    Rel(agent_core, mcp_layer, "SearchAgent вызывает инструменты", "Python call")
    Rel(agent_core, retriever, "ItineraryAgent делает RAG-запрос", "Python call")
    Rel(retriever, travel_kb, "Векторный поиск top-5 чанков", "in-process")
    Rel(mcp_layer, ext_apis, "HTTP-запросы (read-only)", "HTTPS")
    Rel(orchestrator, log_store, "Пишет структурированные логи", "Python logging")

    UpdateLayoutConfig($c4ShapeInRow="4", $c4BoundaryInRow="1")
```

## Технологический стек (PoC)

| Контейнер | Технология | Обоснование |
|---|---|---|
| Chat Interface | Python CLI (`input()` loop) | Минимальный UI для демо, без зависимостей |
| Orchestrator | Python + asyncio | Нативная параллельность для SearchAgent |
| Agent Core | Python + openai SDK | DeepSeek API (OpenAI-compatible) |
| PII Guard | Python regex | Email/phone/card паттерны, без тяжёлых зависимостей |
| MCP Tool Layer | Python + JSON fixtures (PoC) → MCP SDK (production) | MCP-совместимый контракт; mock заменяется на реальный MCP-сервер без изменений в агентах |
| RAG Retriever | ChromaDB + sentence-transformers | Локально, без внешнего сервиса |
| Session State | Python dataclass (in-process) | Достаточно для PoC, нет PII на диске |
| Logs | Python logging → JSONL | Structured, без PII, легко парсить |
