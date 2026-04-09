"""Eventos em formato dot-notation, espelhando o Claude Managed Agents.

O agent loop existente (``mtzcode.agent``) emite eventos no formato
``AgentEvent(kind="text_delta" | "tool_call" | ...)``. Aqui a gente traduz
pro vocabulário ``agent.message`` / ``agent.tool_use`` / ``session.status_idle``
que o usuário de uma API estilo Managed Agents espera consumir.

A ideia é que quem constrói uma UI ou integração nova leia ``ManagedEvent``
em vez do ``AgentEvent`` cru — e o runner garante que ambos os formatos
continuem sendo emitidos (o ``AgentEvent`` segue indo pro ``SessionLogger``
existente, pra não quebrar nada).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mtzcode.agent import AgentEvent


# ---------------------------------------------------------------------------
# Tipos de evento (mesma taxonomia do Claude Managed Agents, onde fizer sentido)
# ---------------------------------------------------------------------------
# Agent-originated
EVT_AGENT_MESSAGE = "agent.message"
EVT_AGENT_MESSAGE_DELTA = "agent.message_delta"
EVT_AGENT_TOOL_USE = "agent.tool_use"
EVT_AGENT_TOOL_RESULT = "agent.tool_result"
EVT_AGENT_TOOL_ERROR = "agent.tool_error"
EVT_AGENT_TOOL_DENIED = "agent.tool_denied"

# Session lifecycle
EVT_SESSION_RUNNING = "session.status_running"
EVT_SESSION_IDLE = "session.status_idle"
EVT_SESSION_TERMINATED = "session.status_terminated"

# User-originated (ecoados no stream pra consistência)
EVT_USER_MESSAGE = "user.message"
EVT_USER_CUSTOM_TOOL_RESULT = "user.custom_tool_result"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sevt_id() -> str:
    return f"sevt_{uuid.uuid4().hex[:16]}"


@dataclass
class ManagedEvent:
    """Evento em formato dot-notation pra consumidores estilo Managed Agents.

    Campos espelham o que o Claude manda no stream SSE: ``id``, ``type``,
    ``processed_at``, e um ``payload`` livre com o conteúdo específico.
    """

    type: str
    id: str = field(default_factory=_sevt_id)
    processed_at: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "processed_at": self.processed_at,
            **self.payload,
        }


# ---------------------------------------------------------------------------
# Bridge: AgentEvent (formato interno) → ManagedEvent (formato público)
# ---------------------------------------------------------------------------
def bridge(event: AgentEvent) -> ManagedEvent | None:
    """Converte um ``AgentEvent`` legado pro formato dot-notation.

    Retorna ``None`` pra kinds que não tem equivalente público (debug/UI
    internos tipo ``text_suppress_start``) — o caller deve ignorar.
    """
    kind = event.kind
    data = event.data or {}

    if kind == "text_delta":
        return ManagedEvent(
            type=EVT_AGENT_MESSAGE_DELTA,
            payload={"delta": data.get("delta", "")},
        )
    if kind in ("assistant_text", "assistant_text_end"):
        text = data.get("text")
        if text is None:
            return None
        return ManagedEvent(
            type=EVT_AGENT_MESSAGE,
            payload={"content": [{"type": "text", "text": text}]},
        )
    if kind == "tool_call":
        return ManagedEvent(
            type=EVT_AGENT_TOOL_USE,
            payload={
                "tool_name": data.get("name", ""),
                "input": data.get("args", {}),
            },
        )
    if kind == "tool_result":
        return ManagedEvent(
            type=EVT_AGENT_TOOL_RESULT,
            payload={
                "tool_name": data.get("name", ""),
                "result": data.get("result", ""),
            },
        )
    if kind == "tool_error":
        return ManagedEvent(
            type=EVT_AGENT_TOOL_ERROR,
            payload={
                "tool_name": data.get("name", ""),
                "error": data.get("error", ""),
            },
        )
    if kind == "tool_denied":
        return ManagedEvent(
            type=EVT_AGENT_TOOL_DENIED,
            payload={"tool_name": data.get("name", "")},
        )
    if kind == "max_iterations":
        return ManagedEvent(
            type=EVT_SESSION_IDLE,
            payload={
                "stop_reason": {
                    "type": "retries_exhausted",
                    "limit": data.get("limit"),
                }
            },
        )
    # text_suppress_start, text_suppress_end e outros UI-internos
    return None


def running_event() -> ManagedEvent:
    return ManagedEvent(type=EVT_SESSION_RUNNING, payload={})


def idle_event(stop_reason: str = "end_turn") -> ManagedEvent:
    return ManagedEvent(
        type=EVT_SESSION_IDLE,
        payload={"stop_reason": {"type": stop_reason}},
    )


def terminated_event(reason: str = "archived") -> ManagedEvent:
    return ManagedEvent(
        type=EVT_SESSION_TERMINATED,
        payload={"reason": reason},
    )


def user_message_event(text: str) -> ManagedEvent:
    return ManagedEvent(
        type=EVT_USER_MESSAGE,
        payload={"content": [{"type": "text", "text": text}]},
    )
