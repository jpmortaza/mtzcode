"""Registry de tools do mtzcode.

Por padrão carrega só as tools CORE (filesystem + bash + web básico) pra
manter o contexto enxuto e a inferência rápida em modelos locais.

Tool groups extras (macos, browser, documents) são opt-in via env var
`MTZCODE_TOOL_GROUPS` (CSV) ou via `default_registry(groups=[...])`.

Exemplo:
    MTZCODE_TOOL_GROUPS=core,macos,documents mtzcode
    MTZCODE_TOOL_GROUPS=all mtzcode serve
"""
from __future__ import annotations

import os

from mtzcode.tools.applescript import AppleScriptTool
from mtzcode.tools.bash import BashTool
from mtzcode.tools.base import Tool, ToolError, ToolRegistry
from mtzcode.tools.browser import BrowserTool
from mtzcode.tools.clipboard import ClipboardReadTool, ClipboardWriteTool
from mtzcode.tools.docx import DocxReadTool, DocxWriteTool
from mtzcode.tools.edit import EditTool
from mtzcode.tools.glob import GlobTool
from mtzcode.tools.grep import GrepTool
from mtzcode.tools.notify import NotifyTool
from mtzcode.tools.open_app import OpenAppTool
from mtzcode.tools.open_url import OpenUrlTool
from mtzcode.tools.pdf import PdfFromMarkdownTool, PdfReadTool
from mtzcode.tools.read import ReadTool
from mtzcode.tools.screenshot import ScreenshotTool
from mtzcode.tools.search import SearchCodeTool
from mtzcode.tools.search_knowledge import SearchKnowledgeTool
from mtzcode.tools.text_writer import TextWriterTool
from mtzcode.tools.web_fetch import WebFetchTool
from mtzcode.tools.web_search import WebSearchTool
from mtzcode.tools.write import WriteTool
from mtzcode.tools.xlsx import XlsxReadTool, XlsxWriteTool


# Grupos de tools — controlam o tamanho do contexto enviado ao modelo.
TOOL_GROUPS: dict[str, list[type[Tool]]] = {
    # CORE: estritamente necessário pra trabalhar com código (10 tools)
    "core": [
        ReadTool,
        WriteTool,
        EditTool,
        GlobTool,
        GrepTool,
        SearchCodeTool,
        SearchKnowledgeTool,
        BashTool,
        WebFetchTool,
        OpenUrlTool,
    ],
    # WEB: pesquisa e navegador real
    "web": [WebSearchTool, BrowserTool],
    # MACOS: controle do sistema (apps, notificações, clipboard, screenshot)
    "macos": [
        AppleScriptTool,
        ClipboardReadTool,
        ClipboardWriteTool,
        NotifyTool,
        ScreenshotTool,
        OpenAppTool,
    ],
    # DOCUMENTS: leitura/escrita de docx, pdf, xlsx, textos longos
    "documents": [
        DocxReadTool,
        DocxWriteTool,
        PdfReadTool,
        PdfFromMarkdownTool,
        XlsxReadTool,
        XlsxWriteTool,
        TextWriterTool,
    ],
}


def _resolve_groups(groups: list[str] | None) -> list[str]:
    """Resolve a lista de grupos a partir de arg explícito, env var ou default."""
    if groups:
        return groups
    env = os.environ.get("MTZCODE_TOOL_GROUPS", "").strip()
    if env:
        return [g.strip() for g in env.split(",") if g.strip()]
    return ["core"]


def default_registry(groups: list[str] | None = None) -> ToolRegistry:
    """Cria o registry de tools.

    Por padrão carrega apenas o grupo `core` (10 tools, ~2-3k tokens de schemas).
    Use `groups=["core","macos"]` ou env `MTZCODE_TOOL_GROUPS=all` pra mais.

    Grupos disponíveis: core, web, macos, documents, all.
    """
    requested = _resolve_groups(groups)
    if "all" in requested:
        requested = list(TOOL_GROUPS.keys())

    seen: set[type[Tool]] = set()
    tools: list[Tool] = []
    for group in requested:
        if group not in TOOL_GROUPS:
            continue
        for cls in TOOL_GROUPS[group]:
            if cls in seen:
                continue
            seen.add(cls)
            tools.append(cls())

    return ToolRegistry(tools)


__all__ = [
    "Tool",
    "ToolError",
    "ToolRegistry",
    "TOOL_GROUPS",
    "default_registry",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "SearchCodeTool",
    "SearchKnowledgeTool",
    "WebFetchTool",
    "WebSearchTool",
    "OpenUrlTool",
    "BrowserTool",
    "AppleScriptTool",
    "ClipboardReadTool",
    "ClipboardWriteTool",
    "NotifyTool",
    "ScreenshotTool",
    "OpenAppTool",
    "DocxReadTool",
    "DocxWriteTool",
    "PdfReadTool",
    "PdfFromMarkdownTool",
    "XlsxReadTool",
    "XlsxWriteTool",
    "TextWriterTool",
]
