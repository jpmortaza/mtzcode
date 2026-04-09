"""Modelo de dados da infra de agentes do mtzcode.

Espelha a arquitetura do Claude Managed Agents (Agent → Environment → Session)
adaptada pro mundo local do mtzcode:

- ``AgentConfig``       — o "contrato" do agente: profile de modelo, system
                          prompt, tool groups, skills. É versionado: cada
                          update cria uma ``AgentVersion`` nova, imutável.
- ``AgentRecord``       — guarda a config atual + histórico de versões +
                          flags de lifecycle (archived_at).
- ``EnvironmentConfig`` — o "workspace" onde o agente roda: cwd, networking,
                          request_timeout. Reutilizável entre agentes.
- ``SessionRecord``     — uma execução concreta: aponta pra um agent (por id
                          + versão) + um environment + o arquivo JSONL de log.

Cada record tem um `id`, `created_at` e `metadata` livre, igual ao modelo
REST do Claude. Persistência JSON fica em ``mtzcode/agents/store.py``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """Timestamp UTC ISO-8601 com sufixo Z, padrão do Claude API."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_uuid() -> str:
    """Sufixo de 12 chars hex — colisão astronomicamente improvável."""
    return uuid.uuid4().hex[:12]


def new_agent_id() -> str:
    return f"agent_{_short_uuid()}"


def new_env_id() -> str:
    return f"env_{_short_uuid()}"


