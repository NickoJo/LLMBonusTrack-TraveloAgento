"""
Guardrail — regex-based middleware.
Маскирует персональные данные перед отправкой в LLM.
Хранит token map только в памяти сессии, не логирует и не сохраняет на диск.
"""
import re
import uuid
from typing import Dict

# ---------------------------------------------------------------------------
# PII patterns (RU + EN)
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email",   re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)),
    ("phone",   re.compile(
        r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
        r"|(?:\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}"
    )),
    ("card",    re.compile(r"\b(?:\d[ \-]?){15,16}\b")),
    ("passport_ru", re.compile(r"\b\d{4}[\s\-]?\d{6}\b")),  # серия + номер
]

# ---------------------------------------------------------------------------
# Prompt injection patterns (EN + RU)
# Применяются к данным из внешних API перед инжектом в промпт.
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|all)\s+instructions?", re.I),
    re.compile(r"forget\s+(all\s+)?instructions?", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    re.compile(r"new\s+instructions?\s*:", re.I),
    re.compile(r"disregard\s+", re.I),
    re.compile(r"<\s*/?system\s*>", re.I),
    re.compile(r"system\s*prompt\s*:", re.I),
    re.compile(r"игнорир[уй]\w*\s+(предыдущ|все?)\w*\s+инструкц", re.I),
    re.compile(r"забудь\s+(все?|предыдущ\w+)?\s*инструкц", re.I),
    re.compile(r"теперь\s+ты\b", re.I),
    re.compile(r"ты\s+теперь\b", re.I),
    re.compile(r"новые?\s+инструкц\w*\s*:", re.I),
    re.compile(r"системный\s+промпт\s*:", re.I),
    re.compile(r"действуй\s+как\b", re.I),
    re.compile(r"притворись\s+что\s+ты\b", re.I),
]


def sanitize(text: str, token_map: Dict[str, str]) -> str:
    """
    Заменяет PII в тексте на токены вида [PII_email_1].
    token_map хранится в SessionState и очищается при завершении сессии.
    """
    for pii_type, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            original = match.group()
            if original not in token_map:
                token = f"[PII_{pii_type}_{uuid.uuid4().hex[:6]}]"
                token_map[original] = token
            text = text.replace(original, token_map[original])
    return text


def restore(text: str, token_map: Dict[str, str]) -> str:
    """Восстанавливает оригинальные значения из токенов (для отладки, не для LLM)."""
    for original, token in token_map.items():
        text = text.replace(token, original)
    return text


def sanitize_external(text: str) -> str:
    """
    Удаляет injection-паттерны из данных внешних API перед инжектом в промпт.
    Не меняет token_map — это не PII, это защита от prompt injection.
    """
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub("[REMOVED]", text)
    return text


def sanitize_dict(data: dict, token_map: Dict[str, str]) -> dict:
    """Рекурсивно санитизирует строковые поля словаря."""
    result = {}
    for k, v in data.items():
        if isinstance(v, str):
            result[k] = sanitize(v, token_map)
        elif isinstance(v, dict):
            result[k] = sanitize_dict(v, token_map)
        elif isinstance(v, list):
            result[k] = [
                sanitize(i, token_map) if isinstance(i, str)
                else sanitize_dict(i, token_map) if isinstance(i, dict)
                else i
                for i in v
            ]
        else:
            result[k] = v
    return result


def has_pii(text: str) -> list[str]:
    """Возвращает список типов PII найденных в тексте (для тестов)."""
    found = []
    for pii_type, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(pii_type)
    return found
