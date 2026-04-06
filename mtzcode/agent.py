"""Agent loop do mtzcode — conversa com o modelo, executa tools, repete
até o modelo terminar (sem mais tool_calls).

Suporta modo síncrono (`run`) e modo streaming (`run_streaming`, que emite
deltas de texto em tempo real para a UI).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from mtzcode.client import ChatClient, ChatClientError
from mtzcode.tools.base import Tool, ToolError, ToolRegistry

MAX_ITERATIONS = 25

# Regex para extrair blocos <tool_call>...</tool_call> (template Qwen oficial)
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


@dataclass
class AgentEvent:
    """Evento emitido pelo agent durante execução, pra UI mostrar progresso."""
    kind: str  # text_delta | assistant_text | tool_call | tool_result | tool_error | tool_denied | max_iterations
    data: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[AgentEvent], None]

# Callback de confirmação para tools destrutivas.
# Recebe (nome_da_tool, args_dict) e retorna True/False.
ConfirmCallback = Callable[[str, dict[str, Any]], bool]


class Agent:
    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        system_prompt: str,
        max_iterations: int = MAX_ITERATIONS,
        confirm_cb: ConfirmCallback | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.confirm_cb = confirm_cb
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def reset(self) -> None:
        self.history = [{"role": "system", "content": self.system_prompt}]

    def set_system_prompt(self, prompt: str) -> None:
        """Substitui o system prompt (pra plan mode, por ex). Preserva o resto do histórico."""
        self.system_prompt = prompt
        if self.history and self.history[0].get("role") == "system":
            self.history[0] = {"role": "system", "content": prompt}
        else:
            self.history.insert(0, {"role": "system", "content": prompt})

    # ------------------------------------------------------------------
    # Modo síncrono — espera a resposta completa antes de devolver.
    # ------------------------------------------------------------------
    def run(self, user_message: str, on_event: EventCallback | None = None) -> str:
        self.history.append({"role": "user", "content": user_message})
        on_event = on_event or (lambda _e: None)
        final_text = ""

        for _ in range(self.max_iterations):
            try:
                msg = self.client.chat(self.history, tools=self.registry.schemas())
            except ChatClientError:
                self.history.pop()
                raise

            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls and content:
                extracted, leftover = _extract_tool_calls_from_content(content)
                if extracted:
                    tool_calls = extracted
                    content = leftover

            self._append_assistant(content, tool_calls)

            if content and not tool_calls:
                on_event(AgentEvent("assistant_text", {"text": content}))
                final_text = content

            if not tool_calls:
                return final_text

            self._execute_tool_calls(tool_calls, on_event)
            final_text = content or final_text

        on_event(AgentEvent("max_iterations", {"limit": self.max_iterations}))
        return final_text or f"(limite de {self.max_iterations} iterações atingido)"

    # ------------------------------------------------------------------
    # Modo streaming — emite text_delta conforme os tokens chegam.
    # ------------------------------------------------------------------
    def run_streaming(
        self, user_message: str, on_event: EventCallback | None = None
    ) -> str:
        self.history.append({"role": "user", "content": user_message})
        on_event = on_event or (lambda _e: None)
        final_text = ""

        for _ in range(self.max_iterations):
            try:
                content, tool_calls = self._consume_stream(on_event)
            except ChatClientError:
                self.history.pop()
                raise

            # Fallback: se não veio tool_calls estruturado, tenta parsear do content
            if not tool_calls and content:
                extracted, leftover = _extract_tool_calls_from_content(content)
                if extracted:
                    tool_calls = extracted
                    content = leftover

            self._append_assistant(content, tool_calls)

            if content and not tool_calls:
                final_text = content

            if not tool_calls:
                return final_text

            on_event(AgentEvent("assistant_text_end", {}))
            self._execute_tool_calls(tool_calls, on_event)
            final_text = content or final_text

        on_event(AgentEvent("max_iterations", {"limit": self.max_iterations}))
        return final_text or f"(limite de {self.max_iterations} iterações atingido)"

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    def _consume_stream(
        self, on_event: EventCallback
    ) -> tuple[str, list[dict[str, Any]]]:
        """Lê os chunks SSE do client e acumula content + tool_calls parciais.

        Retorna (content_final, tool_calls_assembled).
        """
        content = ""
        tool_calls_by_idx: dict[int, dict[str, Any]] = {}

        for chunk in self.client.chat_stream(
            self.history, tools=self.registry.schemas()
        ):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {}) or {}

            # Texto
            content_delta = delta.get("content")
            if content_delta:
                content += content_delta
                on_event(AgentEvent("text_delta", {"delta": content_delta}))

            # Tool calls parciais (formato OpenAI streaming)
            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_by_idx:
                    tool_calls_by_idx[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                accum = tool_calls_by_idx[idx]
                if tc_delta.get("id"):
                    accum["id"] = tc_delta["id"]
                fn = tc_delta.get("function") or {}
                if fn.get("name"):
                    accum["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    accum["function"]["arguments"] += fn["arguments"]

        tool_calls = list(tool_calls_by_idx.values())
        return content, tool_calls

    def _append_assistant(
        self, content: str, tool_calls: list[dict[str, Any]]
    ) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.history.append(msg)

    def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]], on_event: EventCallback
    ) -> None:
        """Executa cada tool call, respeitando confirmação de destrutivas,
        e acrescenta os resultados ao histórico.
        """
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            tool_call_id = tc.get("id") or f"call_{name}"
            raw_args = fn.get("arguments", {}) or {}
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    raw_args = {}

            on_event(AgentEvent("tool_call", {"name": name, "args": raw_args}))

            # Busca a tool e checa se é destrutiva
            try:
                tool = self.registry.get(name)
            except ToolError as exc:
                result = f"ERRO: {exc}"
                on_event(AgentEvent("tool_error", {"name": name, "error": str(exc)}))
                self._append_tool_result(tool_call_id, name, result)
                continue

            # Confirmação pra tools destrutivas
            if tool.destructive and self.confirm_cb is not None:
                if not self.confirm_cb(name, raw_args):
                    result = "usuário recusou a execução desta tool."
                    on_event(AgentEvent("tool_denied", {"name": name}))
                    self._append_tool_result(tool_call_id, name, result)
                    continue

            # Executa
            try:
                result = tool.call(raw_args)
                on_event(
                    AgentEvent("tool_result", {"name": name, "result": result})
                )
            except ToolError as exc:
                result = f"ERRO: {exc}"
                on_event(
                    AgentEvent("tool_error", {"name": name, "error": str(exc)})
                )

            self._append_tool_result(tool_call_id, name, result)

    def _append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": content,
            }
        )


# ----------------------------------------------------------------------
# Fallback parser — para modelos que emitem tool calls como JSON no content
# ----------------------------------------------------------------------
def _extract_tool_calls_from_content(content: str) -> tuple[list[dict[str, Any]], str]:
    """Tenta extrair tool calls de um content textual.

    Cobre três formatos comuns que modelos open-source emitem:
      1. <tool_call>{"name": "...", "arguments": {...}}</tool_call>  (template Qwen)
      2. JSON nu no content: {"name": "...", "arguments": {...}}
      3. Array JSON: [{"name": "..."}, {"name": "..."}]
    """
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

    stripped = content.strip()
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
        calls = [
            c for c in (_normalize_call_dict(d) for d in data if isinstance(d, dict)) if c
        ]
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
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
