"""
IntentAgent — LLM-агент на DeepSeek Chat.
Извлекает TripProfile из свободного текста пользователя.
Задаёт уточняющие вопросы при неполных данных.
"""
import json
import time
from typing import Optional

from openai import OpenAI

from config import cfg
from middleware import logger
from models.schemas import Currency, Message, SessionState, TripProfile

_client = OpenAI(api_key=cfg.DEEPSEEK_API_KEY, base_url=cfg.LLM_BASE_URL)

SYSTEM_PROMPT = """Ты — умный ассистент для планирования путешествий.

Твоя задача: собрать информацию о поездке пользователя и вернуть структурированный JSON.

ОБЯЗАТЕЛЬНЫЕ поля TripProfile:
- destination: куда летим (город/страна)
- origin: откуда летим (город)
- departure_date: дата вылета (YYYY-MM-DD)
- return_date: дата возвращения (YYYY-MM-DD)
- travelers: количество путешественников (число)
- budget: бюджет (число)
- currency: валюта (EUR, USD или RUB)

ОПЦИОНАЛЬНЫЕ поля:
- accommodation_type: "hotel", "rental" или "any"
- preferences: список предпочтений (пляж, горы, музеи и т.д.)

ПРАВИЛА:
1. Если какие-то обязательные поля неизвестны — задай уточняющие вопросы (максимум 2 за раз).
2. Если все обязательные поля известны — верни ТОЛЬКО JSON без дополнительного текста.
3. Всегда отвечай на языке пользователя (русский или английский).
4. Не придумывай значения — только то, что явно сказал пользователь.
5. Для дат используй контекст: "в мае" → ближайший май, "через неделю" → конкретную дату.

Когда возвращаешь JSON — строго такой формат:
{
  "status": "complete",
  "profile": { ...все поля... },
  "message": "Отлично! Начинаю поиск..."
}

Когда нужны уточнения:
{
  "status": "incomplete",
  "profile": { ...известные поля или null... },
  "message": "Уточняющий вопрос пользователю..."
}"""


def run(session: SessionState, user_message: str) -> tuple[str, bool]:
    """
    Обновляет TripProfile в сессии на основе сообщения пользователя.
    Возвращает (ответ_пользователю, profile_complete).
    """
    t0 = time.time()

    # Строим контекст: summary + история + текущий TripProfile
    messages = _build_messages(session, user_message)

    response_text, usage = _call_llm(messages, session.session_id)
    session.llm_call_count += 1

    duration_ms = int((time.time() - t0) * 1000)
    logger.log_llm_call(
        session_id=session.session_id,
        agent="IntentAgent",
        model=cfg.LLM_MODEL,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        duration_ms=duration_ms,
    )

    # Парсим ответ
    result, reply, complete = _parse_response(response_text, session)
    if result:
        session.trip_profile = result

    session.dialog_history.append(Message(role="assistant", content=reply))
    return reply, complete


def _build_messages(session: SessionState, user_message: str) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Контекст сессии — текущий TripProfile
    if session.trip_profile and session.trip_profile.destination:
        profile_ctx = (
            "\n\nТекущий собранный профиль поездки:\n"
            + json.dumps(
                _profile_to_dict(session.trip_profile),
                ensure_ascii=False, indent=2
            )
        )
        msgs[0]["content"] += profile_ctx

    # Суммаризация старого диалога
    if session.dialog_summary:
        msgs.append({
            "role": "user",
            "content": f"[Краткое содержание предыдущего диалога: {session.dialog_summary}]",
        })
        msgs.append({"role": "assistant", "content": "Понял, продолжаем."})

    # Последние сообщения
    for msg in session.dialog_history[-cfg.MAX_CONTEXT_MESSAGES:]:
        msgs.append({"role": msg.role, "content": msg.content})

    # Текущее сообщение пользователя
    msgs.append({"role": "user", "content": user_message})
    return msgs


def _call_llm(messages: list[dict], session_id: str, retry: bool = True) -> tuple[str, dict]:
    try:
        resp = _client.chat.completions.create(
            model=cfg.LLM_MODEL,
            messages=messages,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=cfg.LLM_MAX_TOKENS,
        )
        text = resp.choices[0].message.content or ""
        usage = {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        }
        return text, usage
    except Exception as e:
        if retry:
            # Один retry с упрощённым промптом
            simple = [messages[0], messages[-1]]
            return _call_llm(simple, session_id, retry=False)
        raise


def _parse_response(
    text: str, session: SessionState
) -> tuple[Optional[TripProfile], str, bool]:
    """Парсит JSON-ответ LLM. Возвращает (profile | None, reply, complete)."""
    # Ищем JSON в ответе (LLM иногда добавляет текст вокруг)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        # Нет JSON — LLM просто ответил текстом
        return None, text.strip(), False

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None, text.strip(), False

    status = data.get("status", "incomplete")
    message = data.get("message", "")
    profile_data = data.get("profile") or {}

    profile = _dict_to_profile(profile_data, session.trip_profile)
    complete = status == "complete" and profile.is_complete()
    return profile, message, complete


def _profile_to_dict(p: TripProfile) -> dict:
    return {
        "destination": p.destination,
        "origin": p.origin,
        "departure_date": str(p.departure_date) if p.departure_date else None,
        "return_date": str(p.return_date) if p.return_date else None,
        "travelers": p.travelers,
        "budget": p.budget,
        "currency": p.currency.value if p.currency else None,
        "accommodation_type": p.accommodation_type.value if p.accommodation_type else "any",
        "preferences": p.preferences,
    }


def _dict_to_profile(data: dict, existing: TripProfile) -> TripProfile:
    """Мёржит новые данные с существующим профилем."""
    from datetime import date as dt

    def parse_date(v) -> Optional[dt]:
        if not v:
            return None
        if isinstance(v, dt):
            return v
        try:
            return dt.fromisoformat(str(v))
        except Exception:
            return None

    def coerce_currency(v) -> Currency:
        try:
            return Currency(str(v).upper())
        except Exception:
            return existing.currency

    return TripProfile(
        destination=data.get("destination") or existing.destination,
        origin=data.get("origin") or existing.origin,
        departure_date=parse_date(data.get("departure_date")) or existing.departure_date,
        return_date=parse_date(data.get("return_date")) or existing.return_date,
        travelers=data.get("travelers") or existing.travelers,
        budget=data.get("budget") or existing.budget,
        currency=coerce_currency(data.get("currency")) if data.get("currency") else existing.currency,
        accommodation_type=existing.accommodation_type,
        preferences=data.get("preferences") or existing.preferences,
    )
