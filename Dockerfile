# ── Stage 1: builder — скачиваем зависимости и модель ──────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Системные зависимости для sentence-transformers и chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Кэш embedding-модели кладём внутрь образа (не в ~/.cache)
ENV HF_HOME=/app/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence_transformers

# Скачиваем модель (~120MB) — один раз при build
RUN python src/scripts/download_models.py

# Строим RAG-индекс из Markdown KB
RUN python src/scripts/build_index.py


# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Node.js + npx — для Airbnb MCP-сервера (@openbnb/mcp-server-airbnb)
# curl — для установки uv
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
        curl \
    && npm install -g npx \
    && rm -rf /var/lib/apt/lists/*

# uv — для Aviasales MCP-сервера (flights-mcp)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Копируем установленные Python-пакеты из builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Копируем проект + кэш модели + готовый RAG-индекс
COPY --from=builder /app /app

# Переменные окружения
ENV HF_HOME=/app/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence_transformers
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Логи пишем в volume (см. docker-compose.yml)
VOLUME ["/app/logs"]

# Интерактивный CLI
CMD ["python", "main.py"]
