"""Perfis de modelo do mtzcode.

Cada perfil descreve um backend (Ollama local ou serviço cloud OpenAI-compatible)
com seu modelo, endpoint e variável de API key. Permite trocar de modelo no
REPL com `/modelo` sem reiniciar.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str            # ID curto, usado em comandos
    label: str           # Nome bonito mostrado pro usuário
    backend: str         # "ollama" ou "groq"
    model: str           # ID do modelo enviado pro backend
    base_url: str        # URL OpenAI-compatible (sem /chat/completions)
    api_key_env: str | None  # nome da env var com a key (None = sem auth)
    is_local: bool       # True = roda na máquina, False = chama serviço externo
    description: str

    @property
    def needs_api_key(self) -> bool:
        return self.api_key_env is not None


PROFILES: dict[str, Profile] = {
    "qwen-14b": Profile(
        name="qwen-14b",
        label="Qwen 2.5 Coder 14B (local)",
        backend="ollama",
        model="qwen2.5-coder:14b",
        base_url="http://localhost:11434/v1",
        api_key_env=None,
        is_local=True,
        description="Modelo grande local. Melhor qualidade. ~25 tok/s no M4. Cold start ~30s.",
    ),
    "qwen-7b": Profile(
        name="qwen-7b",
        label="Qwen 2.5 Coder 7B (local, leve)",
        backend="ollama",
        model="qwen2.5-coder:7b",
        base_url="http://localhost:11434/v1",
        api_key_env=None,
        is_local=True,
        description="Modelo leve local. Mais rápido (~50 tok/s), qualidade boa pra tarefas simples.",
    ),
    "groq-llama": Profile(
        name="groq-llama",
        label="Llama 3.3 70B (Groq cloud)",
        backend="groq",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        is_local=False,
        description="Cloud, super rápido (~300 tok/s). ATENÇÃO: dados saem da máquina.",
    ),
}

DEFAULT_PROFILE = "qwen-14b"


def get_profile(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(
            f"perfil desconhecido: {name}. Disponíveis: {', '.join(PROFILES)}"
        )
    return PROFILES[name]


def list_profiles() -> list[Profile]:
    return list(PROFILES.values())
