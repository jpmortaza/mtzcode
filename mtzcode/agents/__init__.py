"""Infra de agentes do mtzcode — modelo Agent/Environment/Session.

Espelha a arquitetura do Claude Managed Agents (Agent persistido e
versionado → Environment reutilizável → Session que referencia ambos e
produz um stream de eventos), adaptada pro mtzcode local.

Uso típico:

    from mtzcode.agents import (
        AgentConfig, EnvironmentConfig, Stores, open_session,
    )

    stores = Stores()  # aponta pra ~/.mtzcode/

    # Uma vez:
    agent = stores.agents.create(
        AgentConfig(
            name="Refactor Bot",
            model="qwen-14b",
            system="Você é um refactorer paciente e conservador.",
            tool_groups=["core", "documents"],
            description="Faz refatorações pequenas e testadas",
        )
    )
    env = stores.environments.create(
        EnvironmentConfig(name="repo-local", cwd="/home/user/projeto")
    )

    # Cada execução:
    sess = open_session(
        stores, agent_id=agent.id, environment_id=env.id, title="fix bug 123"
    )
    result = sess.send_user("Abra o módulo parser.py e explique.")
    print(result.text)
    sess.close()

Ver ``mtzcode/agents/models.py``, ``store.py``, ``events.py``, ``runner.py``.
"""
from mtzcode.agents.events import (
    EVT_AGENT_MESSAGE,
    EVT_AGENT_MESSAGE_DELTA,
    EVT_AGENT_TOOL_DENIED,
    EVT_AGENT_TOOL_ERROR,
    EVT_AGENT_TOOL_RESULT,
    EVT_AGENT_TOOL_USE,
    EVT_SESSION_IDLE,
    EVT_SESSION_RUNNING,
    EVT_SESSION_TERMINATED,
    EVT_USER_CUSTOM_TOOL_RESULT,
    EVT_USER_MESSAGE,
    ManagedEvent,
    bridge,
    idle_event,
    running_event,
    terminated_event,
    user_message_event,
)
from mtzcode.agents.models import (
    AgentConfig,
    AgentRecord,
    AgentRef,
    AgentVersion,
    EnvironmentConfig,
    EnvironmentRecord,
    SessionRecord,
    SessionStatus,
)
from mtzcode.agents.runner import (
    ClientFactory,
    ManagedSession,
    TurnResult,
    open_session,
)
from mtzcode.agents.store import (
    AgentStore,
    EnvironmentStore,
    SessionStore,
    StoreError,
    Stores,
)

__all__ = [
    # models
    "AgentConfig",
    "AgentRecord",
    "AgentRef",
    "AgentVersion",
    "EnvironmentConfig",
    "EnvironmentRecord",
    "SessionRecord",
    "SessionStatus",
    # stores
    "AgentStore",
    "EnvironmentStore",
    "SessionStore",
    "Stores",
    "StoreError",
    # events
    "ManagedEvent",
    "bridge",
    "idle_event",
    "running_event",
    "terminated_event",
    "user_message_event",
    "EVT_AGENT_MESSAGE",
    "EVT_AGENT_MESSAGE_DELTA",
    "EVT_AGENT_TOOL_USE",
    "EVT_AGENT_TOOL_RESULT",
    "EVT_AGENT_TOOL_ERROR",
    "EVT_AGENT_TOOL_DENIED",
    "EVT_SESSION_RUNNING",
    "EVT_SESSION_IDLE",
    "EVT_SESSION_TERMINATED",
    "EVT_USER_MESSAGE",
    "EVT_USER_CUSTOM_TOOL_RESULT",
    # runner
    "ClientFactory",
    "ManagedSession",
    "TurnResult",
    "open_session",
]
