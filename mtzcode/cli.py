"""CLI do mtzcode — REPL com agent loop e tool calling."""
from __future__ import annotations

import sys

import typer
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from mtzcode import __version__
from mtzcode.agent import Agent, AgentEvent
from mtzcode.client import ChatClient, ChatClientError
from mtzcode.config import Config
from mtzcode.profiles import PROFILES, Profile, get_profile, list_profiles
from mtzcode.tools import default_registry

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


def _make_event_handler() -> tuple[callable, callable]:
    def on_event(ev: AgentEvent) -> None:
        if ev.kind == "tool_call":
            args_preview = _preview_args(ev.data["args"])
            console.print(
                f"[dim cyan]→ tool[/] [bold cyan]{ev.data['name']}[/] [dim]{args_preview}[/]"
            )
        elif ev.kind == "tool_result":
            result = ev.data["result"]
            preview = result if len(result) < 800 else result[:800] + "\n... (truncado)"
            console.print(
                Panel(
                    preview,
                    title=f"resultado: {ev.data['name']}",
                    border_style="dim",
                    title_align="left",
                )
            )
        elif ev.kind == "tool_error":
            console.print(f"[red]✖ {ev.data['name']}:[/] {ev.data['error']}")
        elif ev.kind == "max_iterations":
            console.print(f"[yellow]⚠ atingiu limite de {ev.data['limit']} iterações[/]")

    def finalize(text: str) -> None:
        if text:
            console.print(Panel(Markdown(text), border_style="green", title="mtzcode"))

    return on_event, finalize


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
    """Mostra menu e devolve o profile escolhido (ou None se cancelado)."""
    _show_profiles_menu(current)
    try:
        choice = console.input(
            "[bold blue]escolha um perfil (número, ou enter pra cancelar) ›[/] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    if not choice.isdigit():
        console.print("[red]entrada inválida[/]")
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
            f"[yellow]⚠  Atenção:[/] {new_profile.label} envia seus dados pra um servidor externo. "
            "Isso quebra a garantia de privacidade local."
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

    agent = Agent(client, registry, cfg.system_prompt())
    on_event, finalize = _make_event_handler()

    try:
        while True:
            try:
                user_input = console.input("[bold blue]você ›[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]até mais.[/]")
                return

            if not user_input:
                continue
            if user_input in {"/sair", "/exit", "/quit"}:
                console.print("[dim]até mais.[/]")
                return
            if user_input == "/limpar":
                agent.reset()
                console.print("[dim]histórico limpo.[/]")
                continue
            if user_input == "/ajuda":
                console.print(
                    "[dim]/sair: encerra · /limpar: zera histórico · "
                    "/modelo: trocar de modelo · /ajuda: esta mensagem[/]\n"
                    f"[dim]tools disponíveis: {', '.join(registry.names())}[/]"
                )
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

            try:
                with console.status("[dim]pensando...[/]", spinner="dots"):
                    final_text = agent.run(user_input, on_event=on_event)
            except ChatClientError as exc:
                console.print(f"[red]erro:[/] {exc}")
                continue
            except KeyboardInterrupt:
                console.print("\n[yellow]interrompido.[/]")
                continue

            finalize(final_text)
    finally:
        client.close()


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
