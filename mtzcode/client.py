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
        self._inject_backend_options(payload)

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

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
    ):
        """Gera chunks SSE do endpoint `/chat/completions` com `stream=true`.

        Cada item yield-ado é um dict no formato OpenAI streaming, com `choices[0].delta`
        contendo `content` e/ou `tool_calls` parciais.
        """
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": _normalize_messages_for_openai(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = list(tools)
        self._inject_backend_options(payload)

        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    # Tenta ler o body de erro
                    try:
                        body = response.read().decode("utf-8", errors="replace")
                    except Exception:
                        body = "<erro lendo body>"
                    raise ChatClientError(
                        f"{self.profile.label} respondeu {response.status_code}: "
                        f"{body[:500]}"
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise ChatClientError(
                f"falha ao conectar em {self.profile.base_url}: {exc}"
            ) from exc

    def _inject_backend_options(self, payload: dict[str, Any]) -> None:
        """Injeta opções específicas do backend no payload.

        CRÍTICO pro Ollama: sem `num_ctx` explícito, ele usa 2048 tokens
        e trunca o prompt (system + tools + histórico facilmente passa
        de 4-5k), o que **destrói o KV cache** entre turnos e força o
        modelo a reprocessar TUDO a cada mensagem.

        Lê de ``Settings`` (editável pelo web UI). Env vars MTZCODE_*
        têm precedência pra debug/scripting.

        Cloud (Groq/Maritaca) ignora esses campos silenciosamente.
        """
        if not self.profile.is_local or self.profile.backend != "ollama":
            return
        # Carrega settings persistentes
        try:
            from mtzcode.settings import get_settings
            opts = get_settings().model_options
            num_ctx = int(os.environ.get("MTZCODE_NUM_CTX", opts.num_ctx))
            num_predict = int(
                os.environ.get("MTZCODE_NUM_PREDICT", opts.num_predict)
            )
            keep_alive = os.environ.get("MTZCODE_KEEP_ALIVE", opts.keep_alive)
            temperature = opts.temperature
            top_p = opts.top_p
        except Exception:
            num_ctx = int(os.environ.get("MTZCODE_NUM_CTX", "16384"))
            num_predict = int(os.environ.get("MTZCODE_NUM_PREDICT", "2048"))
            keep_alive = os.environ.get("MTZCODE_KEEP_ALIVE", "30m")
            temperature = 0.3
            top_p = 0.9
        payload.setdefault(
            "options",
            {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
                "top_p": top_p,
            },
        )
        payload.setdefault("keep_alive", keep_alive)

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
