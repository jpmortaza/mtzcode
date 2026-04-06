"""Agent loop do mtzcode — recebe mensagem do usuário, conversa com o modelo,
executa tools quando solicitado, repete até o modelo terminar (sem mais tool_calls).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from mtzcode.client import ChatClient, ChatClientError
from mtzcode.tools.base import ToolError, ToolRegistry

MAX_ITERATIONS = 25

# Regex para extrair blocos <tool_call>...</tool_call> emitidos pelo template do Qwen
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


@dataclass
class AgentEvent:
    """Evento emitido pelo agent durante a execução, pra UI mostrar o progresso."""

    kind: str  # "assistant_text" | "tool_call" | "tool_result" | "tool_error" | "max_iterations"
    data: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[AgentEvent], None]


class Agent:
    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        system_prompt: str,
        max_iterations: int = MAX_ITERATIONS,
    ) -> None:
        self.client = client
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def reset(self) -> None:
        self.history = [{"role": "system", "content": self.system_prompt}]

    def run(self, user_message: str, on_event: EventCallback | None = None) -> str:
        """Executa um turno completo: envia user_message, processa tool_calls em loop,
        retorna o texto final do assistente.
        """
        self.history.append({"role": "user", "content": user_message})
        on_event = on_event or (lambda _e: None)

        final_text = ""
        for _ in range(self.max_iterations):
            try:
                msg = self.client.chat(self.history, tools=self.registry.schemas())
            except ChatClientError as exc:
                self.history.pop()  # remove user msg pra poder retentar
                raise

            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []

            # Fallback: alguns modelos (Qwen2.5-Coder Q4) emitem tool calls como JSON
            # nu no content, sem o campo tool_calls estruturado. Tentamos extrair daqui.
            if not tool_calls and content:
                extracted, leftover_text = _extract_tool_calls_from_content(content)
                if extracted:
                    tool_calls = extracted
                    content = leftover_text  # texto que sobrou (geralmente vazio)

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.history.append(assistant_msg)

            if content and not tool_calls:
                on_event(AgentEvent("assistant_text", {"text": content}))
                final_text = content

            if not tool_calls:
                # Modelo terminou — sem mais tools pra chamar
                return final_text

            # Executa cada tool_call e devolve resultado pro modelo
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                tool_call_id = tc.get("id") or f"call_{name}"
                raw_args = fn.get("arguments", {}) or {}
                # OpenAI/Ollama às vezes mandam arguments como string JSON
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        raw_args = {}

                on_event(AgentEvent("tool_call", {"name": name, "args": raw_args}))

                try:
                    tool = self.registry.get(name)
                    result = tool.call(raw_args)
                    on_event(AgentEvent("tool_result", {"name": name, "result": result}))
                except ToolError as exc:
                    result = f"ERRO: {exc}"
                    on_event(AgentEvent("tool_error", {"name": name, "error": str(exc)}))

                # Formato OpenAI: tool message precisa de tool_call_id
                self.history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": name,
                        "content": result,
                    }
                )
            # volta ao topo do loop pra dar mais um turno ao modelo

        on_event(AgentEvent("max_iterations", {"limit": self.max_iterations}))
        return final_text or f"(limite de {self.max_iterations} iterações atingido)"


def _extract_tool_calls_from_content(content: str) -> tuple[list[dict[str, Any]], str]:
    """Tenta extrair tool calls de um content textual.

    Cobre três formatos comuns que modelos open-source emitem:
      1. <tool_call>{"name": "...", "arguments": {...}}</tool_call>  (template do Qwen)
      2. JSON nu no content inteiro: {"name": "...", "arguments": {...}}
      3. Um array JSON: [{"name": "..."}, {"name": "..."}]

    Retorna (lista_de_tool_calls_no_formato_ollama, texto_sobrando).
    Se nada for extraído, retorna ([], content).
    """
    # 1. Tags <tool_call>
    tag_matches = _TOOL_CALL_TAG_RE.findall(content)
    if tag_matches:
        calls: list[dict[str, Any]] = []
        for raw in tag_matches:
            parsed = _parse_call_json(raw)
            if parsed:
                calls.append(parsed)
        leftover = _TOOL_CALL_TAG_RE.sub("", content).strip()
        if calls:
            return calls, leftover

    # 2. JSON nu — tenta o content inteiro como objeto/array
    stripped = content.strip()
    # Remove cercas de markdown se vierem (```json ... ```)
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return [], content

    if isinstance(data, dict):
        parsed = _normalize_call_dict(data)
        return ([parsed], "") if parsed else ([], content)
    if isinstance(data, list):
        calls = [c for c in (_normalize_call_dict(d) for d in data if isinstance(d, dict)) if c]
        return (calls, "") if calls else ([], content)

    return [], content


def _parse_call_json(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_call_dict(data)


def _normalize_call_dict(data: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza um dict {name, arguments} para o formato tool_call do Ollama."""
    name = data.get("name")
    if not isinstance(name, str):
        return None
    arguments = data.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return {"function": {"name": name, "arguments": arguments}}
