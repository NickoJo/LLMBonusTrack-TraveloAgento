#!/usr/bin/env bash
# setup.sh — одна команда для полной подготовки TraveloAgento
# Использование: bash setup.sh

set -e  # прерываться при любой ошибке

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

echo ""
echo -e "${CYAN}══════════════════════════════════════════${RESET}"
echo -e "${CYAN}   TraveloAgento — первоначальная настройка${RESET}"
echo -e "${CYAN}══════════════════════════════════════════${RESET}"
echo ""

# ── 1. Проверка Python ──────────────────────────────────────
echo -e "${CYAN}[1/4] Проверяем Python...${RESET}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ python3 не найден. Установите Python 3.11+${RESET}"
    exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo -e "${RED}❌ Нужен Python 3.11+, обнаружен $PY_VER${RESET}"
    exit 1
fi
echo -e "${GREEN}✅ Python $PY_VER${RESET}"

# ── 2. Зависимости ─────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/4] Устанавливаем зависимости...${RESET}"
python3 -m pip install -r requirements.txt --quiet
echo -e "${GREEN}✅ Зависимости установлены${RESET}"

# ── 3. Файл окружения ──────────────────────────────────────
echo ""
echo -e "${CYAN}[3/4] Настраиваем окружение...${RESET}"
if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}⚠️  Создан файл .env из шаблона.${RESET}"
    echo -e "${YELLOW}   Откройте .env и вставьте DEEPSEEK_API_KEY.${RESET}"
    echo -e "${YELLOW}   Ключ: https://platform.deepseek.com${RESET}"
    echo ""
    echo -e "   Нажмите ${CYAN}Enter${RESET} после того как вставите ключ, или Ctrl+C для выхода..."
    read -r
    # Проверяем что ключ вставлен
    if grep -q "sk-your-key-here" .env; then
        echo -e "${RED}❌ DEEPSEEK_API_KEY не заменён. Отредактируйте .env и запустите снова.${RESET}"
        exit 1
    fi
else
    if grep -q "sk-your-key-here" .env 2>/dev/null; then
        echo -e "${YELLOW}⚠️  DEEPSEEK_API_KEY в .env не заменён. Вставьте реальный ключ.${RESET}"
    else
        echo -e "${GREEN}✅ .env уже настроен${RESET}"
    fi
fi

# ── 4. Модель и индекс ─────────────────────────────────────
echo ""
echo -e "${CYAN}[4/4] Подготовка моделей и индекса...${RESET}"

# Embedding-модель (пропускаем если уже скачана)
echo "   Скачиваем embedding-модель (~120 MB, только первый раз)..."
python3 src/scripts/download_models.py

# RAG-индекс
echo "   Строим RAG-индекс..."
python3 src/scripts/build_index.py

# ── Готово ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════${RESET}"
echo -e "${GREEN}   ✅ Настройка завершена!${RESET}"
echo -e "${GREEN}══════════════════════════════════════════${RESET}"
echo ""
echo -e "   Запуск:    ${CYAN}python3 main.py${RESET}"
echo -e "   Или:       ${CYAN}make run${RESET}"
echo ""
