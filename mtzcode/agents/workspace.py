"""Scaffold de workspace de desenvolvimento.

Dada uma pasta alvo, gera a estrutura padrão de um projeto de software
que o agente vai usar como "casa": documentação padrão, PRD inicial,
``.env.example`` + ``.gitignore`` protegendo segredos, pasta ``docs/`` pra
material de referência, e placeholders de plano/decisões de arquitetura.

Design:
  - **Idempotente**: chamar duas vezes não sobrescreve nada que já existe.
    Cada arquivo só é criado se faltar. Permite evoluir um projeto real.
  - **Respeita git**: se já é um repo git, não reinicializa. Se não é, não
    força ``git init`` — só deixa a estrutura pronta.
  - **Segurança de segredos**: o ``.gitignore`` sempre inclui ``.env`` e
    derivados; o ``.env.example`` nunca contém valores reais.
  - **Stack default Python + React fullstack**: as pastas ``backend/``,
    ``frontend/`` e ``scripts/`` são criadas vazias (com ``.gitkeep``) pra
    o agente preencher quando o PRD for processado.

Usado por:
  - ``mtzcode.agents.tools.WorkspaceScaffoldTool`` (pra o agente chamar)
  - ``mtzcode.agents.presets.fullstack_dev_workspace()`` (setup programático)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Conteúdo dos arquivos padrão
# ---------------------------------------------------------------------------
_GITIGNORE = """\
# Segredos — nunca commitar
.env
.env.*
!.env.example
secrets/
*.key
*.pem

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
.venv/
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# Node / React
node_modules/
.pnp
.pnp.js
coverage/
.next/
out/
.nuxt/
dist-ssr/
.vite/

# IDE
.idea/
.vscode/
*.swp
*.swo
.DS_Store
Thumbs.db

# Logs e DBs locais
*.log
logs/
*.sqlite
*.sqlite3
*.db

# mtzcode
.mtzcode/
"""

_ENV_EXAMPLE = """\
# Copie para .env e preencha com valores reais.
# O arquivo .env NUNCA vai para o git (veja .gitignore).

# Backend
DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
SECRET_KEY=troque-isso-em-producao
LOG_LEVEL=info

# Serviços externos
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
STRIPE_SECRET_KEY=

# Frontend (prefixo VITE_ para o Vite expor ao browser)
VITE_API_URL=http://localhost:8000
"""

_README_TEMPLATE = """\
# {project_name}

> {tagline}

## Visão geral

Descreva em uma ou duas frases o que este projeto faz.

## Stack

- **Backend**: Python (FastAPI) — ver `backend/`
- **Frontend**: React + TypeScript (Vite) — ver `frontend/`
- **Banco**: ajustar conforme necessidade (Postgres / SQLite para dev)

## Quick start

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env  # preencha as variáveis
uvicorn app.main:app --reload

# Frontend (em outro terminal)
cd frontend
npm install
npm run dev
```

## Documentação

- [PRD — Product Requirements](docs/PRD.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Plano de desenvolvimento](docs/PLAN.md)
- [Changelog](docs/CHANGELOG.md)
- [Decisões (ADR)](docs/decisions/)

## Senhas e segredos

Copie `.env.example` para `.env` e preencha localmente. O arquivo `.env`
está no `.gitignore` — **nunca** commite segredos reais.
"""

_PRD_TEMPLATE = """\
# PRD — {project_name}

> Criado em: {date}
> Status: rascunho

## 1. Problema

Qual problema este produto resolve? Quem é afetado hoje e como?

## 2. Usuários

- **Usuário primário**: quem vai usar mais? Que "job to be done" tem?
- **Usuário secundário**: quem mais interage?

## 3. Objetivos

### O que está no escopo (MVP)

- [ ] ...
- [ ] ...

### O que NÃO está no escopo

- ...

## 4. Requisitos funcionais

1. ...
2. ...

## 5. Requisitos não-funcionais

- **Performance**: ...
- **Segurança**: ...
- **Disponibilidade**: ...

## 6. Fluxos principais

Liste ou desenhe os fluxos de uso mais importantes.

## 7. Métricas de sucesso

Como sabemos que funcionou? (Ex: X usuários ativos, Y% de taxa de conversão.)

## 8. Riscos e premissas

- **Risco**: ... → **Mitigação**: ...
- **Premissa**: ... (a validar)

## 9. Referências

- Links, docs, screenshots.
"""

_ARCH_TEMPLATE = """\
# Arquitetura — {project_name}

> Documento vivo. Atualize quando decisões importantes mudarem.

## Visão geral

Descrição em alto nível dos componentes e como eles se conectam.

```
[frontend (React)]  →  [backend (FastAPI)]  →  [DB (Postgres)]
                              ↓
                      [serviços externos]
