"""Registry padrão de tools do mtzcode."""
from __future__ import annotations

from mtzcode.tools.bash import BashTool
from mtzcode.tools.base import Tool, ToolError, ToolRegistry
from mtzcode.tools.edit import EditTool
from mtzcode.tools.glob import GlobTool
from mtzcode.tools.grep import GrepTool
from mtzcode.tools.read import ReadTool
from mtzcode.tools.write import WriteTool


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ReadTool(),
            WriteTool(),
            EditTool(),
            BashTool(),
            GlobTool(),
            GrepTool(),
        ]
    )


__all__ = [
    "Tool",
    "ToolError",
    "ToolRegistry",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "BashTool",
    "GlobTool",
    "GrepTool",
    "default_registry",
]
