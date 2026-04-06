"""Cliente HTTP unificado para qualquer backend OpenAI-compatible.

Suporta Ollama local (via `/v1/chat/completions`) e serviços cloud como Groq.
A escolha do backend é feita pelo `Profile` passado no construtor.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Iterable

import httpx

from mtzcode.profiles import Profile


class ChatClientError(RuntimeError):
    """Erro de comunicação com o backend (rede, auth, payload, etc)."""


# Alias retrocompatível pro código antigo (agent.py importava OllamaError).
OllamaError = ChatClientError


class ChatClient:
    """Cliente OpenAI-compatible. Funciona com Ollama, Groq, ou qualquer outro
    serviço que exponha `/chat/completions` no formato OpenAI.
    """

    def __init__(self, profile: Profile, timeout_s: float = 300.0) -> None:
        self.profile = profile
        self.model = profile.model

        api_key = "ollama"  # placeholder — Ollama não verifica
        if profile.needs_api_key:
            api_key = os.environ.get(profile.api_key_env or "", "")
            if not api_key:
                raise ChatClientError(
                    f"variável de ambiente {profile.api_key_env} não definida — "
                    f"necessária para usar {profile.label}."
                )

        self._client = httpx.Client(
            base_url=profile.base_url,
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Envia uma rodada e devolve o `message` do assistant (dict)."""
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": _normalize_messages_for_openai(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)

        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise ChatClientError(
                f"falha ao conectar em {self.profile.base_url}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise ChatClientError(
                f"{self.profile.label} respondeu {response.status_code}: "
                f"{response.text[:500]}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ChatClientError(f"resposta sem choices: {str(data)[:300]}")
        return choices[0].get("message", {}) or {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ChatClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# Aliases retrocompatíveis
OllamaClient = ChatClient


def _normalize_messages_for_openai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A API OpenAI exige `tool_calls[*].function.arguments` como string JSON.
    Algumas mensagens do histórico podem ter sido criadas com arguments como dict
    (especialmente vindas do fallback parser do agent). Reserializa onde precisa.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        # Cópia rasa primeiro; só clona profundo se houver tool_calls que mexer.
        tcs = m.get("tool_calls")
        if not tcs:
            out.append(m)
            continue
        new_msg = copy.deepcopy(m)
        for tc in new_msg.get("tool_calls", []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, dict):
                fn["arguments"] = json.dumps(args, ensure_ascii=False)
            elif args is None:
                fn["arguments"] = "{}"
            # OpenAI também exige `type: "function"` e um `id` em cada tool_call.
            tc.setdefault("type", "function")
            tc.setdefault("id", f"call_{fn.get('name', 'unknown')}")
        out.append(new_msg)
    return out
