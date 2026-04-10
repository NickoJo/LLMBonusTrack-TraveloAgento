"""
Structured JSON logger. Все события без PII — в JSONL файл.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from config import cfg


def _ensure_log_dir() -> None:
    log_dir = os.path.dirname(cfg.LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)


_ensure_log_dir()

_handler = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8") if cfg.LOG_FILE else logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))

_logger = logging.getLogger("travelo")
_logger.setLevel(getattr(logging, cfg.LOG_LEVEL, logging.INFO))
_logger.addHandler(_handler)
_logger.propagate = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event: str, session_id: str, **meta: Any) -> None:
    record = {"timestamp": _now(), "session_id": session_id, "event": event, **meta}
    _logger.info(json.dumps(record, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Public helpers — один вызов на событие
# ---------------------------------------------------------------------------

def log_session_start(session_id: str) -> None:
    _emit("session_start", session_id)


def log_session_end(session_id: str, status: str, turns: int, llm_calls: int, duration_ms: int) -> None:
    _emit("session_end", session_id,
          status=status, total_turns=turns,
          total_llm_calls=llm_calls, total_duration_ms=duration_ms)


def log_agent_call(session_id: str, agent: str, step: str,
                   duration_ms: int, status: str, retry_count: int = 0) -> None:
    _emit("agent_call", session_id,
          agent=agent, step=step,
          duration_ms=duration_ms, status=status, retry_count=retry_count)


def log_llm_call(session_id: str, agent: str, model: str,
                 input_tokens: int, output_tokens: int, duration_ms: int) -> None:
    _emit("llm_call", session_id,
          agent=agent, model=model,
          input_tokens=input_tokens, output_tokens=output_tokens,
          duration_ms=duration_ms, status="success")


def log_tool_call(session_id: str, tool: str, results_count: int,
                  duration_ms: int, status: str) -> None:
    _emit("tool_call", session_id,
          tool=tool, results_count=results_count,
          duration_ms=duration_ms, status=status)


def log_pii_detected(session_id: str, pii_types: list[str]) -> None:
    _emit("pii_detected", session_id, pii_types=pii_types, action="masked")


def log_hitl(session_id: str, checkpoint_type: str, user_decision: str) -> None:
    _emit("hitl_checkpoint", session_id,
          checkpoint_type=checkpoint_type, user_decision=user_decision)


def log_budget_check(session_id: str, check_type: str, status: str) -> None:
    _emit("budget_check", session_id, check_type=check_type, status=status)


def log_failover(session_id: str, from_service: str, to_service: str, reason: str) -> None:
    _emit("failover_event", session_id,
          from_service=from_service, to_service=to_service, reason=reason)


def log_rag_query(session_id: str, destination: str, results_count: int,
                  top_score: float, used_fallback: bool) -> None:
    _emit("rag_query", session_id,
          destination=destination, results_count=results_count,
          top_score=round(top_score, 3), used_fallback=used_fallback)


def log_circuit_break(session_id: str, reason: str, llm_calls_used: int) -> None:
    _emit("circuit_break", session_id, reason=reason, llm_calls_used=llm_calls_used)


def log_error(session_id: str, agent: str, error_type: str, message: str) -> None:
    _emit("error", session_id, agent=agent, error_type=error_type, message=message)
