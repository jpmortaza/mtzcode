"""Tool pra scaffold de um workspace de projeto.

Dá ao agente a capacidade de, recebendo um caminho, criar a estrutura
padrão de um projeto de software (README, PRD, ARCH, CHANGELOG, docs/,
.env.example, .gitignore, etc) via ``mtzcode.agents.workspace``.

A tool é **destrutiva** (cria arquivos no disco), portanto passa pela
confirmação do usuário no CLI. Por default é idempotente — não
sobrescreve nada que já existe. Com ``force=true`` sobrescreve.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from mtzcode.tools.base import Tool, ToolError


class ScaffoldWorkspaceArgs(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Caminho da pasta onde o workspace será criado. Criada se "
            "não existir. Pode ser absoluta ou relativa ao cwd."
        ),
    )
    project_name: str | None = Field(
        default=None,
        description=(
            "Nome do projeto usado nos templates. Se omitido, usa o "
            "nome da pasta."
        ),
    )
    tagline: str = Field(
        default="Projeto criado via mtzcode",
        description="Linha curta pro topo do README.",
    )
    force: bool = Field(
        default=False,
        description=(
            "Se True, sobrescreve arquivos já existentes. Default False — "
            "arquivos existentes são preservados e reportados como skipped."
        ),
    )


class ScaffoldWorkspaceTool(Tool):
    name = "scaffold_workspace"
    destructive = True
    description = (
        "Cria a estrutura padrão de um projeto de software em uma pasta: "
        "README.md, docs/PRD.md, docs/ARCHITECTURE.md, docs/PLAN.md, "
        "docs/CHANGELOG.md, .env.example, .env (vazio), .gitignore "
        "(com .env bloqueado), backend/, frontend/, scripts/. "
        "Idempotente por default (não sobrescreve nada). Use com `force=true` "
        "só se quiser resetar."
    )
    Args = ScaffoldWorkspaceArgs

    def run(self, args: ScaffoldWorkspaceArgs) -> str:  # type: ignore[override]
        # Import lazy pra não criar dependência circular agent → tool → agent.
        from mtzcode.agents.workspace import scaffold_workspace

        try:
            result = scaffold_workspace(
                args.path,
                project_name=args.project_name,
                tagline=args.tagline,
                force=args.force,
            )
        except OSError as exc:
            raise ToolError(f"erro ao criar workspace: {exc}") from exc

        return result.summary()
