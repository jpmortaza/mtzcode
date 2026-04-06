"""Base classes para tools do mtzcode.

Cada tool é uma subclasse de `Tool` que declara:
  - name: identificador único usado pelo modelo
  - description: texto que vai pro modelo explicando quando usar
  - Args: classe Pydantic com os parâmetros de entrada
  - run(args): executa e retorna uma string (resultado pro modelo)

A classe `ToolRegistry` mantém um mapa nome → instância e gera os schemas
no formato OpenAI/Ollama (`{type:'function', function:{...}}`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError


class ToolError(RuntimeError):
    """Erro recuperável ao executar uma tool. A mensagem volta pro modelo."""


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]
    # Tools destrutivas disparam prompt de confirmação antes de executar.
    destructive: ClassVar[bool] = False

    def schema(self) -> dict[str, Any]:
        """Schema OpenAI/Ollama-style para tool calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Args.model_json_schema(),
            },
        }

    def call(self, raw_args: dict[str, Any]) -> str:
        """Valida os argumentos e despacha para `run()`. Retorna sempre string."""
        try:
            args = self.Args.model_validate(raw_args or {})
        except ValidationError as exc:
            raise ToolError(f"argumentos inválidos para `{self.name}`: {exc}") from exc
        try:
            result = self.run(args)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — devolve qualquer erro pro modelo
            raise ToolError(f"erro em `{self.name}`: {exc}") from exc
        if not isinstance(result, str):
            result = str(result)
        return result

    @abstractmethod
    def run(self, args: BaseModel) -> str:
        """Implementação concreta. Recebe args validados, retorna string."""


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"tool desconhecida: `{name}`")
        return self._tools[name]

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)
