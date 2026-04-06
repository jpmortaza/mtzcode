"""CLI do mtzcode — REPL com agent loop, streaming, diffs e confirmação destrutiva."""
from __future__ import annotations

import sys
from typing import Any

import typer
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from pathlib import Path

from mtzcode import __version__
from mtzcode.agent import Agent, AgentEvent
from mtzcode.client import ChatClient, ChatClientError
from mtzcode.commands import SlashCommand, commands_dir, load_commands, parse_slash
from mtzcode.config import Config
from mtzcode.profiles import PROFILES, Profile, get_profile, list_profiles
from mtzcode.tools import default_registry

_PLAN_MODE_PROMPT_PATH = Path(__file__).parent / "prompts" / "plan_mode.md"

app = typer.Typer(
    add_completion=False,
    help="mtzcode — assistente de código local (Ollama), 100% offline.",
)
console = Console()


_BANNER_ASCII = r"""
███╗   ███╗████████╗███████╗ ██████╗ ██████╗ ██████╗ ███████╗
████╗ ████║╚══██╔══╝╚══███╔╝██╔════╝██╔═══██╗██╔══██╗██╔════╝
██╔████╔██║   ██║     ███╔╝ ██║     ██║   ██║██║  ██║█████╗
██║╚██╔╝██║   ██║    ███╔╝  ██║     ██║   ██║██║  ██║██╔══╝
██║ ╚═╝ ██║   ██║   ███████╗╚██████╗╚██████╔╝██████╔╝███████╗
╚═╝     ╚═╝   ╚═╝   ╚══════╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
"""


def _banner(cfg: Config, n_tools: int) -> None:
    logo = Text(_BANNER_ASCII, style="bold bright_cyan")
    console.print(Align.center(logo))
    signature = Text("by Jean Mortaza", style="italic bright_magenta")
    console.print(Align.center(signature))
    console.print()

    profile = cfg.profile
    locality = "[green]local[/]" if profile.is_local else "[red]cloud[/]"
    info = (
        f"[dim]versão[/] [bold]{__version__}[/]   "
        f"[dim]perfil[/] [yellow]{profile.name}[/] ({locality})   "
        f"[dim]tools[/] [green]{n_tools}[/]\n"
        f"[dim]modelo[/] {profile.model}\n"
        f"[dim]host[/]   {profile.base_url}\n\n"
        f"[dim]Comandos: /sair  /limpar  /modelo  /ajuda[/]"
    )
    console.print(Align.center(Panel.fit(info, border_style="cyan")))
    console.print()


# ---------------------------------------------------------------------------
# Session state — flags compartilhadas entre REPL e callbacks
# ---------------------------------------------------------------------------
class SessionState:
    def __init__(self) -> None:
        # Tools marcadas como "sempre permitir" nesta sessão
        self.always_allow: set[str] = set()
        # Plan mode: se True, bloqueia tools destrutivas independente do always_allow
        self.plan_mode: bool = False

    def should_confirm(self, tool_name: str) -> bool:
        if self.plan_mode:
            return True  # sempre confirma em plan mode (será recusado automaticamente)
        return tool_name not in self.always_allow


