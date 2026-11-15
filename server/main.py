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
from .content import Content
from .db import Database
from .tilemap import load_map
from .world import World

log = logging.getLogger("server")

DB = web.AppKey("db", Database)
WORLD = web.AppKey("world", World)
SESSIONS = web.AppKey("sessions", dict)  # id do jogador -> WebSocketResponse
LOOP_TASK = web.AppKey("loop_task", asyncio.Task)


def dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(C.CLIENT_DIR / "index.html")


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
                player, error = world.login(str(data.get("name", "")), str(data.get("password", "")))
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


def create_app(db_path: str | Path = C.DB_PATH, map_path: str | Path = C.DEFAULT_MAP) -> web.Application:
    content = Content.load(C.DATA_DIR)  # erro no YAML derruba aqui, com a mensagem do campo
    app = web.Application()
    app[DB] = Database(db_path)
    app[WORLD] = World(load_map(map_path, content.enemy_by_mark), app[DB], content)
    app[SESSIONS] = {}

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", C.CLIENT_DIR)

    async def on_startup(app: web.Application) -> None:
        app[LOOP_TASK] = asyncio.create_task(game_loop(app))

    async def on_shutdown(app: web.Application) -> None:
        # Fechar as conexões faz cada ws_handler deslogar (e salvar) seu jogador
        for ws in list(app[SESSIONS].values()):
            await ws.close()

    async def on_cleanup(app: web.Application) -> None:
        app[LOOP_TASK].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app[LOOP_TASK]
        app[WORLD].save_all()
        app[DB].close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
