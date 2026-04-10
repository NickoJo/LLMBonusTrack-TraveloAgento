# Spec: Serving / Config

Запуск PoC, конфигурация, управление секретами, версии моделей и зависимостей.

---

## Структура проекта

```
TraveloAgento/
├── main.py                  # точка входа (CLI или Streamlit)
├── config.py                # загрузка конфигурации из env
├── pyproject.toml           # зависимости (Poetry / pip)
├── .env.example             # шаблон переменных окружения
│
├── agents/
│   ├── orchestrator.py
│   ├── intent_agent.py
│   ├── search_agent.py
│   ├── optimization_agent.py
│   ├── budget_tracker.py
│   ├── itinerary_agent.py
│   └── report_formatter.py
│
├── middleware/
│   └── guardrail.py
│
├── retrieval/
│   ├── retriever.py
│   └── index_builder.py
│
├── tools/
│   ├── flights.py
│   ├── hotels.py
│   └── rentals.py
│
├── models/                  # Pydantic схемы
│   └── schemas.py
│
├── data/
│   ├── mocks/               # JSON-фикстуры для PoC
│   └── travel_kb/           # Markdown контент + ChromaDB индекс
│
└── scripts/
    ├── build_index.py       # скрипт сборки RAG-индекса
    └── download_models.py   # предварительная загрузка embedding модели
```

---

## Конфигурация

Все параметры — через переменные окружения. Файл `.env` для локального запуска.

### Обязательные переменные

| Переменная | Описание | Пример |
|---|---|---|
| `DEEPSEEK_API_KEY` | Ключ DeepSeek API | `sk-...` |

### Опциональные переменные

| Переменная | Default | Описание |
|---|---|---|
| `LLM_MODEL` | `deepseek-chat` | Версия модели |
| `LLM_MAX_TOKENS` | `2048` | Максимум токенов в ответе LLM |
| `LLM_TEMPERATURE` | `0.3` | Temperature для агентов (кроме ItineraryAgent) |
| `ITINERARY_TEMPERATURE` | `0.7` | Temperature для ItineraryAgent (больше вариативности) |
| `MAX_LLM_CALLS` | `30` | Circuit breaker лимит |
| `MAX_SEARCH_RESULTS` | `5` | top-N вариантов от каждого инструмента |
| `TOOL_TIMEOUT_SEC` | `5` | Timeout для mock tool вызовов |
| `RAG_TOP_K` | `5` | Кол-во чанков из Travel KB |
| `RAG_SCORE_THRESHOLD` | `0.6` | Cosine distance порог релевантности |
| `MAX_CONTEXT_MESSAGES` | `20` | Скользящее окно диалога |
| `MAX_CONCURRENT_SESSIONS` | `5` | Лимит параллельных сессий |
| `SESSION_TIMEOUT_MIN` | `30` | Таймаут неактивной сессии |
| `INTERFACE` | `cli` | `cli` или `streamlit` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `LOG_FILE` | `logs/travelo.jsonl` | Путь к лог-файлу (stdout если не задан) |
| `KB_DIR` | `data/travel_kb/cities/` | Путь к KB файлам |
| `KB_INDEX_DIR` | `data/travel_kb/index/` | Путь к ChromaDB индексу |

### Флаги для демо (симуляция ошибок)

| Переменная | Default | Описание |
|---|---|---|
| `MOCK_BOOKING_FAIL` | `false` | Симулировать падение Booking API |
| `MOCK_FLIGHTS_DELAY_SEC` | `0` | Симулировать задержку Flights API |
| `MOCK_AIRBNB_FAIL` | `false` | Симулировать падение Airbnb API |

---

## Файл `.env.example`

```bash
# Обязательно
DEEPSEEK_API_KEY=sk-your-key-here

# Модель
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.3
ITINERARY_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048

# Лимиты
MAX_LLM_CALLS=30
TOOL_TIMEOUT_SEC=5
MAX_CONCURRENT_SESSIONS=5

# RAG
RAG_TOP_K=5
RAG_SCORE_THRESHOLD=0.6

# Логирование
LOG_LEVEL=INFO
LOG_FILE=logs/travelo.jsonl

# Demo flags (для демонстрации failover)
MOCK_BOOKING_FAIL=false
MOCK_AIRBNB_FAIL=false
MOCK_FLIGHTS_DELAY_SEC=0
```

---

## Версии моделей и зависимостей

### LLM

| Параметр | Значение |
|---|---|
| Модель | `deepseek-chat` |
| Провайдер | DeepSeek (OpenAI-compatible API) |
| SDK | `openai` Python SDK (`base_url="https://api.deepseek.com"`) |
| Контекстное окно | 64k токенов |
| Температура (агенты) | 0.3 (детерминированность) |
| Температура (итинерарий) | 0.7 (вариативность текста) |
| Max output tokens | 2 048 |

### Python зависимости (PoC)

```toml
[tool.poetry.dependencies]
python = "^3.11"
openai = "^1.0"                # DeepSeek API (OpenAI-compatible)
chromadb = "^0.5"              # векторное хранилище
sentence-transformers = "^3.0" # embedding модель (multilingual)
pydantic = "^2.0"              # валидация схем
python-dotenv = "^1.0"         # загрузка .env
```

> **Примечание:** spaCy исключён. PII Guard реализован на regex-паттернах — достаточно для PoC и не требует загрузки языковых моделей.

---

## Запуск

### 1. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 2. Загрузка embedding модели (один раз, ~120MB)

```bash
python scripts/download_models.py
# Загружает paraphrase-multilingual-MiniLM-L12-v2 локально
# Обязательно выполнить до демо, требует интернет
```

### 3. Сборка RAG-индекса (один раз)

```bash
python scripts/build_index.py \
  --kb-dir data/travel_kb/cities/ \
  --output data/travel_kb/index/
```

### 4. Запуск

```bash
cp .env.example .env
# отредактировать .env: вставить DEEPSEEK_API_KEY
python main.py
```

### 4. Проверка готовности

```bash
python -c "
from agents.orchestrator import Orchestrator
from retrieval.retriever import Retriever
r = Retriever()
print('KB OK:', r.collection.count(), 'chunks')
"
```

---

## Управление секретами

| Секрет | Хранение | Доступ |
|---|---|---|
| `DEEPSEEK_API_KEY` | `.env` файл (не в git) | Только процесс приложения |
| PII Token Map | In-memory сессии | Не покидает процесс, не логируется |

**Что не хранится на диске:** PII пользователей, dialog history, session state.

**`.gitignore` обязан содержать:**
```
.env
logs/
data/travel_kb/index/   # генерируется локально
```

---

## Ограничения PoC-стенда

| Параметр | Значение |
|---|---|
| Deployment | Локальный процесс / один сервер |
| Масштабирование | Не предусмотрено в PoC |
| HTTPS | Не обязателен для локального демо |
| Auth | Нет (одиночный или небольшой closed-group доступ) |
| Мониторинг | Локальный лог-файл (JSONL) |
| Uptime target | ≥ 95% в период демо |
