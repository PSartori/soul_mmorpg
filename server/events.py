"""Eventos em ondas: invasões (data/events.yaml).

Um evento acontece num mapa: os inimigos de cada onda aparecem numa área (spawn) e,
se o evento tiver `march`, marcham até um ponto (ex.: a porta da cidade), atacando
quem encontram no caminho. Quando todos os invasores de uma onda caem, a próxima
chega depois de C.EVENT_WAVE_PAUSE segundos. Vencer a última onda dentro do tempo
(`time`) é vitória; se o tempo acabar, os invasores somem e é derrota.

Quem participa: quem causou pelo menos `min_damage` de dano aos invasores. Na vitória,
cada participante online recebe as recompensas (`rewards`, as mesmas ações das regras).
Ao terminar (vitória ou derrota), o evento `event_end` das regras dispara para cada
participante (com result e damage), para recompensas e consequências extras.

Começa de dois jeitos: pela equipe (janela de administração, permissão `events`) ou
sozinho, a cada `every` minutos, se houver pelo menos `min_players` online. Só um
evento por mapa ao mesmo tempo.

Invasores não renascem, não desistem por estar longe de casa (a "casa" deles é o
ponto de marcha) e enxergam jogadores mais longe (C.EVENT_AGGRO_MULT).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import config as C

if TYPE_CHECKING:
    from .content import EventDef
    from .entities import Enemy, Player
    from .world import World

log = logging.getLogger("events")


@dataclass
class RunningEvent:
    defn: EventDef
    ends_at: float  # horário do mundo em que o tempo acaba
    wave: int = 0  # índice da onda atual (0 = primeira)
    next_wave_at: float | None = None  # pausa entre ondas: quando a próxima chega
    damage: dict[int, float] = field(default_factory=dict)  # id do jogador -> dano causado aos invasores


class EventSystem:
    def __init__(self, world: World):
        self.world = world
        self.running: dict[str, RunningEvent] = {}  # id do evento -> em andamento
        self.next_auto: dict[str, float] = {}  # id do evento -> quando tenta começar sozinho

    # ------------------------------------------------------------ começar e terminar

    def start(self, event_id: str, by: str | None = None) -> tuple[bool, str]:
        w = self.world
        defn = w.content.events.get(event_id)
        if defn is None:
            return False, f"Evento {event_id!r} não existe."
        if defn.map not in w.maps:
            return False, f"O mapa {defn.map!r} do evento não existe."
        if event_id in self.running:
            return False, f"{defn.name} já está acontecendo."
        if any(r.defn.map == defn.map for r in self.running.values()):
            return False, "Já há um evento acontecendo nesse mapa."
        run = RunningEvent(defn=defn, ends_at=w.time + defn.time)
        self.running[event_id] = run
        self._announce(defn.text)
        self._spawn_wave(run)
        log.info("evento %s começou%s", event_id, f" ({by})" if by else " (agenda)")
        return True, f"{defn.name} começou."

    def stop(self, event_id: str) -> tuple[bool, str]:
        """A equipe encerra: invasores somem, sem recompensa."""
        run = self.running.get(event_id)
        if run is None:
            return False, "Esse evento não está acontecendo."
        self._finish(run, None)
        self._announce(f"O evento {run.defn.name} foi encerrado pela equipe.")
        return True, f"Evento encerrado: {run.defn.name}."

    def reset(self) -> None:
        """Recarga de conteúdo: os inimigos renascem do zero, então os eventos em andamento acabam."""
        if self.running:
            self.world._system("Os eventos em andamento foram encerrados pela recarga do conteúdo.")
        self.running.clear()
        self.next_auto = {k: v for k, v in self.next_auto.items() if k in self.world.content.events}

    def _finish(self, run: RunningEvent, result: str | None) -> None:
        """Termina o evento. result: "win", "lose" ou None (encerrado pela equipe, sem regras)."""
        w = self.world
        self.running.pop(run.defn.id, None)
        for enemy in self._invaders(run):
            del w.enemies[enemy.id]
            w._despawn_summons(enemy)
        if result is None:
            return
        self._announce(run.defn.win_text if result == "win" else run.defn.lose_text)
        for p in self.participants(run):
            ctx = {"event": run.defn.id, "result": result, "damage": round(run.damage[p.id])}
            if result == "win":
                full = w._player_context(p) | ctx
                for name, args in run.defn.rule.actions:
                    w._do_action(p, name, args, full, 0)
            w._fire("event_end", p, ctx)
        log.info("evento %s terminou: %s (%d participantes)", run.defn.id, result, len(self.participants(run)))

    def participants(self, run: RunningEvent) -> list[Player]:
        """Quem está online e causou pelo menos min_damage aos invasores."""
        return [p for pid, dmg in run.damage.items()
                if dmg >= run.defn.min_damage and (p := self.world.players.get(pid)) is not None]

    # ------------------------------------------------------------ ondas

    def _spawn_wave(self, run: RunningEvent) -> None:
        w = self.world
        defn = run.defn
        tilemap = w.maps[defn.map]
        wave = defn.waves[run.wave]
        x0, y0, x1, y1 = defn.spawn
        free = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if _free(tilemap.rows[y][x])]
        if defn.march:
            home = tilemap.tile_center(*defn.march)
        for enemy_id, count in wave.enemies:
            enemy_def = w.content.enemies[enemy_id]
            for _ in range(count):
                x, y = tilemap.tile_center(*w.rng.choice(free))
                enemy = w._new_enemy(enemy_def, defn.map, x, y)
                enemy.event_id = defn.id
                if defn.march:
                    enemy.home_x, enemy.home_y = home
        total = len(defn.waves)
        label = f"{defn.name}: onda {run.wave + 1}/{total}" if total > 1 else defn.name
        text = f"{label} — {sum(n for _, n in wave.enemies)} inimigos." + (f" {wave.text}" if wave.text else "")
        w._system(text)

    def _invaders(self, run: RunningEvent) -> list[Enemy]:
        return [e for e in self.world.enemies.values() if e.event_id == run.defn.id]

    def alive(self, run: RunningEvent) -> int:
        return sum(1 for e in self._invaders(run) if e.alive)

    # ------------------------------------------------------------ jogo

    def on_damage(self, source: Player, target: Enemy, amount: float) -> None:
        run = self.running.get(target.event_id or "")
        if run is not None:
            run.damage[source.id] = run.damage.get(source.id, 0.0) + amount

    def update(self) -> None:
        w = self.world
        for run in list(self.running.values()):
            if w.time >= run.ends_at:
                self._finish(run, "lose")
            elif run.next_wave_at is not None:
                if w.time >= run.next_wave_at:
                    run.next_wave_at = None
                    self._spawn_wave(run)
            elif self.alive(run) == 0:
                for enemy in self._invaders(run):  # corpos da onda derrotada
                    del w.enemies[enemy.id]
                if run.wave + 1 >= len(run.defn.waves):
                    self._finish(run, "win")
                else:
                    run.wave += 1
                    run.next_wave_at = w.time + C.EVENT_WAVE_PAUSE
                    w._system(f"{run.defn.name}: onda {run.wave}/{len(run.defn.waves)} derrotada! "
                              f"A próxima chega em {round(C.EVENT_WAVE_PAUSE)} s.")
        self._schedule()

    def _schedule(self) -> None:
        """Eventos com `every`: tentam começar sozinhos a cada tantos minutos."""
        w = self.world
        for defn in w.content.events.values():
            if defn.every <= 0 or defn.map not in w.maps:
                continue
            due = self.next_auto.setdefault(defn.id, w.time + defn.every * 60)
            if w.time < due:
                continue
            self.next_auto[defn.id] = w.time + defn.every * 60
            if len(w.players) >= defn.min_players and defn.id not in self.running:
                self.start(defn.id)

    def _announce(self, text: str) -> None:
        self.world._outbox.append((None, {"t": "announce", "text": text, "by": "Evento"}))

    # ------------------------------------------------------------ para o cliente e a equipe

    def client_info(self, p: Player) -> list[dict]:
        """Eventos em andamento, como o painel do jogador mostra (o próprio dano incluso)."""
        w = self.world
        info = []
        for run in self.running.values():
            tilemap = w.maps.get(run.defn.map)
            info.append({
                "id": run.defn.id,
                "name": run.defn.name,
                "map": tilemap.title or tilemap.name if tilemap else run.defn.map,
                "here": p.map_id == run.defn.map,
                "wave": run.wave + 1,
                "waves": len(run.defn.waves),
                "left": self.alive(run),
                "time": max(0, math.ceil(run.ends_at - w.time)),
                "pause": math.ceil(run.next_wave_at - w.time) if run.next_wave_at is not None else 0,
                "dmg": round(run.damage.get(p.id, 0.0)),
                "min": round(run.defn.min_damage),
            })
        return info

    def snapshot_for(self, p: Player) -> tuple[bool, list[dict]]:
        """(mudou, eventos). Só vai ao cliente quando muda (o relógio muda a cada segundo)."""
        info = self.client_info(p)
        key = json.dumps(info)
        if key == p.events_sent:
            return False, info
        p.events_sent = key
        return True, info

    def admin_info(self) -> list[dict]:
        w = self.world
        return [{"id": d.id, "name": d.name, "map": (w.maps[d.map].title or d.map) if d.map in w.maps else d.map,
                 "waves": len(d.waves), "running": d.id in self.running,
                 "auto": d.every} for d in w.content.events.values()]


def _free(ch: str) -> bool:
    from .content import _enemy_can_stand
    return _enemy_can_stand(ch)
