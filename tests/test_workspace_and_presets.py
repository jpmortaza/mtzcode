"""Testes pro scaffold de workspace, inventário de docs e presets.

Cobre:

- ``scaffold_workspace`` cria a estrutura esperada e é idempotente
- ``.env`` vai pro ``.gitignore`` e ``.env`` nunca é sobrescrito
- ``inventory_docs`` respeita extensões suportadas e ignora pastas ruído
- ``fullstack_dev_agent`` e ``notebook_chat_agent`` devolvem configs
  coerentes com o preset declarado
- A ``ScaffoldWorkspaceTool`` está registrada no ``default_registry``
"""
from __future__ import annotations

from pathlib import Path

from mtzcode.agents import (
    AgentConfig,
    fullstack_dev_agent,
    inventory_docs,
    notebook_chat_agent,
    refactor_bot_agent,
    scaffold_workspace,
)
from mtzcode.tools import default_registry


# ---------------------------------------------------------------------------
# scaffold_workspace
# ---------------------------------------------------------------------------
def test_scaffold_creates_standard_files(tmp_path: Path) -> None:
    result = scaffold_workspace(tmp_path / "myapp", project_name="MyApp")

    ws = result.workspace
    expected = [
        ".gitignore",
        ".env.example",
        ".env",
        "README.md",
        "CONTRIBUTING.md",
        "docs/PRD.md",
        "docs/ARCHITECTURE.md",
        "docs/PLAN.md",
        "docs/CHANGELOG.md",
        "backend/.gitkeep",
        "frontend/.gitkeep",
        "scripts/.gitkeep",
        "docs/decisions/.gitkeep",
    ]
    for rel in expected:
        assert (ws / rel).exists(), f"faltou: {rel}"

    # Todos foram criados nesta rodada
    for rel in expected:
        assert rel in result.created
    assert not result.skipped


def test_gitignore_blocks_env(tmp_path: Path) -> None:
    result = scaffold_workspace(tmp_path / "proj")
    gi = (result.workspace / ".gitignore").read_text(encoding="utf-8")
    # .env geral bloqueado, .env.example liberado
    assert "\n.env\n" in gi
    assert "!.env.example" in gi


def test_env_example_has_no_real_secrets(tmp_path: Path) -> None:
    result = scaffold_workspace(tmp_path / "proj")
    env_ex = (result.workspace / ".env.example").read_text(encoding="utf-8")
    # Os valores de serviços externos devem estar vazios no template
    assert "OPENAI_API_KEY=\n" in env_ex
    assert "ANTHROPIC_API_KEY=\n" in env_ex


def test_scaffold_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "idem"
    scaffold_workspace(target)

    # Modifica um arquivo existente pra provar que não é sobrescrito
    readme = target / "README.md"
    readme.write_text("# Customizado\nconteúdo manual", encoding="utf-8")

    result2 = scaffold_workspace(target)

    # README deve ter ido pra skipped, não created
    assert "README.md" in result2.skipped
    assert "README.md" not in result2.created
    # Conteúdo preservado
    assert readme.read_text(encoding="utf-8") == "# Customizado\nconteúdo manual"


def test_scaffold_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "forced"
    scaffold_workspace(target)

    (target / "README.md").write_text("custom", encoding="utf-8")

    result2 = scaffold_workspace(target, force=True)
    assert "README.md" in result2.created
    # Conteúdo foi sobrescrito pelo template
    assert (target / "README.md").read_text(encoding="utf-8").startswith("# ")


def test_scaffold_env_file_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "envtest"
    scaffold_workspace(target)

    env_path = target / ".env"
    env_path.write_text("SECRET=real-value\n", encoding="utf-8")

    # Mesmo com force=True, o .env local não pode ser sobrescrito
    result2 = scaffold_workspace(target, force=True)
    assert env_path.read_text(encoding="utf-8") == "SECRET=real-value\n"
    assert ".env" in result2.skipped


