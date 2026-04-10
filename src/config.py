"""
Централизованная загрузка конфигурации из переменных окружения.
Все модули импортируют cfg из этого файла — не читают os.environ напрямую.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


class Config:
    # LLM
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_TEMPERATURE: float = _float("LLM_TEMPERATURE", 0.3)
    ITINERARY_TEMPERATURE: float = _float("ITINERARY_TEMPERATURE", 0.7)
    LLM_MAX_TOKENS: int = _int("LLM_MAX_TOKENS", 2048)

    # Circuit breaker & session limits
    MAX_LLM_CALLS: int = _int("MAX_LLM_CALLS", 30)
    MAX_CONTEXT_MESSAGES: int = _int("MAX_CONTEXT_MESSAGES", 20)
    SESSION_TIMEOUT_MIN: int = _int("SESSION_TIMEOUT_MIN", 30)
    MAX_CONCURRENT_SESSIONS: int = _int("MAX_CONCURRENT_SESSIONS", 5)

    # Tools
    TOOL_TIMEOUT_SEC: float = _float("TOOL_TIMEOUT_SEC", 5.0)
    MAX_SEARCH_RESULTS: int = _int("MAX_SEARCH_RESULTS", 5)

    # RAG
    RAG_TOP_K: int = _int("RAG_TOP_K", 5)
    RAG_SCORE_THRESHOLD: float = _float("RAG_SCORE_THRESHOLD", 0.6)
    KB_DIR: str = os.getenv("KB_DIR", "data/travel_kb/cities")
    KB_INDEX_DIR: str = os.getenv("KB_INDEX_DIR", "data/travel_kb/index")
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )

    # Demo flags (MCP failure simulation)
    MOCK_BOOKING_FAIL: bool = _bool("MOCK_BOOKING_FAIL")
    MOCK_AIRBNB_FAIL: bool = _bool("MOCK_AIRBNB_FAIL")
    MOCK_FLIGHTS_DELAY_SEC: float = _float("MOCK_FLIGHTS_DELAY_SEC", 0.0)

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/travelo.jsonl")

    def validate(self) -> None:
        if not self.DEEPSEEK_API_KEY:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and add your key."
            )


cfg = Config()
