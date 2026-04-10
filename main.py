"""
TraveloAgento — CLI entry point.

Запуск:
    python main.py

Завершение:
    Ctrl+C  или команды: exit / quit / выход / новая сессия
"""
import asyncio
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def _check_env() -> None:
    """Проверяем переменные окружения и индекс RAG перед стартом."""
    from config import cfg  # импортируем после sys.path настройки

    if not cfg.DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY не задан.")
        print("   Скопируйте .env.example → .env и вставьте ключ.")
        sys.exit(1)

    index_path = Path(cfg.KB_INDEX_DIR)
    if not index_path.exists() or not any(index_path.iterdir()):
        print("⚠️  RAG-индекс не найден. Запускаем build_index.py автоматически...")
        print()
        import subprocess
        result = subprocess.run(
            [sys.executable, "src/scripts/build_index.py"],
            cwd=Path(__file__).parent,
        )
        if result.returncode != 0:
            print("❌ Не удалось собрать RAG-индекс. Проверьте data/travel_kb/.")
            sys.exit(1)
        print()


# ---------------------------------------------------------------------------
# ANSI-цвета (отключаем если не TTY)
# ---------------------------------------------------------------------------

_IS_TTY = sys.stdout.isatty()


def _color(text: str, code: str) -> str:
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


def cyan(t: str) -> str:
    return _color(t, "96")


def green(t: str) -> str:
    return _color(t, "92")


def yellow(t: str) -> str:
    return _color(t, "93")


def dim(t: str) -> str:
    return _color(t, "2")


def bold(t: str) -> str:
    return _color(t, "1")


# ---------------------------------------------------------------------------
# Отображение ответа ассистента
# ---------------------------------------------------------------------------

def _print_assistant(reply: str) -> None:
    """Печатает ответ ассистента с мягким форматированием Markdown → терминал."""
    print()
    # Простое преобразование Markdown → текст для терминала
    lines = reply.splitlines()
    for line in lines:
        if line.startswith("# "):
            print(bold(cyan(line[2:])))
        elif line.startswith("## "):
            print()
            print(bold(line[3:]))
        elif line.startswith("### "):
            print(bold("  " + line[4:]))
        elif line.startswith("> ⚠️") or line.startswith("> 🔴"):
            print(yellow(line.replace(">", "").strip()))
        elif line.startswith("> "):
            print(dim(line[2:]))
        elif line.startswith("---"):
            print(dim("─" * 60))
        else:
            # Inline bold (**text**) — упрощённое
            formatted = line
            while "**" in formatted:
                s = formatted.find("**")
                e = formatted.find("**", s + 2)
                if s == -1 or e == -1:
                    break
                formatted = formatted[:s] + bold(formatted[s + 2:e]) + formatted[e + 2:]
            print(formatted)
    print()


# ---------------------------------------------------------------------------
# Команды управления сессией
# ---------------------------------------------------------------------------

_EXIT_COMMANDS = {"exit", "quit", "выход", "q", "bye"}
_NEW_SESSION_COMMANDS = {"новая сессия", "new session", "reset", "restart", "сброс"}


def _is_exit(text: str) -> bool:
    return text.strip().lower() in _EXIT_COMMANDS


def _is_new_session(text: str) -> bool:
    return text.strip().lower() in _NEW_SESSION_COMMANDS


# ---------------------------------------------------------------------------
# Основной диалоговый цикл
# ---------------------------------------------------------------------------

async def _run_session(orchestrator) -> None:
    """Один диалоговый сеанс до DONE / exit / new session."""
    session = orchestrator.new_session()
    print(dim(f"[Сессия {session.session_id}]"))
    print()

    # Приветствие
    welcome = (
        bold("TraveloAgento") + " — ваш персональный планировщик путешествий.\n"
        "Расскажите, куда и когда хотите поехать. Я помогу с рейсами, жильём и маршрутом.\n"
        + dim("(введите 'выход' для выхода, 'новая сессия' для сброса)")
    )
    _print_assistant(welcome)

    while True:
        # Ввод пользователя
        try:
            if _IS_TTY:
                user_input = input(cyan("Вы: ")).strip()
            else:
                user_input = input().strip()
        except EOFError:
            break

        if not user_input:
            continue

        if _is_exit(user_input):
            print(dim("До свидания!"))
            sys.exit(0)

        if _is_new_session(user_input):
            print(dim("Начинаем новую сессию..."))
            print()
            break

        # Индикатор обработки
        if _IS_TTY:
            print(dim("⏳ Обрабатываю..."), end="\r", flush=True)

        try:
            reply = await orchestrator.handle_message(session, user_input)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            reply = f"Произошла внутренняя ошибка: {type(e).__name__}. Попробуйте ещё раз."

        # Очищаем строку "Обрабатываю..."
        if _IS_TTY:
            print(" " * 30, end="\r")

        _print_assistant(reply)

        # Если сессия завершена — предлагаем новую
        from models.schemas import SessionStatus
        if session.status in (SessionStatus.COMPLETED, SessionStatus.ERROR):
            print(dim("─" * 60))
            print()
            try:
                choice = input(
                    cyan("Начать новую сессию? (да/нет): ")
                ).strip().lower()
            except EOFError:
                break

            if choice in {"да", "yes", "y", "д", "ok", "ок"}:
                print()
                break
            else:
                print(dim("До свидания!"))
                sys.exit(0)


async def _main_loop() -> None:
    from agents.orchestrator import Orchestrator

    orchestrator = Orchestrator()

    print()
    print(bold(cyan("=" * 60)))
    print(bold(cyan("           TraveloAgento PoC")))
    print(bold(cyan("=" * 60)))
    print()

    while True:
        try:
            await _run_session(orchestrator)
        except KeyboardInterrupt:
            print()
            print(dim("Прерывание. До свидания!"))
            break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Добавляем src/ в sys.path — все пакеты (agents, middleware, ...) живут там
    project_root = Path(__file__).parent
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    _check_env()

    try:
        asyncio.run(_main_loop())
    except KeyboardInterrupt:
        print()
        print(dim("До свидания!"))


if __name__ == "__main__":
    main()