# ---------------------------------------------------------------------------
# inventory_docs
# ---------------------------------------------------------------------------
def test_inventory_docs_finds_supported(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"not a doc")

    inv = inventory_docs(tmp_path)
    rels = {p.relative_to(tmp_path).as_posix() for p in inv.docs}
    assert rels == {"a.md", "b.txt", "sub/c.md"}
    assert "image.png" not in rels


def test_inventory_docs_skips_noise_dirs(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    for noise in ("node_modules", ".git", "__pycache__", ".venv"):
        d = tmp_path / noise
        d.mkdir()
        (d / "ignored.md").write_text("noise", encoding="utf-8")

    inv = inventory_docs(tmp_path)
    rels = {p.relative_to(tmp_path).as_posix() for p in inv.docs}
    assert rels == {"real.md"}


def test_inventory_docs_as_text_lists_files(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("x", encoding="utf-8")
    inv = inventory_docs(tmp_path)
    text = inv.as_text()
    assert "readme.md" in text


def test_inventory_docs_empty_folder(tmp_path: Path) -> None:
    inv = inventory_docs(tmp_path)
    assert inv.docs == []
    assert "sem documentos" in inv.as_text()


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def test_fullstack_preset_shape(tmp_path: Path) -> None:
    cfg = fullstack_dev_agent(tmp_path / "projeto", name="DevX")
    assert isinstance(cfg, AgentConfig)
    assert cfg.name == "DevX"
    assert cfg.model == "qwen-14b"
    assert "PRD" in cfg.system
    assert "PLAN" in cfg.system
    assert ".env" in cfg.system
    assert str((tmp_path / "projeto").resolve()) in cfg.system
    assert "core" in cfg.tool_groups
    assert cfg.metadata["preset"] == "fullstack_dev"


def test_fullstack_preset_extra_system(tmp_path: Path) -> None:
    cfg = fullstack_dev_agent(
        tmp_path / "p", extra_system="Use Postgres 16 obrigatoriamente."
    )
    assert "Postgres 16 obrigatoriamente" in cfg.system
    assert "Instruções específicas deste projeto" in cfg.system


def test_notebook_preset_inventory_in_system(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# a", encoding="utf-8")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4")

    cfg = notebook_chat_agent(tmp_path, name="Nb")
    assert "a.md" in cfg.system
    assert "b.pdf" in cfg.system
    # Modo read-only: write/edit/bash desabilitados
    assert "write" in cfg.disabled_tools
    assert "edit" in cfg.disabled_tools
    assert "bash" in cfg.disabled_tools
    assert cfg.metadata["preset"] == "notebook_chat"
    assert cfg.metadata["doc_count"] == 2


def test_refactor_preset_shape() -> None:
    cfg = refactor_bot_agent()
    assert cfg.metadata["preset"] == "refactor_bot"
    assert "refactor" in cfg.system.lower() or "refato" in cfg.system.lower()
    assert cfg.disabled_tools == []


# ---------------------------------------------------------------------------
# Tool registrada
# ---------------------------------------------------------------------------
def test_scaffold_tool_registered_in_core() -> None:
    reg = default_registry(groups=["core"])
    assert "scaffold_workspace" in reg.names()
    tool = reg.get("scaffold_workspace")
    schema = tool.schema(slim=True)
    assert schema["function"]["name"] == "scaffold_workspace"
    assert "path" in schema["function"]["parameters"]["properties"]


def test_scaffold_tool_creates_workspace(tmp_path: Path) -> None:
    from mtzcode.tools.scaffold import ScaffoldWorkspaceTool

    tool = ScaffoldWorkspaceTool()
    result = tool.call({"path": str(tmp_path / "via-tool")})
    assert "Workspace:" in result
    assert (tmp_path / "via-tool" / "README.md").exists()
    assert (tmp_path / "via-tool" / ".gitignore").exists()
