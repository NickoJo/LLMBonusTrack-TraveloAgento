# Workflow Diagram — Execution Flow

Пошаговое выполнение запроса от user input до финального плана, включая все ветки ошибок и HiTL-точки.

```mermaid
flowchart TD
    A([Пользователь отправляет запрос]) --> B[PII Guard\nмаскирует PII]

    B --> C[IntentAgent\nизвлекает TripProfile]

    C --> D{TripProfile\nполный?}
    D -- "Нет\n(пропущены обязательные поля)" --> E[Задаёт уточняющий вопрос\nпользователю]
    E --> A

    D -- "Да" --> F[BudgetTracker\nранняя проверка бюджета]

    F --> G{Бюджет\nреалистичен?}
    G -- "Нет\nmin_price > budget" --> H[Сообщает о несоответствии\nПредлагает альтернативы]
    H --> A

    G -- "Да" --> I

    subgraph PARALLEL ["SearchAgent — параллельный поиск"]
        I[Запуск параллельно] --> J[search_flights\nMCP Flights API]
        I --> K[search_hotels\nMCP Booking API]
    end

    J --> J1{Flights API\nответил?}
    J1 -- "Timeout / Error" --> J2[Retry ×1]
    J2 --> J3{Успех?}
    J3 -- "Нет" --> J4[❌ Нет рейсов\nDegraded mode]
    J3 -- "Да" --> J5[top-5 рейсов]
    J1 -- "OK" --> J5

    K --> K1{Booking API\nответил?}
    K1 -- "Timeout / Error" --> K2[Failover →\nsearch_rentals\nAirbnb API]
    K2 --> K3{Airbnb\nответил?}
    K3 -- "Нет" --> K4[⚠️ Только рейсы\nуведомление]
    K3 -- "Да" --> K5[top-5 жилья Airbnb]
    K1 -- "OK" --> K5

    J5 --> L{Рейсы на\nточные даты?}
    L -- "Нет" --> L1[Предлагает ±3 дня\nс разницей в цене]
    L1 --> M
    L -- "Да" --> M

    J5 & K5 --> M[OptimizationAgent\nВыбирает лучшую комбинацию\nрейс × жильё]

    M --> N{Стыковка\n< 50 мин?}
    N -- "Да" --> N1[⚠️ Risk-флаг\nпредупреждение пользователю]
    N1 --> O
    N -- "Нет / нет стыковки" --> O

    O[BudgetTracker\nфинальная проверка]

    O --> P{Превышение\n> 10%?}
    P -- "Да" --> P1["🛑 HiTL Checkpoint #1\nПревышение бюджета\nОжидание решения пользователя"]
    P1 --> P2{Пользователь\nрешил?}
    P2 -- "Другой вариант" --> I
    P2 -- "Подтвердил" --> Q
    P -- "Нет" --> Q

    Q["🛑 HiTL Checkpoint #2\nПоказывает SelectedPackage\nРейс + Жильё + Стоимость\nОжидание подтверждения"]

    Q --> R{Пользователь\nподтвердил?}
    R -- "Хочет другой вариант" --> I
    R -- "Сменил город" --> S[Orchestrator: сбрасывает\nSearchResults\nСохраняет даты и бюджет]
    S --> I
    R -- "Подтвердил ✅" --> T

    subgraph RAG ["ItineraryAgent — RAG + LLM"]
        T[RAG-запрос в Travel KB\ndestination POI + рестораны] --> T1[top-5 чанков\nby cosine similarity]
        T1 --> T2{Релевантные\nрезультаты?}
        T2 -- "score < threshold" --> T3[Без RAG +\ndisclaimer]
        T2 -- "OK" --> T4[LLM строит\nday-by-day план]
        T3 --> T4
    end

    T4 --> U[ReportFormatter\nРендерит Markdown\nФлаг: 'рекомендация, не бронирование']

    U --> V([Пользователь получает\nFinalPlan ✅])

    J4 --> ERR[❌ Graceful Error\nОба API недоступны\nСессия завершается]

    style PARALLEL fill:#e8f4f8,stroke:#2196F3
    style RAG fill:#f0f8e8,stroke:#4CAF50
    style P1 fill:#fff3cd,stroke:#ff9800
    style Q fill:#fff3cd,stroke:#ff9800
    style ERR fill:#fde8e8,stroke:#f44336
    style J4 fill:#fde8e8,stroke:#f44336
    style V fill:#e8f8e8,stroke:#4CAF50
```

## Ветки ошибок — сводка

| Ветка | Триггер | Поведение |
|---|---|---|
| Неполный TripProfile | Отсутствует destination / даты / бюджет | Уточняющий вопрос, цикл продолжается |
| Нереалистичный бюджет | min_market_price > budget | Сообщение + предложение альтернатив, не падает |
| Flights API недоступен | Timeout + retry fail | Degraded mode, сессия завершается gracefully |
| Booking недоступен | Timeout + retry fail | Failover на Airbnb, пользователь уведомлён |
| Оба жилья недоступны | Airbnb timeout fail | Только рейсы + уведомление |
| Рейсов на точные даты нет | Empty flight results | Предложение ±3 дня |
| Короткая стыковка | connection_time < 50 мин | Risk-флаг в ответе, не блокирует |
| Превышение бюджета | cost > budget × 1.10 | HiTL stop: пользователь решает |
| Смена города | destination changed в диалоге | Сброс SearchResults, сохранение дат/бюджета |
| Нет RAG-результатов | cosine score < threshold | Plan без RAG + disclaimer |
| Лимит LLM-вызовов | llm_call_count > 30 | Circuit breaker, завершение сессии |