```

## Componentes

### Frontend (`frontend/`)
- **Framework**: React 18 + TypeScript + Vite
- **Roteamento**: React Router
- **Estado**: a decidir (Zustand / Redux / Context)
- **HTTP**: fetch / axios

### Backend (`backend/`)
- **Framework**: FastAPI
- **ORM**: SQLAlchemy / SQLModel
- **Auth**: JWT / OAuth
- **Testes**: pytest

### Dados
- **Produção**: Postgres
- **Dev local**: SQLite (opcional)
- **Migrações**: Alembic

## Decisões importantes

Adicione ADRs em `docs/decisions/NNNN-titulo.md` conforme for decidindo.
Formato sugerido:

```
# ADR NNNN: Título

## Contexto
## Decisão
## Consequências
```

## Deploy

- **Dev local**: `uvicorn` + `vite dev` em portas separadas
- **Produção**: a definir (Docker + Railway / Fly.io / VPS)
"""

_PLAN_TEMPLATE = """\
# Plano de desenvolvimento — {project_name}

> Este arquivo é o plano de execução do agente. Ele lê o PRD e vai
> atualizando aqui em cada rodada — cada item tem status e link pra
> commit/PR quando for implementado.

## Status geral

- [ ] PRD revisado e aprovado
- [ ] Arquitetura inicial escrita
- [ ] Esqueleto do backend criado
- [ ] Esqueleto do frontend criado
- [ ] Banco de dados configurado
- [ ] Autenticação
- [ ] Features do MVP
- [ ] Testes
- [ ] Deploy

## Tarefas ativas

_(o agente preenche aqui a partir do PRD)_

## Concluído

_(movido pra cá ao fim de cada tarefa)_

## Bloqueios

- Nenhum.
"""

_CHANGELOG_TEMPLATE = """\
# Changelog — {project_name}

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento: [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Adicionado
- Estrutura inicial do projeto ({date}).

## [0.0.1] - {date}

### Adicionado
- Scaffold inicial via mtzcode.
"""

_CONTRIBUTING = """\
# Contribuindo

## Setup

Ver [README.md](README.md) pra instalar dependências.

## Fluxo de trabalho

1. Abra uma issue descrevendo o que vai fazer (ou pegue uma existente).
2. Crie um branch: `git checkout -b feat/nome-curto`.
3. Faça commits pequenos e atômicos.
4. Rode os testes: `pytest` no backend, `npm test` no frontend.
5. Abra um PR apontando pra `main`.

## Estilo de commit

Siga [Conventional Commits](https://www.conventionalcommits.org/pt-br/):
`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
"""

_BACKEND_GITKEEP = ""
_FRONTEND_GITKEEP = ""
_SCRIPTS_GITKEEP = ""
_DECISIONS_GITKEEP = ""


# ---------------------------------------------------------------------------
# Datamodel
# ---------------------------------------------------------------------------
@dataclass
class ScaffoldResult:
    """Relatório do scaffold."""

    workspace: Path
    created: list[str]   # paths relativos criados
    skipped: list[str]   # paths relativos que já existiam

    def summary(self) -> str:
        lines = [f"Workspace: {self.workspace}"]
        if self.created:
            lines.append(f"\nCriados ({len(self.created)}):")
            lines.extend(f"  + {p}" for p in self.created)
        if self.skipped:
            lines.append(f"\nJá existiam ({len(self.skipped)}):")
            lines.extend(f"  = {p}" for p in self.skipped)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
def scaffold_workspace(
    target: str | Path,
    *,
    project_name: str | None = None,
    tagline: str = "Projeto criado via mtzcode",
    force: bool = False,
) -> ScaffoldResult:
    """Cria a estrutura padrão de workspace em ``target``.

    Args:
        target: Pasta destino. É criada se não existir.
        project_name: Nome exibido nos templates. Default = nome da pasta.
        tagline: Linha curta pro README.
        force: Se True, sobrescreve arquivos existentes. Default False —
            pula silenciosamente e lista em ``skipped``.

    Returns:
        ``ScaffoldResult`` com a lista de criados vs pulados.
    """
    workspace = Path(target).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    if project_name is None:
        project_name = workspace.name or "meu-projeto"

    date = datetime.now().strftime("%Y-%m-%d")
    ctx = {"project_name": project_name, "tagline": tagline, "date": date}

    created: list[str] = []
    skipped: list[str] = []

    def _write(rel: str, content: str) -> None:
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            skipped.append(rel)
            return
        path.write_text(content, encoding="utf-8")
        created.append(rel)

    # Raiz
    _write(".gitignore", _GITIGNORE)
    _write(".env.example", _ENV_EXAMPLE)
    _write("README.md", _README_TEMPLATE.format(**ctx))
    _write("CONTRIBUTING.md", _CONTRIBUTING)

    # docs/
    _write("docs/PRD.md", _PRD_TEMPLATE.format(**ctx))
    _write("docs/ARCHITECTURE.md", _ARCH_TEMPLATE.format(**ctx))
    _write("docs/PLAN.md", _PLAN_TEMPLATE.format(**ctx))
    _write("docs/CHANGELOG.md", _CHANGELOG_TEMPLATE.format(**ctx))

    # Pastas de stack — .gitkeep pra manter no repo
    _write("backend/.gitkeep", _BACKEND_GITKEEP)
    _write("frontend/.gitkeep", _FRONTEND_GITKEEP)
    _write("scripts/.gitkeep", _SCRIPTS_GITKEEP)
    _write("docs/decisions/.gitkeep", _DECISIONS_GITKEEP)

    # .env de verdade — criado vazio SE não existir, nunca sobrescrito.
    env_path = workspace / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# Arquivo local de segredos. NÃO COMMITAR.\n"
            "# Copie as chaves de .env.example e preencha aqui.\n",
            encoding="utf-8",
        )
        created.append(".env")
    else:
        skipped.append(".env")

    return ScaffoldResult(workspace=workspace, created=created, skipped=skipped)


# ---------------------------------------------------------------------------
# Docs folder (modo "notebook") — varre uma pasta e lista os docs
# ---------------------------------------------------------------------------
_DOC_EXTENSIONS = {
    ".md", ".txt", ".rst",
    ".pdf",
    ".docx", ".doc",
    ".xlsx", ".xls", ".csv",
    ".html", ".htm",
    ".json", ".yaml", ".yml",
}


@dataclass
class DocInventory:
    """Inventário de uma pasta de documentos (modo notebook-chat)."""

    root: Path
    docs: list[Path]

    def as_text(self, *, max_items: int = 200) -> str:
        """Resumo textual pra injetar no system prompt do agente."""
        if not self.docs:
            return f"(sem documentos suportados em {self.root})"
        lines = [f"Documentos disponíveis em {self.root}:"]
        for p in self.docs[:max_items]:
            try:
                rel = p.relative_to(self.root)
            except ValueError:
                rel = p
            lines.append(f"- {rel}")
        if len(self.docs) > max_items:
            lines.append(f"... (+{len(self.docs) - max_items} arquivos)")
        return "\n".join(lines)


def inventory_docs(
    root: str | Path,
    *,
    extensions: set[str] | None = None,
    max_files: int = 1000,
    include_filter: Callable[[Path], bool] | None = None,
) -> DocInventory:
    """Lista recursivamente arquivos de documentação numa pasta.

    Pula qualquer coisa dentro de ``.git``, ``node_modules``, ``.venv``,
    ``__pycache__``, ``dist``, ``build``.
    """
    base = Path(root).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return DocInventory(root=base, docs=[])

    exts = extensions if extensions is not None else _DOC_EXTENSIONS
    skip_dirs = {
        ".git", "node_modules", ".venv", "venv", "__pycache__",
        "dist", "build", ".next", ".mtzcode", ".pytest_cache",
        ".ruff_cache", ".mypy_cache",
    }

    found: list[Path] = []
    for path in base.rglob("*"):
        if len(found) >= max_files:
            break
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in exts:
            continue
        if include_filter is not None and not include_filter(path):
            continue
        found.append(path)

    found.sort()
    return DocInventory(root=base, docs=found)
