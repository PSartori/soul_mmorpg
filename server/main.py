"""Servidor HTTP + WebSocket. Roda com:  ./run.sh   (ou: uv run python -m server.main)

- GET /         -> cliente (client/index.html)
- GET /static/  -> arquivos do cliente
- GET /ws       -> WebSocket do jogo (mensagens JSON)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from aiohttp import WSMsgType, web

from . import config as C
from .db import Database
from .gamedata import load_game_data, watched_files_state
from .world import World

log = logging.getLogger("server")

DB = web.AppKey("db", Database)
WORLD = web.AppKey("world", World)
SESSIONS = web.AppKey("sessions", dict)  # id do jogador -> WebSocketResponse
LOOP_TASK = web.AppKey("loop_task", asyncio.Task)
BACKUP_TASK = web.AppKey("backup_task", asyncio.Task)
WATCH_TASK = web.AppKey("watch_task", asyncio.Task)


def dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(C.CLIENT_DIR / "index.html")


async def revalidate_client_files(request: web.Request, response: web.StreamResponse) -> None:
    """O navegador sempre confere se o cliente mudou (responde 304 se não mudou).

    Sem isso, depois de uma atualização o navegador pode misturar arquivos JS
    novos com antigos guardados em cache.
    """
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"


async def races_handler(request: web.Request) -> web.Response:
    """Raças que aparecem na criação de personagem (antes do login)."""
    return web.json_response(request.app[WORLD].content.selectable_races())


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    world = request.app[WORLD]
    sessions = request.app[SESSIONS]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await ws.prepare(request)

    player = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue

            if player is None:
                # Antes do login, a única mensagem aceita é "login"
                if data.get("t") != "login":
                    continue
                race = data.get("race") if isinstance(data.get("race"), str) else None
                player, error = world.login(str(data.get("name", "")), str(data.get("password", "")), race)
                if error:
                    await ws.send_str(dumps({"t": "error", "msg": error}))
                    continue
                sessions[player.id] = ws
                await ws.send_str(dumps(world.welcome_payload(player)))
                log.info("login: %s (id %s)", player.name, player.id)
            else:
                world.handle_message(player, data)
    finally:
        if player is not None:
            sessions.pop(player.id, None)
            world.logout(player)
            log.info("logout: %s", player.name)
    return ws


async def send_outbox(world: World, sessions: dict) -> None:
    for target, payload in world.drain_outbox():
        text = dumps(payload)
        if target is None:
            targets = list(sessions.values())
        else:
            ws = sessions.get(target)
            targets = [ws] if ws is not None else []
        for ws in targets:
            if ws.closed:
                continue
            with contextlib.suppress(ConnectionResetError, RuntimeError):
                await ws.send_str(text)
                if payload.get("t") == "kick":  # expulso/banido: avisa e desconecta (o handler desloga)
                    await ws.close()


async def game_loop(app: web.Application) -> None:
    world, sessions = app[WORLD], app[SESSIONS]
    loop = asyncio.get_running_loop()
    dt = 1.0 / C.TICK_RATE
    next_tick = loop.time()
    while True:
        try:
            world.step(dt)
            await send_outbox(world, sessions)
        except Exception:
            log.exception("Erro no tick")
        next_tick += dt
        delay = next_tick - loop.time()
        if delay < -1.0:  # atrasou mais de 1 s (ex.: máquina suspensa): não tenta "correr atrás"
            next_tick = loop.time()
            delay = 0.0
        await asyncio.sleep(max(0.0, delay))


async def backup_loop(app: web.Application) -> None:
    """Um backup ao ligar e outro a cada BACKUP_INTERVAL_HOURS (salvando os online antes)."""
    while True:
        try:
            app[WORLD].save_all()
            path = app[DB].backup(C.BACKUP_DIR, C.BACKUP_KEEP)
            log.info("backup: %s", path.name)
        except Exception:
            log.exception("Erro no backup")
        await asyncio.sleep(C.BACKUP_INTERVAL_HOURS * 3600)


async def watch_loop(app: web.Application) -> None:
    """Modo --watch: recarrega sozinho quando algum arquivo de conteúdo muda."""
    world = app[WORLD]
    state = watched_files_state()
    while True:
        await asyncio.sleep(1.5)
        current = watched_files_state()
        if current == state:
            continue
        state = current
        ok, text = world.reload_from_disk()
        if ok:
            log.info("conteúdo recarregado: %s", text)
            world._system("O conteúdo do jogo foi atualizado.")
        else:
            log.error("conteúdo com erro (nada mudou): %s", text)
        for p in world.players.values():  # a equipe online fica sabendo, inclusive dos erros
            if p.staff_role:
                world._private(p, ("Conteúdo recarregado: " if ok else "Erro no conteúdo (nada mudou): ") + text)


def create_app(db_path: str | Path = C.DB_PATH, maps_dir: str | Path = C.MAPS_DIR,
               watch: bool = False) -> web.Application:
    content, maps = load_game_data(C.DATA_DIR, maps_dir)  # erro no YAML ou mapa derruba aqui, com a mensagem
    app = web.Application()
    app[DB] = Database(db_path)
    app[WORLD] = World(maps, app[DB], content, start_map=C.START_MAP)
    app[WORLD].reload_source = lambda: load_game_data(C.DATA_DIR, maps_dir)
    app[SESSIONS] = {}

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/races", races_handler)
    app.router.add_static("/static/", C.CLIENT_DIR)
    app.on_response_prepare.append(revalidate_client_files)

    async def on_startup(app: web.Application) -> None:
        app[LOOP_TASK] = asyncio.create_task(game_loop(app))
        if str(db_path) != ":memory:":
            app[BACKUP_TASK] = asyncio.create_task(backup_loop(app))
        if watch:
            app[WATCH_TASK] = asyncio.create_task(watch_loop(app))
            log.info("modo --watch: recarrego o conteúdo sempre que um arquivo de data/ mudar")

    async def on_shutdown(app: web.Application) -> None:
        # Fechar as conexões faz cada ws_handler deslogar (e salvar) seu jogador
        for ws in list(app[SESSIONS].values()):
            await ws.close()

    async def on_cleanup(app: web.Application) -> None:
        for key in (LOOP_TASK, BACKUP_TASK, WATCH_TASK):
            task = app.get(key)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        app[WORLD].save_all()
        app[DB].close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Servidor do jogo.")
    parser.add_argument("--watch", action="store_true",
                        help="recarrega o conteúdo sozinho quando um arquivo de data/ muda (para desenvolvimento)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    web.run_app(create_app(watch=args.watch), host=host, port=port)


if __name__ == "__main__":
    main()
