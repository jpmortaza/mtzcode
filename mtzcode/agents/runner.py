"""Runner de sessão — liga um ``SessionRecord`` ao agent loop existente.

Dado um ``SessionRecord`` (+ ``AgentRecord`` + ``EnvironmentRecord``) resolvidos
pelo store, o ``ManagedSession`` monta:

1. Um ``ChatClient`` apontando pro profile do agent (``mtzcode.profiles``)
2. Um ``ToolRegistry`` filtrado pelos ``tool_groups`` + ``disabled_tools``
3. Um ``Agent`` (loop existente) com o system prompt do agent
4. Um ``SessionLogger`` escrevendo no ``events_path`` do SessionRecord
5. Um buffer de ``ManagedEvent`` construído via ``bridge()`` — o que a UI
   externa consome

``send_user(text)`` dispara ``Agent.run_streaming``, drena os eventos,
atualiza ``status`` no store, e devolve a resposta final + lista de
``ManagedEvent`` emitidos durante o turno.

**Por que injetar ``client_factory``:** testes não precisam do Ollama. Em
produção o default ``mtzcode.client.ChatClient`` é usado; em testes você
passa um fake que devolve respostas pré-gravadas.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from mtzcode.agent import Agent, AgentEvent
from mtzcode.agents.events import (
    EVT_AGENT_MESSAGE,
    ManagedEvent,
    bridge,
    idle_event,
    running_event,
    terminated_event,
    user_message_event,
)
from mtzcode.agents.models import (
    AgentRecord,
    AgentRef,
    EnvironmentRecord,
    SessionRecord,
)
from mtzcode.agents.store import Stores
from mtzcode.profiles import get_profile
from mtzcode.tools import default_registry
from mtzcode.tools.base import ToolError, ToolRegistry


# Factory de ChatClient. Default resolve o profile lazy pra não importar
# ``mtzcode.client`` em contexto de teste sem Ollama.
ClientFactory = Callable[[str, float], Any]


def _default_client_factory(profile_name: str, timeout_s: float):
    from mtzcode.client import ChatClient  # import lazy

    return ChatClient(get_profile(profile_name), timeout_s)


def _build_registry(
    tool_groups: list[str], disabled: list[str]
) -> ToolRegistry:
    """Constrói um ``ToolRegistry`` dos grupos pedidos, filtrando desabilitadas.

    Usa o mesmo monkey-patch da web UI: substitui ``registry.schemas`` e
    ``registry.get`` por closures que respeitam ``disabled_tools``. Isso
    mantém o agent loop 100% alheio à existência de tools desabilitadas.
    """
    registry = default_registry(groups=tool_groups or ["core"])
    if not disabled:
        return registry

    disabled_set = set(disabled)
    original_schemas = registry.schemas
    original_get = registry.get

    def filtered_schemas(slim: bool = False) -> list[dict[str, Any]]:
        try:
            all_schemas = original_schemas(slim=slim)  # type: ignore[call-arg]
        except TypeError:
            all_schemas = original_schemas()
        return [
            s
            for s in all_schemas
            if s.get("function", {}).get("name") not in disabled_set
        ]

    def filtered_get(name: str):
        if name in disabled_set:
            raise ToolError(f"tool `{name}` desabilitada neste agent")
        return original_get(name)

    registry.schemas = filtered_schemas  # type: ignore[method-assign]
    registry.get = filtered_get  # type: ignore[method-assign]
    return registry


# ---------------------------------------------------------------------------
# ManagedSession
# ---------------------------------------------------------------------------
@dataclass
class TurnResult:
    """Retorno de ``ManagedSession.send_user``.

    - ``text``   — resposta textual final do agente
    - ``events`` — lista de ``ManagedEvent`` emitidos durante o turno (útil
                   pra UIs que não querem um callback async)
    """

    text: str
    events: list[ManagedEvent]


class ManagedSession:
    """Sessão viva — mantém o ``Agent`` instanciado e o logger aberto.

    Uso típico:

        stores = Stores()
        session = open_session(
            stores, agent_id="agent_xyz", environment_id="env_abc",
            title="refactor do parser",
        )
        result = session.send_user("oi, liste os .py deste repo")
        print(result.text)
        session.close()

    Múltiplos turnos reaproveitam o mesmo ``Agent`` (histórico preservado).
    ``close()`` marca a sessão como ``terminated`` e fecha o logger.
    """

    def __init__(
        self,
        *,
        stores: Stores,
        session: SessionRecord,
        agent_record: AgentRecord,
        environment: EnvironmentRecord,
        agent_loop: Agent,
        session_logger: Any | None = None,
    ) -> None:
        self._stores = stores
        self.session = session
        self.agent_record = agent_record
        self.environment = environment
        self._loop = agent_loop
        self._logger = session_logger
        self._buffer: list[ManagedEvent] = []

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.session.id

    @property
    def status(self) -> str:
        return self.session.status

    def send_user(
        self,
        text: str,
        *,
        stream: bool = True,
    ) -> TurnResult:
        """Envia uma mensagem do usuário e roda o loop até o agent ficar idle.

        Marca a sessão como ``running`` no store, drena os eventos via bridge,
        volta pra ``idle`` no final. Se o loop levantar exceção, a sessão vai
        pra ``terminated`` com ``stop_reason=error``.
        """
        turn_events: list[ManagedEvent] = []

        def on_event(ev: AgentEvent) -> None:
            # Repassa pro logger (formato legacy) pra compat com session_log.
            if self._logger is not None:
                try:
                    self._logger.log_event(ev)
                except Exception:  # noqa: BLE001
                    pass
            # Traduz pra ManagedEvent.
            managed = bridge(ev)
            if managed is not None:
                turn_events.append(managed)
                self._buffer.append(managed)

        # Log da mensagem do usuário no JSONL legacy.
        if self._logger is not None:
            try:
                self._logger.log_user(text)
            except Exception:  # noqa: BLE001
                pass
        user_evt = user_message_event(text)
        turn_events.append(user_evt)
        self._buffer.append(user_evt)

        # Transição pra running.
        self.session = self._stores.sessions.set_status(self.session.id, "running")
        run_evt = running_event()
        turn_events.append(run_evt)
        self._buffer.append(run_evt)

        try:
            if stream:
                final_text = self._loop.run_streaming(text, on_event=on_event)
            else:
                final_text = self._loop.run(text, on_event=on_event)
        except Exception as exc:  # noqa: BLE001
            self.session.status = "terminated"
            self.session.metadata["last_error"] = str(exc)
            self._stores.sessions.save(self.session)
            term = terminated_event(reason="error")
            turn_events.append(term)
            self._buffer.append(term)
            raise

        # Emite um ``agent.message`` sintético no fim do turno, caso o bridge
        # não tenha capturado (o streaming emite deltas, não assistant_text).
        if final_text and not any(
            e.type == EVT_AGENT_MESSAGE for e in turn_events
        ):
            final_msg = ManagedEvent(
                type=EVT_AGENT_MESSAGE,
                payload={"content": [{"type": "text", "text": final_text}]},
            )
            turn_events.append(final_msg)
            self._buffer.append(final_msg)

        # Transição pra idle com stop_reason=end_turn.
        idle = idle_event("end_turn")
        turn_events.append(idle)
        self._buffer.append(idle)
        self.session = self._stores.sessions.set_status(self.session.id, "idle")

        return TurnResult(text=final_text, events=turn_events)

    def events(self) -> Iterator[ManagedEvent]:
        """Itera todos os ManagedEvents acumulados nesta sessão viva.

        NÃO esgota o buffer — chama repetidas vezes devolve os mesmos. Pra
        drain, use ``drain_events``.
        """
        yield from self._buffer

    def drain_events(self) -> list[ManagedEvent]:
        """Retorna e limpa o buffer. Útil pra polling por parte da UI."""
        out = self._buffer
        self._buffer = []
        return out

    def close(self, *, reason: str = "closed") -> None:
        """Marca a sessão como ``terminated``, fecha o logger."""
        if self._logger is not None:
            try:
                self._logger.close()
            except Exception:  # noqa: BLE001
                pass
            self._logger = None
        self.session.status = "terminated"
        self._stores.sessions.save(self.session)
        self._buffer.append(terminated_event(reason=reason))


# ---------------------------------------------------------------------------
# Factory — resolve todas as dependências e devolve uma ManagedSession pronta
# ---------------------------------------------------------------------------
def open_session(
    stores: Stores,
    *,
    agent_id: str,
    environment_id: str,
    title: str = "",
    metadata: dict | None = None,
    agent_version: int | None = None,
    client_factory: ClientFactory = _default_client_factory,
    session_logger_factory: Callable[[Path], Any] | None = None,
) -> ManagedSession:
    """Abre uma sessão nova, persiste o ``SessionRecord``, retorna live handle.

    - Resolve agent (+versão opcional) e environment no store
    - Monta ChatClient via ``client_factory`` (sobrescrevível em teste)
    - Monta registry filtrado pelos tool_groups + disabled_tools do agent
    - Instancia ``Agent`` com o system prompt da versão resolvida
    - Cria um ``SessionLogger`` apontando pra ``~/.mtzcode/logs/``
    - Escreve o ``SessionRecord`` no store e devolve a ``ManagedSession``
    """
    # 1. Resolve agent.
    ref = AgentRef(id=agent_id, version=agent_version)
    agent_record, resolved_version = stores.agents.resolve(ref)
    version = agent_record.find_version(resolved_version)
    if version is None:
        raise RuntimeError(
            f"versão {resolved_version} do agent {agent_id} não encontrada"
        )
    config = version.config

    # 2. Resolve environment.
    env_record = stores.environments.get(environment_id)
    env = env_record.config

    # 3. ChatClient + registry + Agent.
    client = client_factory(config.model, env.request_timeout_s)
    registry = _build_registry(config.tool_groups, config.disabled_tools)
    system_prompt = config.system or ""
    loop = Agent(client=client, registry=registry, system_prompt=system_prompt)

    # 4. SessionLogger apontando pra ~/.mtzcode/logs/ (mesmo local do legacy).
    if session_logger_factory is None:
        from mtzcode.session_log import SessionLogger

        logs_dir = stores.base_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        logger = SessionLogger(log_dir=logs_dir)
    else:
        logger = session_logger_factory(stores.base_dir / "logs")
    events_path = str(getattr(logger, "path", stores.base_dir / "logs" / "unknown.jsonl"))

    # 5. SessionRecord persistido.
    session_record = stores.sessions.create(
        agent=AgentRef(id=agent_record.id, version=resolved_version),
        environment_id=env_record.id,
        events_path=events_path,
        title=title,
        metadata=metadata,
    )

    # 6. Marca metadata inicial no logger (agent/env/sessao) pra tornar o
    # JSONL autocontido — se alguém olhar só pro arquivo depois, acha o ref.
    try:
        logger.log_meta("session_id", session_record.id)
        logger.log_meta("agent_id", agent_record.id)
        logger.log_meta("agent_version", resolved_version)
        logger.log_meta("environment_id", env_record.id)
        logger.log_meta("model_profile", config.model)
    except Exception:  # noqa: BLE001
        pass

    return ManagedSession(
        stores=stores,
        session=session_record,
        agent_record=agent_record,
        environment=env_record,
        agent_loop=loop,
        session_logger=logger,
    )
