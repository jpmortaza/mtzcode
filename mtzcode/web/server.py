"""Servidor FastAPI do mtzcode.

Expõe uma UI web local e endpoints JSON/SSE que envolvem o mesmo Agent/Tools
usados pelo CLI. Pensado pra rodar só em localhost.

Endpoints:
  GET  /             — HTML da UI
  GET  /api/state    — estado atual (profile, cwd, versão)
  GET  /api/profiles — lista de perfis disponíveis
  POST /api/profile  — troca o profile ativo { name: "qwen-7b" }
  POST /api/cwd      — troca o diretório de trabalho { path: "/abs/path" }
  POST /api/tree     — lista arquivos do cwd atual
  POST /api/chat     — SSE: envia { message } e recebe stream de eventos
  POST /api/reset    — limpa o histórico do agent
"""
from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from mtzcode import __version__
from mtzcode.agent import Agent, AgentEvent
from mtzcode.client import ChatClient, ChatClientError
from mtzcode.config import Config
from mtzcode.profiles import PROFILES, Profile, get_profile, list_profiles
from mtzcode.tools import default_registry


# ---------------------------------------------------------------------------
# Session — estado do servidor (processo single-user, sem auth, só localhost)
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cwd: Path = Path.cwd()
        self.client: ChatClient = ChatClient(cfg.profile, cfg.request_timeout_s)
        self.registry = default_registry()
        # Web UI auto-approva tools destrutivas (usuário confirma pela UI depois)
        self.agent = Agent(
            self.client,
            self.registry,
            cfg.system_prompt(),
            confirm_cb=None,  # sem confirmação — UI pode exigir depois se quiser
        )
        # Lock garante que requisições concorrentes de /api/chat não interleave
        # o histórico do mesmo agent.
        self.chat_lock = threading.Lock()

    def switch_profile(self, profile: Profile) -> None:
        new_client = ChatClient(profile, self.cfg.request_timeout_s)
        self.client.close()
        self.client = new_client
        self.agent.client = new_client
        self.cfg = self.cfg.with_profile(profile)

    def set_cwd(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValueError(f"diretório inválido: {path}")
        self.cwd = path
        os.chdir(path)  # tools usam cwd do processo

    def reset(self) -> None:
        self.agent.reset()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class ProfileRequest(BaseModel):
    name: str


class CwdRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="mtzcode", version=__version__)
    cfg = Config.load()
    session = Session(cfg)

    index_html_path = Path(__file__).parent / "static" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        try:
            return index_html_path.read_text(encoding="utf-8")
        except OSError:
            return "<h1>mtzcode</h1><p>index.html não encontrado.</p>"

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        p = session.cfg.profile
        return {
            "version": __version__,
            "cwd": str(session.cwd),
            "profile": {
                "name": p.name,
                "label": p.label,
                "is_local": p.is_local,
                "model": p.model,
            },
        }

    @app.get("/api/profiles")
    def profiles_list() -> dict[str, Any]:
        return {
            "profiles": [
                {
                    "name": p.name,
                    "label": p.label,
                    "is_local": p.is_local,
                    "model": p.model,
                    "description": p.description,
                }
                for p in list_profiles()
            ]
        }

    @app.post("/api/profile")
    def switch_profile_endpoint(req: ProfileRequest) -> dict[str, Any]:
        try:
            profile = get_profile(req.name)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            session.switch_profile(profile)
        except ChatClientError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, "profile": profile.name}

    @app.post("/api/cwd")
    def set_cwd_endpoint(req: CwdRequest) -> dict[str, Any]:
        try:
            session.set_cwd(Path(req.path))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "cwd": str(session.cwd)}

    @app.post("/api/reset")
    def reset_endpoint() -> dict[str, Any]:
        session.reset()
        return {"ok": True}

    @app.post("/api/tree")
    def tree_endpoint() -> dict[str, Any]:
        """Lista arquivos do cwd (até ~500 entradas, respeitando excludes comuns)."""
        excludes = {
            ".git", ".venv", "venv", "__pycache__", "node_modules",
            "dist", "build", ".pytest_cache", ".ruff_cache", ".DS_Store",
        }
        entries: list[dict[str, Any]] = []
        count = 0
        for p in sorted(session.cwd.rglob("*")):
            if count >= 500:
                break
            if any(part in excludes for part in p.parts):
                continue
            rel = p.relative_to(session.cwd)
            entries.append(
                {
                    "path": str(rel),
                    "is_dir": p.is_dir(),
                    "depth": len(rel.parts),
                }
            )
            count += 1
        return {"root": str(session.cwd), "entries": entries, "truncated": count >= 500}

    @app.post("/api/chat")
    def chat_endpoint(req: ChatRequest) -> StreamingResponse:
        """Envia a mensagem pro agent e devolve eventos SSE token-a-token.

        Agent roda em thread separada; o gerador consome uma queue de eventos
        e dá yield imediato, permitindo streaming real.

        Kinds: text_delta | tool_call | tool_result | tool_error |
               tool_denied | assistant_text_end | done | error
        """
        # Sentinela pra sinalizar fim do stream
        _DONE = object()
        q: queue.Queue = queue.Queue()

        def serialize(kind: str, data: dict) -> str:
            return json.dumps({"kind": kind, "data": data})

        def on_event(ev: AgentEvent) -> None:
            q.put(("event", serialize(ev.kind, ev.data)))

        def worker() -> None:
            """Roda o agent na thread. Adquire lock pra não interleave com
            outras requisições simultâneas que compartilham o mesmo Session.
            """
            with session.chat_lock:
                try:
                    final = session.agent.run_streaming(
                        req.message, on_event=on_event
                    )
                    q.put(("event", serialize("done", {"text": final})))
                except ChatClientError as exc:
                    q.put(("event", serialize("error", {"message": str(exc)})))
                except Exception as exc:  # noqa: BLE001
                    q.put(("event", serialize("error", {"message": str(exc)})))
                finally:
                    q.put(("done", _DONE))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        def event_stream():
            while True:
                try:
                    kind, payload = q.get(timeout=600)
                except queue.Empty:
                    yield "data: {\"kind\":\"error\",\"data\":{\"message\":\"timeout\"}}\n\n"
                    return
                if kind == "done":
                    return
                yield f"data: {payload}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Entry point do `mtzcode serve`."""
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