def new_session_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"sess_{ts}_{_short_uuid()[:8]}"


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
@dataclass
class AgentConfig:
    """Configuração mutável de um agente. Cada update vira uma nova versão.

    Campos espelham os do Claude ``POST /v1/agents`` traduzidos pro mtzcode:

    - ``model`` é um **profile name** (ver ``mtzcode.profiles``), não um model
      ID direto — isso permite trocar o backend (ollama/groq/maritaca) sem
      mexer no agente.
    - ``tool_groups`` é a lista de grupos do ``default_registry`` (``core``,
      ``documents``, ``web``...). O registry real é resolvido na hora da
      sessão, não guardado aqui.
    - ``disabled_tools`` é o filtro fino por nome de tool, respeitado pela
      web UI existente (``Session.disabled_tools``).
    - ``skills`` e ``mcp_servers`` ficam como lista de nomes/URLs pra plugar
      depois na infra de skills/MCP que já existe.
    """

    name: str
    model: str  # profile name, ex "qwen-14b"
    system: str = ""
    description: str = ""
    tool_groups: list[str] = field(default_factory=lambda: ["core"])
    disabled_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentConfig":
        return cls(
            name=data["name"],
            model=data["model"],
            system=data.get("system", ""),
            description=data.get("description", ""),
            tool_groups=list(data.get("tool_groups") or ["core"]),
            disabled_tools=list(data.get("disabled_tools") or []),
            skills=list(data.get("skills") or []),
            mcp_servers=list(data.get("mcp_servers") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class AgentVersion:
    """Snapshot imutável de uma config + número sequencial + timestamp."""

    version: int
    created_at: str
    config: AgentConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "config": self.config.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentVersion":
        return cls(
            version=int(data["version"]),
            created_at=data["created_at"],
            config=AgentConfig.from_dict(data["config"]),
        )


@dataclass
class AgentRecord:
    """Objeto persistido de um agente.

    Contém a versão mais recente acessível direto em ``current`` e o histórico
    em ``versions``. ``archived_at`` é o único "delete" suportado — igual ao
    Claude API, agents não têm hard delete.
    """

    id: str
    current: AgentVersion
    versions: list[AgentVersion]
    created_at: str
    updated_at: str
    archived_at: str | None = None

    @property
    def name(self) -> str:
        return self.current.config.name

    @property
    def version(self) -> int:
        return self.current.version

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def find_version(self, version: int) -> AgentVersion | None:
        for v in self.versions:
            if v.version == version:
                return v
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "agent",
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
            "current": self.current.to_dict(),
            "versions": [v.to_dict() for v in self.versions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRecord":
        versions = [AgentVersion.from_dict(v) for v in data.get("versions", [])]
        current = AgentVersion.from_dict(data["current"])
        return cls(
            id=data["id"],
            current=current,
            versions=versions,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            archived_at=data.get("archived_at"),
        )

    @classmethod
    def new(cls, config: AgentConfig) -> "AgentRecord":
        now = _now_iso()
        first = AgentVersion(version=1, created_at=now, config=config)
        return cls(
            id=new_agent_id(),
            current=first,
            versions=[first],
            created_at=now,
            updated_at=now,
        )

    def bump_version(self, new_config: AgentConfig) -> AgentVersion:
        """Cria uma versão nova, torna ela a ``current`` e retorna."""
        next_num = max((v.version for v in self.versions), default=0) + 1
        now = _now_iso()
        new_v = AgentVersion(version=next_num, created_at=now, config=new_config)
        self.versions.append(new_v)
        self.current = new_v
        self.updated_at = now
        return new_v


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
@dataclass
class EnvironmentConfig:
    """Config do workspace onde tools executam.

    No mtzcode tudo roda local, então ``networking="unrestricted"`` é o
    default — o campo existe pra compatibilidade com o modelo mental do
    Managed Agents e pra um dia suportar sandbox (Docker/firejail).
    """

    name: str
    cwd: str  # diretório de trabalho absoluto
    networking: str = "unrestricted"  # unrestricted | sandboxed
    request_timeout_s: float = 300.0
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvironmentConfig":
        return cls(
            name=data["name"],
            cwd=data["cwd"],
            networking=data.get("networking", "unrestricted"),
            request_timeout_s=float(data.get("request_timeout_s", 300.0)),
            description=data.get("description", ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class EnvironmentRecord:
    id: str
    config: EnvironmentConfig
    created_at: str
    updated_at: str
    archived_at: str | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "environment",
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
            "config": self.config.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvironmentRecord":
        return cls(
            id=data["id"],
            config=EnvironmentConfig.from_dict(data["config"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            archived_at=data.get("archived_at"),
        )

    @classmethod
    def new(cls, config: EnvironmentConfig) -> "EnvironmentRecord":
        now = _now_iso()
        return cls(
            id=new_env_id(),
            config=config,
            created_at=now,
            updated_at=now,
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
@dataclass
class AgentRef:
    """Ponteiro pra um agente + versão (mesma forma do Claude API)."""

    id: str
    version: int | None = None  # None = "latest"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "agent", "id": self.id}
        if self.version is not None:
            out["version"] = self.version
        return out

    @classmethod
    def from_any(cls, value: "AgentRef | str | dict[str, Any]") -> "AgentRef":
        """Aceita as três formas que o Claude aceita em ``sessions.create``.

        - ``"agent_abc123"``                       → shorthand, latest version
        - ``{"id": ..., "version": N}``            → pinado
        - ``AgentRef(id=..., version=N)``          → já é
        """
        if isinstance(value, AgentRef):
            return value
        if isinstance(value, str):
            return cls(id=value)
        if isinstance(value, dict):
            return cls(id=value["id"], version=value.get("version"))
        raise TypeError(f"agent ref inválido: {value!r}")


SessionStatus = str  # "idle" | "running" | "terminated"


@dataclass
class SessionRecord:
    """Metadados de uma sessão.

    O histórico real de eventos vive no JSONL do ``session_log`` (apontado
    por ``events_path``). Esse record é só o índice: quem é o agent, quem é
    o env, status atual, contagem de tokens, título.
    """

    id: str
    agent: AgentRef
    environment_id: str
    events_path: str  # caminho absoluto pro JSONL
    status: SessionStatus
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "session",
            "id": self.id,
            "agent": self.agent.to_dict(),
            "environment_id": self.environment_id,
            "events_path": self.events_path,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
            "metadata": self.metadata,
            "usage": self.usage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            id=data["id"],
            agent=AgentRef.from_any(data["agent"]),
            environment_id=data["environment_id"],
            events_path=data["events_path"],
            status=data.get("status", "idle"),
            title=data.get("title", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            archived_at=data.get("archived_at"),
            metadata=dict(data.get("metadata") or {}),
            usage=dict(data.get("usage") or {}),
        )
