"""Persistência dos records da infra de agentes.

Três stores, cada um responsável por um tipo:

- ``AgentStore``       → ``~/.mtzcode/agents/<id>.json``
- ``EnvironmentStore`` → ``~/.mtzcode/environments/<id>.json``
- ``SessionStore``     → ``~/.mtzcode/sessions/<id>.json`` (metadados; o log
                          JSONL dos eventos continua em ``~/.mtzcode/logs/``
                          via ``SessionLogger``)

Write é atômico: escreve num ``.tmp`` e renomeia. Read é tolerante a arquivos
corrompidos (pula e loga). Nenhum lock — o mtzcode roda single-user local.

``base_dir`` é injetável pros testes não tocarem o home do usuário.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from mtzcode.agents.models import (
    AgentConfig,
    AgentRecord,
    AgentRef,
    EnvironmentConfig,
    EnvironmentRecord,
    SessionRecord,
    _now_iso,
    new_session_id,
)


DEFAULT_BASE = Path.home() / ".mtzcode"


class StoreError(RuntimeError):
    """Erro de IO/consistência no store (arquivo sumiu, id desconhecido...)."""


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, data: dict) -> None:
    """Escreve JSON atomicamente: tmp + rename. Cria pai se faltar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# AgentStore
# ---------------------------------------------------------------------------
class AgentStore:
    """CRUD + versionamento de agentes.

    Lifecycle igual ao Claude Managed Agents:
      create → get/list/update (bumpa versão) → archive (sem delete)

    ``update()`` aceita um ``AgentConfig`` inteiro (replace semantics) ou um
    dict de patch via ``**updates``. Sempre cria uma versão nova imutável.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        base = Path(base_dir) if base_dir is not None else DEFAULT_BASE
        self.dir = base / "agents"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return self.dir / f"{agent_id}.json"

    # ------------------------------------------------------------------
    def create(self, config: AgentConfig) -> AgentRecord:
        record = AgentRecord.new(config)
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def get(self, agent_id: str) -> AgentRecord:
        path = self._path(agent_id)
        if not path.exists():
            raise StoreError(f"agent não encontrado: {agent_id}")
        return AgentRecord.from_dict(_read_json(path))

    def list(self, include_archived: bool = False) -> list[AgentRecord]:
        out: list[AgentRecord] = []
        for path in sorted(self.dir.glob("agent_*.json")):
            try:
                rec = AgentRecord.from_dict(_read_json(path))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            if rec.is_archived and not include_archived:
                continue
            out.append(rec)
        # Mais recente primeiro.
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    def update(
        self,
        agent_id: str,
        *,
        config: AgentConfig | None = None,
        **patch,
    ) -> AgentRecord:
        """Bumpa a versão do agent. Dois modos:

        1. ``update(id, config=novo_config)``       — replace
        2. ``update(id, system="...", tool_groups=[...])`` — patch em cima
           do config atual
        """
        record = self.get(agent_id)
        if record.is_archived:
            raise StoreError(f"agent arquivado: {agent_id}")

        if config is None:
            base = record.current.config.to_dict()
            base.update(patch)
            new_config = AgentConfig.from_dict(base)
        else:
            if patch:
                raise TypeError("use config= OU kwargs, não os dois")
            new_config = config

        record.bump_version(new_config)
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def archive(self, agent_id: str) -> AgentRecord:
        record = self.get(agent_id)
        if record.is_archived:
            return record
        record.archived_at = _now_iso()
        record.updated_at = record.archived_at
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def resolve(self, ref: AgentRef) -> tuple[AgentRecord, int]:
        """Resolve uma ``AgentRef`` pra (record, version_real).

        Se ``ref.version is None`` usa a versão atual. Levanta ``StoreError``
        se o id ou a versão pinada não existirem.
        """
        record = self.get(ref.id)
        if ref.version is None:
            return record, record.version
        version = record.find_version(ref.version)
        if version is None:
            raise StoreError(
                f"agent {ref.id} não tem versão {ref.version}"
            )
        return record, version.version


# ---------------------------------------------------------------------------
# EnvironmentStore
# ---------------------------------------------------------------------------
class EnvironmentStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        base = Path(base_dir) if base_dir is not None else DEFAULT_BASE
        self.dir = base / "environments"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, env_id: str) -> Path:
        return self.dir / f"{env_id}.json"

    def create(self, config: EnvironmentConfig) -> EnvironmentRecord:
        record = EnvironmentRecord.new(config)
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def get(self, env_id: str) -> EnvironmentRecord:
        path = self._path(env_id)
        if not path.exists():
            raise StoreError(f"environment não encontrado: {env_id}")
        return EnvironmentRecord.from_dict(_read_json(path))

    def list(self, include_archived: bool = False) -> list[EnvironmentRecord]:
        out: list[EnvironmentRecord] = []
        for path in sorted(self.dir.glob("env_*.json")):
            try:
                rec = EnvironmentRecord.from_dict(_read_json(path))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            if rec.is_archived and not include_archived:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    def update(self, env_id: str, config: EnvironmentConfig) -> EnvironmentRecord:
        record = self.get(env_id)
        if record.is_archived:
            raise StoreError(f"environment arquivado: {env_id}")
        record.config = config
        record.updated_at = _now_iso()
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def archive(self, env_id: str) -> EnvironmentRecord:
        record = self.get(env_id)
        if record.is_archived:
            return record
        record.archived_at = _now_iso()
        record.updated_at = record.archived_at
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def delete(self, env_id: str) -> None:
        path = self._path(env_id)
        if not path.exists():
            raise StoreError(f"environment não encontrado: {env_id}")
        path.unlink()


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------
class SessionStore:
    """Metadados de sessões.

    Não duplica o conteúdo do JSONL — apenas aponta pra ele via
    ``events_path``. Integra com o ``session_log.SessionLogger`` existente.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        base = Path(base_dir) if base_dir is not None else DEFAULT_BASE
        self.dir = base / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def create(
        self,
        *,
        agent: AgentRef,
        environment_id: str,
        events_path: str,
        title: str = "",
        metadata: dict | None = None,
    ) -> SessionRecord:
        now = _now_iso()
        record = SessionRecord(
            id=new_session_id(),
            agent=agent,
            environment_id=environment_id,
            events_path=events_path,
            status="idle",
            title=title,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
            usage={},
        )
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def get(self, session_id: str) -> SessionRecord:
        path = self._path(session_id)
        if not path.exists():
            raise StoreError(f"session não encontrada: {session_id}")
        return SessionRecord.from_dict(_read_json(path))

    def save(self, record: SessionRecord) -> SessionRecord:
        """Re-serializa o record inteiro. Chamado pelo runner a cada transição."""
        record.updated_at = _now_iso()
        _atomic_write_json(self._path(record.id), record.to_dict())
        return record

    def set_status(self, session_id: str, status: str) -> SessionRecord:
        record = self.get(session_id)
        record.status = status
        return self.save(record)

    def list(
        self,
        include_archived: bool = False,
        agent_id: str | None = None,
    ) -> list[SessionRecord]:
        out: list[SessionRecord] = []
        for path in sorted(self.dir.glob("sess_*.json")):
            try:
                rec = SessionRecord.from_dict(_read_json(path))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
            if rec.is_archived and not include_archived:
                continue
            if agent_id is not None and rec.agent.id != agent_id:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    def archive(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        if record.is_archived:
            return record
        record.archived_at = _now_iso()
        record.status = "terminated"
        return self.save(record)

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if not path.exists():
            raise StoreError(f"session não encontrada: {session_id}")
        path.unlink()


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------
class Stores:
    """Conveniência: carrega os três stores apontando pra mesma base_dir."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else DEFAULT_BASE
        self.agents = AgentStore(self.base_dir)
        self.environments = EnvironmentStore(self.base_dir)
        self.sessions = SessionStore(self.base_dir)
