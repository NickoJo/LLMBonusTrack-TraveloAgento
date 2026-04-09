# C4 Context — TraveloAgento

Кто взаимодействует с системой и какие внешние сервисы она использует.

```mermaid
C4Context
    title TraveloAgento — System Context

    Person(user, "Путешественник", "Планирует поездку через чат: задаёт запрос, уточняет детали, получает готовый план")

    System(travelo, "TraveloAgento", "Мультиагентная система планирования путешествий. Ведёт диалог, агрегирует данные, собирает travel-пакет")

    System_Ext(flights_api, "Flights API", "Поиск авиабилетов\n(mock: Skyscanner-like)")
    System_Ext(booking_api, "Booking.com API", "Поиск отелей\n(mock: read-only)")
    System_Ext(airbnb_api, "Airbnb API", "Поиск аренды\n(mock: read-only, failover)")
    System_Ext(llm_api, "DeepSeek Chat API", "LLM для NLU, генерации текста\n(OpenAI-compatible API)")

    Rel(user, travelo, "Отправляет запрос в свободной форме, получает план", "Chat / CLI")
    Rel(travelo, flights_api, "Поиск рейсов по параметрам", "MCP / mock в PoC")
    Rel(travelo, booking_api, "Поиск отелей по параметрам", "MCP / mock в PoC")
    Rel(travelo, airbnb_api, "Failover поиск жилья", "MCP / mock в PoC")
    Rel(travelo, llm_api, "LLM-вызовы: intent, итинерарий", "HTTPS / openai SDK")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

## Границы системы

| Внутри системы | Вне системы |
|---|---|
| Весь агентский слой (Orchestrator, агенты, PII Guard) | Реальные API провайдеров (в PoC — mock) |
| Travel KB и vector store | LLM-провайдер (DeepSeek Chat API) |
| Session State и логи | Платёжные системы (out-of-scope) |
| MCP Tool Layer (mock в PoC) | Визовые и паспортные сервисы (out-of-scope) |
