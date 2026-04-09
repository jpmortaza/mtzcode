"""Smoke test da infra de agentes (``mtzcode.agents``).

Cobre o caminho feliz end-to-end sem depender de Ollama:

1. Store CRUD (agents + environments + sessions)
2. Versionamento de agent (``update`` bumpa versão)
3. Resolução de ``AgentRef`` por id + versão
4. ``open_session`` + ``send_user`` com ``ChatClient`` fake
5. Bridge de eventos: ``AgentEvent`` → ``ManagedEvent`` dot-notation

O fake client simula o mínimo da interface do ``mtzcode.client.ChatClient``:
``chat_stream`` devolve um iterador de chunks OpenAI-compatible que o
``Agent._consume_stream`` sabe consumir. Isso evita tocar no Ollama e
mantém o teste rápido e determinístico.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mtzcode.agents import (
    AgentConfig,
    EnvironmentConfig,
    Stores,
    open_session,
)
from mtzcode.agents.events import (
    EVT_AGENT_MESSAGE,
    EVT_SESSION_IDLE,
    EVT_SESSION_RUNNING,
    EVT_USER_MESSAGE,
)
from mtzcode.agents.store import StoreError


# ---------------------------------------------------------------------------
# Fake ChatClient — devolve 1 chunk de texto e fecha. Sem tool calls.
# ---------------------------------------------------------------------------
class FakeClient:
    """Mínimo necessário pro ``Agent.run_streaming`` funcionar sem Ollama.

    Cada chamada a ``chat_stream`` devolve:
      - 1 ``content_delta`` com o texto pré-gravado
      - nenhum tool_call
    O ``Agent`` vê isso e encerra o loop com ``final_text`` igual ao texto.
    """

    def __init__(self, reply: str = "olá, sou o agente") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def chat_stream(self, messages, tools=None):  # noqa: D401
        self.calls.append({"messages": list(messages), "tools": tools})
        yield {
            "choices": [
                {
                    "delta": {"content": self.reply, "tool_calls": []},
                    "finish_reason": None,
                }
            ]
        }

    def chat(self, messages, tools=None):
        # Não usado (run_streaming é o caminho default do ManagedSession),
        # mas fornecido por completude.
        self.calls.append({"messages": list(messages), "tools": tools})
        return {"content": self.reply, "tool_calls": []}

    def close(self):
        pass


def _fake_factory(reply: str = "olá, sou o agente"):
    def factory(profile_name: str, timeout_s: float):
        return FakeClient(reply=reply)

    return factory


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------
def test_agent_store_create_get_list_archive(tmp_path: Path) -> None:
    stores = Stores(base_dir=tmp_path)

    config = AgentConfig(
        name="Refactor Bot",
        model="qwen-14b",
        system="Você refatora código.",
        tool_groups=["core"],
    )
    record = stores.agents.create(config)

    assert record.id.startswith("agent_")
    assert record.version == 1
    assert record.name == "Refactor Bot"

    # Persistência
    reloaded = stores.agents.get(record.id)
    assert reloaded.name == "Refactor Bot"
    assert reloaded.current.config.system == "Você refatora código."

    # Listagem não inclui archived por padrão
    listed = stores.agents.list()
    assert len(listed) == 1

    # Archive
    archived = stores.agents.archive(record.id)
    assert archived.is_archived
    assert stores.agents.list() == []
    assert len(stores.agents.list(include_archived=True)) == 1

    # get de id inexistente
    with pytest.raises(StoreError):
        stores.agents.get("agent_doesnotexist")


def test_agent_versioning_bumps_and_pins(tmp_path: Path) -> None:
    stores = Stores(base_dir=tmp_path)
    record = stores.agents.create(
        AgentConfig(name="A", model="qwen-14b", system="v1 prompt")
    )
    assert record.version == 1

    # Update via patch
    bumped = stores.agents.update(record.id, system="v2 prompt")
    assert bumped.version == 2
    assert bumped.current.config.system == "v2 prompt"
    assert len(bumped.versions) == 2
    assert bumped.versions[0].config.system == "v1 prompt"
    assert bumped.versions[1].config.system == "v2 prompt"

    # Update via replace
    new_cfg = AgentConfig(name="A", model="qwen-7b", system="v3 prompt")
    bumped2 = stores.agents.update(record.id, config=new_cfg)
    assert bumped2.version == 3
    assert bumped2.current.config.model == "qwen-7b"
    assert len(bumped2.versions) == 3

    # Resolve latest
    from mtzcode.agents.models import AgentRef

    rec, ver = stores.agents.resolve(AgentRef(id=record.id))
    assert ver == 3

    # Pin a uma versão antiga
    rec, ver = stores.agents.resolve(AgentRef(id=record.id, version=1))
    assert ver == 1
    assert rec.find_version(1).config.system == "v1 prompt"

    # Versão inexistente
    with pytest.raises(StoreError):
        stores.agents.resolve(AgentRef(id=record.id, version=99))


def test_environment_store(tmp_path: Path) -> None:
    stores = Stores(base_dir=tmp_path)
    env = stores.environments.create(
        EnvironmentConfig(name="repo-local", cwd=str(tmp_path))
    )
    assert env.id.startswith("env_")
    assert env.config.networking == "unrestricted"

    reloaded = stores.environments.get(env.id)
    assert reloaded.config.cwd == str(tmp_path)

    # Archive
    stores.environments.archive(env.id)
    assert stores.environments.list() == []


# ---------------------------------------------------------------------------
# open_session + send_user (fluxo completo com FakeClient)
# ---------------------------------------------------------------------------
def test_open_session_and_send_user(tmp_path: Path) -> None:
    stores = Stores(base_dir=tmp_path)

    agent = stores.agents.create(
        AgentConfig(
            name="Chat Simples",
            model="qwen-14b",
            system="você é um agente de teste",
            tool_groups=["core"],
        )
    )
    env = stores.environments.create(
        EnvironmentConfig(name="test-env", cwd=str(tmp_path))
    )

    session = open_session(
        stores,
        agent_id=agent.id,
        environment_id=env.id,
        title="primeiro turno",
        client_factory=_fake_factory("oi, sou o teste"),
    )

    assert session.session.id.startswith("sess_")
    assert session.session.status == "idle"
    assert session.session.title == "primeiro turno"

    # Envia mensagem do usuário
    result = session.send_user("olá")
    assert result.text == "oi, sou o teste"

    # Deve ter emitido na ordem: user.message, session.status_running,
    # (zero ou mais agent.message_delta), agent.message, session.status_idle
    types = [e.type for e in result.events]
    assert EVT_USER_MESSAGE in types
    assert EVT_SESSION_RUNNING in types
    assert EVT_AGENT_MESSAGE in types
    assert EVT_SESSION_IDLE in types
    # idle deve ser o último
    assert types[-1] == EVT_SESSION_IDLE

    # Stop reason = end_turn
    idle_evt = [e for e in result.events if e.type == EVT_SESSION_IDLE][-1]
    assert idle_evt.payload["stop_reason"]["type"] == "end_turn"

    # Persistência: status volta pra idle no store
    stored = stores.sessions.get(session.session.id)
    assert stored.status == "idle"

    # Aponta pro JSONL de eventos e o arquivo existe
    assert Path(stored.events_path).exists()

    # Fecha
    session.close()
    closed = stores.sessions.get(session.session.id)
    assert closed.status == "terminated"


def test_session_lists_filtered_by_agent(tmp_path: Path) -> None:
    stores = Stores(base_dir=tmp_path)

    agent_a = stores.agents.create(AgentConfig(name="A", model="qwen-14b"))
    agent_b = stores.agents.create(AgentConfig(name="B", model="qwen-14b"))
    env = stores.environments.create(
        EnvironmentConfig(name="e", cwd=str(tmp_path))
    )

    sa = open_session(
        stores,
        agent_id=agent_a.id,
        environment_id=env.id,
        client_factory=_fake_factory("a"),
    )
    sb = open_session(
        stores,
        agent_id=agent_b.id,
        environment_id=env.id,
        client_factory=_fake_factory("b"),
    )
    sa.close()
    sb.close()

    all_sessions = stores.sessions.list(include_archived=True)
    assert len(all_sessions) == 2

    only_a = stores.sessions.list(include_archived=True, agent_id=agent_a.id)
    assert len(only_a) == 1
    assert only_a[0].agent.id == agent_a.id