def _make_confirm_cb(state: SessionState):
    """Retorna um confirm_cb que pergunta [s/N/sempre] e respeita 'sempre'.
    Em plan_mode, recusa automaticamente tools destrutivas."""

    def confirm(tool_name: str, args: dict) -> bool:
        if state.plan_mode:
            console.print(
                f"[yellow]⊘ plano ativo:[/] [bold]{tool_name}[/] bloqueada. "
                "Digite /executar pra sair do modo plano."
            )
            return False
        if not state.should_confirm(tool_name):
            return True
        args_preview = _preview_args(args)
        console.print(
            f"\n[yellow]⚠  confirmação:[/] executar [bold cyan]{tool_name}[/] "
            f"[dim]{args_preview}[/]?"
        )
        try:
            resp = (
                console.input(
                    "[bold]› [/][dim]([/][green]s[/]im / [red]N[/]ão / "
                    "[cyan]a[/]lways[dim])[/] "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            console.print("[dim](recusado)[/]")
            return False
        if resp in {"s", "sim", "y", "yes"}:
            return True
        if resp in {"a", "always", "sempre"}:
            state.always_allow.add(tool_name)
            console.print(
                f"[dim](ok — {tool_name} liberada pro resto da sessão)[/]"
            )
            return True
        return False

    return confirm


# ---------------------------------------------------------------------------
# Event rendering
# ---------------------------------------------------------------------------
class EventRenderer:
    """Renderiza eventos do agent no terminal. Mantém estado pra streaming."""

    def __init__(self) -> None:
        self._streaming_text = False

    def handle(self, ev: AgentEvent) -> None:
        if ev.kind == "text_delta":
            if not self._streaming_text:
                console.print("[dim]mtzcode ›[/] ", end="")
                self._streaming_text = True
            console.print(
                ev.data["delta"], end="", markup=False, highlight=False
            )
        elif ev.kind == "assistant_text_end":
            if self._streaming_text:
                console.print()  # newline pra fechar a linha de streaming
                self._streaming_text = False
        elif ev.kind == "assistant_text":
            # Modo não-streaming — renderiza tudo de uma vez em markdown
            console.print(
                Panel(
                    Markdown(ev.data["text"]),
                    border_style="green",
                    title="mtzcode",
                )
            )
        elif ev.kind == "tool_call":
            self._close_streaming()
            args_preview = _preview_args(ev.data["args"])
            console.print(
                f"[dim cyan]→ tool[/] [bold cyan]{ev.data['name']}[/] "
                f"[dim]{args_preview}[/]"
            )
        elif ev.kind == "tool_result":
            self._render_tool_result(ev.data["name"], ev.data["result"])
        elif ev.kind == "tool_error":
            self._close_streaming()
            console.print(f"[red]✖ {ev.data['name']}:[/] {ev.data['error']}")
        elif ev.kind == "tool_denied":
            self._close_streaming()
            console.print(f"[yellow]⊘ {ev.data['name']} recusada pelo usuário[/]")
        elif ev.kind == "max_iterations":
            self._close_streaming()
            console.print(
                f"[yellow]⚠ atingiu limite de {ev.data['limit']} iterações[/]"
            )

    def finalize(self, text: str) -> None:
        if self._streaming_text:
            console.print()  # fecha a linha
            self._streaming_text = False
        # Não re-renderiza o texto final — já foi streamado

    def _close_streaming(self) -> None:
        if self._streaming_text:
            console.print()
            self._streaming_text = False

    def _render_tool_result(self, name: str, result: str) -> None:
        # Se o resultado contém um diff unified, renderiza com syntax highlight
        if "--- a/" in result and "+++ b/" in result:
            header, _, diff_body = result.partition("--- a/")
            diff_body = "--- a/" + diff_body
            if header.strip():
                console.print(f"[dim]{header.strip()}[/]")
            console.print(
                Panel(
                    Syntax(
                        diff_body,
                        "diff",
                        theme="ansi_dark",
                        background_color="default",
                        word_wrap=True,
                    ),
                    title=f"resultado: {name}",
                    border_style="dim",
                    title_align="left",
                )
            )
            return

        preview = (
            result if len(result) < 800 else result[:800] + "\n... (truncado)"
        )
        console.print(
            Panel(
                preview,
                title=f"resultado: {name}",
                border_style="dim",
                title_align="left",
            )
        )


def _preview_args(args: dict) -> str:
    if not args:
        return "()"
    parts = []
    for k, v in args.items():
        sv = repr(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f"{k}={sv}")
    return "(" + ", ".join(parts) + ")"


# ---------------------------------------------------------------------------
# Comandos do REPL
# ---------------------------------------------------------------------------
def _show_profiles_menu(current: Profile) -> None:
    console.print()
    console.print("[bold]Perfis disponíveis:[/]")
    for i, p in enumerate(list_profiles(), start=1):
        marker = "[green]●[/]" if p.name == current.name else " "
        locality = "[green]local[/]" if p.is_local else "[red]cloud[/]"
        console.print(
            f"  {marker} [bold cyan]{i}[/]. [bold]{p.label}[/] ({locality})\n"
            f"      [dim]{p.description}[/]"
        )
    console.print()


def _switch_profile(current: Profile) -> Profile | None:
    _show_profiles_menu(current)
    try:
        choice = console.input(
            "[bold blue]escolha um perfil (número, enter pra cancelar) ›[/] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice or not choice.isdigit():
        return None
    idx = int(choice) - 1
    profiles = list_profiles()
    if idx < 0 or idx >= len(profiles):
        console.print("[red]número fora do intervalo[/]")
        return None
    new_profile = profiles[idx]
    if new_profile.name == current.name:
        console.print("[dim]já está nesse perfil.[/]")
        return None
    if not new_profile.is_local:
        console.print(
            f"[yellow]⚠  Atenção:[/] {new_profile.label} envia seus dados "
            "pra um servidor externo. Quebra a garantia de privacidade local."
        )
    return new_profile


def _repl(cfg: Config) -> None:
    registry = default_registry()
    _banner(cfg, len(registry))

    try:
        client = ChatClient(cfg.profile, cfg.request_timeout_s)
    except ChatClientError as exc:
        console.print(f"[red]erro ao iniciar cliente:[/] {exc}")
        return

    state = SessionState()
    confirm_cb = _make_confirm_cb(state)
    default_prompt = cfg.system_prompt()
    agent = Agent(client, registry, default_prompt, confirm_cb=confirm_cb)
    renderer = EventRenderer()

    # Carrega slash commands customizados do usuário
    custom_commands = load_commands()
    if custom_commands:
        console.print(
            f"[dim]slash commands customizados carregados: "
            f"{', '.join('/' + n for n in custom_commands)}[/]"
        )

    try:
        while True:
            try:
                user_input = console.input("[bold blue]você ›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]até mais.[/]")
                return

            if not user_input:
                continue

            # --- comandos built-in ---
            if user_input in {"/sair", "/exit", "/quit"}:
                console.print("[dim]até mais.[/]")
                return
            if user_input == "/limpar":
                agent.reset()
                agent.set_system_prompt(default_prompt)
                state.always_allow.clear()
                state.plan_mode = False
                console.print("[dim]histórico limpo.[/]")
                continue
            if user_input == "/ajuda":
                _print_help(registry, custom_commands)
                continue
            if user_input in {"/modelo", "/model"}:
                new_profile = _switch_profile(client.profile)
                if new_profile is None:
                    continue
                try:
                    new_client = ChatClient(new_profile, cfg.request_timeout_s)
                except ChatClientError as exc:
                    console.print(f"[red]falha ao trocar:[/] {exc}")
                    continue
                client.close()
                client = new_client
                agent.client = new_client
                console.print(
                    f"[green]✓[/] agora usando [bold]{new_profile.label}[/] "
                    f"[dim](histórico preservado)[/]"
                )
                continue
            if user_input in {"/plano", "/plan"}:
                if state.plan_mode:
                    console.print("[dim]já está em modo plano.[/]")
                    continue
                try:
                    plan_prompt = _PLAN_MODE_PROMPT_PATH.read_text(encoding="utf-8")
                except OSError:
                    plan_prompt = (
                        "Você está em modo de planejamento: use apenas tools "
                        "read-only. Entregue um plano estruturado ao final."
                    )
                agent.set_system_prompt(plan_prompt)
                state.plan_mode = True
                console.print(
                    "[yellow]◐ modo plano ativado[/] — tools destrutivas "
                    "bloqueadas. Digite [cyan]/executar[/] pra sair."
                )
                continue
            if user_input in {"/executar", "/exec", "/run"}:
                if not state.plan_mode:
                    console.print("[dim]não estava em modo plano.[/]")
                    continue
                agent.set_system_prompt(default_prompt)
                state.plan_mode = False
                console.print(
                    "[green]▶ modo plano desativado[/] — tools destrutivas "
                    "liberadas novamente (ainda pedem confirmação)."
                )
                continue

            # --- slash commands customizados ---
            parsed = parse_slash(user_input)
            if parsed is not None:
                cmd_name, cmd_args = parsed
                if cmd_name in custom_commands:
                    rendered = custom_commands[cmd_name].render(cmd_args)
                    console.print(
                        f"[dim]→ expandindo /{cmd_name} ({len(rendered)} chars)[/]"
                    )
                    user_input = rendered
                else:
                    # Slash desconhecido — não repassa pro modelo, evita confusão
                    console.print(
                        f"[red]comando desconhecido:[/] /{cmd_name}. "
                        "Digite [cyan]/ajuda[/] pra ver os disponíveis."
                    )
                    continue

            # --- turno do agent ---
            try:
                final_text = agent.run_streaming(
                    user_input, on_event=renderer.handle
                )
                renderer.finalize(final_text)
            except ChatClientError as exc:
                console.print(f"\n[red]erro:[/] {exc}")
                continue
            except KeyboardInterrupt:
                console.print("\n[yellow]interrompido.[/]")
                continue
    finally:
        client.close()


def _print_help(registry, custom_commands: dict[str, SlashCommand] | None = None) -> None:
    console.print(
        "[bold]Comandos do REPL:[/]\n"
        "  [cyan]/sair[/]       encerra a sessão\n"
        "  [cyan]/limpar[/]     zera o histórico da conversa\n"
        "  [cyan]/modelo[/]     troca de modelo (menu interativo)\n"
        "  [cyan]/plano[/]      ativa modo de planejamento (sem tools destrutivas)\n"
        "  [cyan]/executar[/]   sai do modo plano\n"
        "  [cyan]/ajuda[/]      mostra esta mensagem\n"
    )
    if custom_commands:
        console.print("[bold]Slash commands customizados:[/]")
        for name, cmd in custom_commands.items():
            first_line = cmd.template.strip().splitlines()[0] if cmd.template.strip() else ""
            preview = first_line[:80] + ("…" if len(first_line) > 80 else "")
            console.print(f"  [cyan]/{name}[/]  [dim]{preview}[/]")
        console.print(
            f"[dim]  (arquivos em {commands_dir()})[/]"
        )
    console.print(
        f"\n[dim]Tools disponíveis: {', '.join(registry.names())}[/]"
    )


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------
@app.command()
def chat(
    profile: str = typer.Option(
        None,
        "--profile",
        "-p",
        help=f"Perfil de modelo. Disponíveis: {', '.join(PROFILES)}",
    ),
) -> None:
    """Abre o REPL interativo do mtzcode."""
    cfg = Config.load()
    if profile:
        try:
            cfg = cfg.with_profile(get_profile(profile))
        except KeyError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    _repl(cfg)


@app.command()
def profiles() -> None:
    """Lista todos os perfis de modelo disponíveis."""
    for p in list_profiles():
        locality = "[green]local[/]" if p.is_local else "[red]cloud[/]"
        console.print(
            f"[bold cyan]{p.name}[/] — [bold]{p.label}[/] ({locality})\n"
            f"  [dim]{p.description}[/]\n"
            f"  [dim]modelo:[/] {p.model}\n"
            f"  [dim]host:[/]   {p.base_url}\n"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host do servidor."),
    port: int = typer.Option(8765, "--port", "-P", help="Porta do servidor."),
) -> None:
    """Inicia a interface web do mtzcode no navegador."""
    try:
        from mtzcode.web.server import run as run_server
    except ImportError as exc:
        console.print(
            f"[red]dependências da UI web não instaladas:[/] {exc}\n"
            "rode: [cyan]uv pip install -e .[/]"
        )
        raise typer.Exit(1) from exc
    url = f"http://{host}:{port}"
    console.print(
        Panel.fit(
            f"[bold cyan]mtzCode Web UI[/]\n\n"
            f"abra no navegador: [bold]{url}[/]\n"
            f"[dim]Ctrl+C para parar[/]",
            border_style="cyan",
        )
    )
    run_server(host=host, port=port)


@app.command()
def version() -> None:
    """Mostra a versão."""
    console.print(f"mtzcode {__version__}")


def main() -> None:
    if len(sys.argv) == 1:
        _repl(Config.load())
        return
    app()


if __name__ == "__main__":
    main()
