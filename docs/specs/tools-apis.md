# Spec: Tools / APIs

Спецификация MCP-инструментов и внешних API-интеграций.

**Принцип:** все инструменты — read-only. Write-операции недоступны ни в PoC, ни в production-контракте.

> **PoC-реализация:** в демо-версии инструменты реализованы как Python-функции с JSON-фикстурами, соблюдающие MCP-совместимый контракт. При переходе к production функции заменяются на реальные MCP-серверы без изменений в агентской логике.

---

## Инструменты

### `search_flights`

**Назначение:** Поиск авиабилетов по заданным параметрам.

**Вызывается из:** SearchAgent (параллельно с `search_hotels`)

**Контракт:**

```python
# Вход
SearchFlightsParams(
    origin: str,           # IATA-код или название города, required
    destination: str,      # IATA-код или название города, required
    departure_date: str,   # "YYYY-MM-DD", required
    return_date: str,      # "YYYY-MM-DD", required (round-trip)
    passengers: int,       # 1–9, required
    max_price: float,      # total для всех пассажиров, optional
    currency: str,         # "EUR" | "RUB" | "USD", default "EUR"
)

# Выход
FlightSearchResult(
    flights: List[Flight],  # top-5 по цене, может быть пустым
    search_id: str,
    cached_at: str,         # ISO8601
)

Flight(
    id: str,
    origin: str,
    destination: str,
    departure: str,         # ISO8601
    arrival: str,           # ISO8601
    price: float,
    currency: str,
    airline: str,
    stops: int,             # 0 = прямой
    connection_time_min: int | None,   # None если stops=0
    source: str,            # "mock_flights"
)
```

**Ошибки:**

| Код | Ситуация | Поведение SearchAgent |
|---|---|---|
| `TIMEOUT` | Ответ не получен за 5 сек | Retry ×1, затем degraded mode |
| `NO_RESULTS` | Рейсов не найдено | Предложение ±3 дня |
| `INVALID_PARAMS` | Некорректные даты / IATA | Лог ошибки, сообщение пользователю |
| `SERVICE_UNAVAILABLE` | API недоступен | Retry ×1, затем degraded mode |

**Timeout:** 5 секунд
**Side effects:** нет (read-only; mock не изменяет состояние, MCP production — только GET)

---

### `search_hotels`

**Назначение:** Поиск отелей по городу и датам.

**Вызывается из:** SearchAgent (параллельно с `search_flights`)

**Контракт:**

```python
# Вход
SearchHotelsParams(
    city: str,             # название города, required
    checkin: str,          # "YYYY-MM-DD", required
    checkout: str,         # "YYYY-MM-DD", required
    guests: int,           # required
    max_price_per_night: float | None,   # optional
    currency: str,         # default "EUR"
)

# Выход
HotelSearchResult(
    hotels: List[Hotel],   # top-5 по рейтингу×цене
    search_id: str,
)

Hotel(
    id: str,
    name: str,
    type: str,             # "hotel"
    city: str,
    price_per_night: float,
    total_price: float,    # price_per_night × nights
    currency: str,
    rating: float,         # 1.0–5.0
    amenities: List[str],
    source: str,           # "mock_booking"
)
```

**Ошибки:**

| Код | Ситуация | Поведение SearchAgent |
|---|---|---|
| `TIMEOUT` | Ответ не получен за 5 сек | Retry ×1, затем failover на `search_rentals` |
| `NO_RESULTS` | Отелей нет | Failover на `search_rentals` |
| `SERVICE_UNAVAILABLE` | API недоступен | Retry ×1, затем failover |

**Failover:** при любой ошибке после retry → `search_rentals`
**Timeout:** 5 секунд
**Side effects:** нет

---

### `search_rentals`

**Назначение:** Поиск аренды (Airbnb-like). Используется как failover для `search_hotels`.

**Вызывается из:** SearchAgent (только при failover)

**Контракт:**

```python
# Вход — аналогично search_hotels + max_total_price вместо per_night
SearchRentalsParams(
    city: str,
    checkin: str,
    checkout: str,
    guests: int,
    max_total_price: float | None,
    currency: str,
)

# Выход
RentalSearchResult(
    rentals: List[Rental],
    search_id: str,
)

Rental(
    id: str,
    name: str,
    type: str,             # "apartment" | "house" | "room"
    city: str,
    price_per_night: float,
    total_price: float,
    currency: str,
    rating: float,
    max_guests: int,
    source: str,           # "mock_airbnb"
)
```

**Ошибки:**

| Код | Ситуация | Поведение |
|---|---|---|
| `TIMEOUT` / `SERVICE_UNAVAILABLE` | API недоступен | Degraded mode: только рейсы + уведомление пользователю |

**Timeout:** 5 секунд

---

## Политика вызовов

### Параллельность

```python
# SearchAgent запускает параллельно:
async def search(trip_profile):
    flights_task = asyncio.create_task(search_flights(params))
    hotels_task  = asyncio.create_task(search_hotels(params))
    flights, hotels = await asyncio.gather(
        flights_task, hotels_task, return_exceptions=True
    )
```

### Retry

```
Вызов → Timeout/Error
    └─ Retry ×1 (немедленно, без sleep)
           ├─ Success → продолжаем
           └─ Error → Failover / Degraded mode
```

**Нет exponential backoff в PoC** — одна немедленная повторная попытка.

### Нормализация ответов

SearchAgent нормализует все ответы в единый формат `SearchResults` независимо от источника:
- `source` поле идентифицирует происхождение
- Цены приводятся к единой валюте сессии
- Пустые поля заполняются `None`, не удаляются

---

## Защита

| Мера | Реализация |
|---|---|
| Read-only enforcement | MCP-контракт: только read-операции; в PoC функции не имеют write-логики |
| Params без PII | SearchAgent не передаёт имена пользователей, контакты в params |
| Санитизация ответов | Все строки из API-ответов проходят через injection filter перед логированием и инжектом в промпт |
| Логирование params | tool_name + params без PII + status (не raw response) |

### Injection filter (применяется к строковым полям ответов API)

```python
INJECTION_PATTERNS = [
    r"ignore\s+(previous|all)\s+instructions?",
    r"you\s+are\s+now",
    r"new\s+instructions?",
    r"disregard\s+",
    r"system\s*:",
    r"<\s*/?system\s*>",
]
```

---

## PoC-реализация MCP-инструментов

Инструменты реализованы как Python-функции с единым MCP-совместимым интерфейсом. Каждая функция принимает типизированный params-объект и возвращает структурированный result — тот же контракт, что и реальный MCP-сервер. В PoC данные берутся из JSON-фикстур:

```
data/mocks/
├── flights_tyumen.json
├── flights_barcelona.json
├── hotels_tyumen.json
├── hotels_barcelona.json
└── ...
```

Функция возвращает данные соответствующего города или `NO_RESULTS` если фикстура отсутствует.
Для демо симуляция сбоев MCP-сервера: env-флаг `MOCK_BOOKING_FAIL=true` заставляет `search_hotels` возвращать `SERVICE_UNAVAILABLE`.

**Путь к production:** заменить тело функции на вызов реального MCP-сервера. Контракт (params, result, error codes) остаётся неизменным — агентская логика не затрагивается.
