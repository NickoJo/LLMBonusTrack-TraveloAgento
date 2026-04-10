.PHONY: setup install model index run test demo demo-failover demo-degraded clean help docker-build docker-run docker-clean

# ─── Переменные ────────────────────────────────────────────
PYTHON   := python3
SRC      := src
PYTEST   := $(PYTHON) -m pytest

# ─── По умолчанию ──────────────────────────────────────────
help:
	@echo ""
	@echo "  TraveloAgento — команды управления"
	@echo ""
	@echo "  Первый запуск:"
	@echo "    make setup      — полная подготовка (install + model + index)"
	@echo ""
	@echo "  По шагам:"
	@echo "    make install    — установить зависимости"
	@echo "    make model      — скачать embedding-модель (~120 MB)"
	@echo "    make index      — построить RAG-индекс из Travel KB"
	@echo ""
	@echo "  Запуск:"
	@echo "    make run        — запустить чат-ассистента"
	@echo ""
	@echo "  Тесты:"
	@echo "    make test       — запустить все eval-тесты"
	@echo ""
	@echo "  Demo-режимы:"
	@echo "    make demo             — стандартный запуск"
	@echo "    make demo-failover    — отель недоступен → failover на Airbnb"
	@echo "    make demo-degraded    — оба источника отелей недоступны"
	@echo ""
	@echo "  Docker:"
	@echo "    make docker-build — собрать образ (модель + индекс внутри, ~5 мин)"
	@echo "    make docker-run   — запустить контейнер (нужен .env)"
	@echo "    make docker-clean — удалить образ и контейнеры"
	@echo ""
	@echo "  Утилиты:"
	@echo "    make clean      — удалить кэш и логи"
	@echo ""

# ─── Подготовка ────────────────────────────────────────────
setup: install model index
	@echo ""
	@echo "✅ Готово. Запустите: make run"
	@echo ""

install:
	@echo "📦 Устанавливаем зависимости..."
	$(PYTHON) -m pip install -r requirements.txt
	@echo "✅ Зависимости установлены"

model:
	@echo "🤖 Скачиваем embedding-модель..."
	$(PYTHON) $(SRC)/scripts/download_models.py
	@echo "✅ Модель готова"

index:
	@echo "🗂  Строим RAG-индекс..."
	$(PYTHON) $(SRC)/scripts/build_index.py
	@echo "✅ Индекс построен"

# ─── Запуск ────────────────────────────────────────────────
run:
	@if [ ! -f .env ]; then \
		echo "❌ Файл .env не найден. Выполните:"; \
		echo "   cp .env.example .env"; \
		echo "   и вставьте DEEPSEEK_API_KEY"; \
		exit 1; \
	fi
	$(PYTHON) main.py

# ─── Тесты ─────────────────────────────────────────────────
test:
	@echo "🧪 Запускаем eval-тесты..."
	$(PYTEST) tests/evals/ -v

# ─── Demo-режимы ───────────────────────────────────────────
demo: run

demo-failover:
	@echo "🔀 Demo: отель недоступен → failover на Airbnb"
	MOCK_BOOKING_FAIL=true $(PYTHON) main.py

demo-degraded:
	@echo "⚠️  Demo: оба источника отелей недоступны → только рейс"
	MOCK_BOOKING_FAIL=true MOCK_AIRBNB_FAIL=true $(PYTHON) main.py

# ─── Docker ────────────────────────────────────────────────
docker-build:
	@echo "🐳 Собираем Docker-образ (модель + индекс внутри)..."
	docker build -t travelo-agento .
	@echo "✅ Образ travelo-agento готов"

docker-run:
	@if [ ! -f .env ]; then \
		echo "❌ Файл .env не найден. Выполните: cp .env.example .env"; \
		exit 1; \
	fi
	docker compose run --rm travelo

docker-clean:
	@echo "🧹 Удаляем образ и контейнеры..."
	docker compose down --rmi local 2>/dev/null || true
	@echo "✅ Готово"

# ─── Утилиты ───────────────────────────────────────────────
clean:
	@echo "🧹 Чистим кэш и логи..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f logs/*.jsonl
	@echo "✅ Готово"
