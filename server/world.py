"""Simulação do mundo.

O servidor é a autoridade: o cliente só manda intenções (teclas, ações, chat)
e recebe de volta o estado. Esta classe não sabe nada de rede; tudo que precisa
ser enviado vai para uma "caixa de saída" que o main.py esvazia a cada tick.
"""
from __future__ import annotations

import math
import random
import re
from itertools import chain
from typing import Iterator

from . import config as C
from .db import Database
from .entities import DIRECTIONS, MOVE_KEYS, Enemy, Entity, Player, Projectile
from .stats import STAT_LABELS
from .tilemap import SAFE, SHOT_BLOCKERS, WALK_BLOCKERS, TileMap

NAME_RE = re.compile(r"[A-Za-z0-9_]{3,16}")
ACTIONS = frozenset({"attack", "blast", "meditate", "rest"})
MAX_QUEUED_ACTIONS = 4

# (id do jogador destinatário, ou None para todos; mensagem)
Outgoing = tuple[int | None, dict]


def _player_blocks(ch: str) -> bool:
    return ch in WALK_BLOCKERS


def _enemy_blocks(ch: str) -> bool:
    return ch in WALK_BLOCKERS or ch == SAFE


def _shot_blocks(ch: str) -> bool:
    return ch in SHOT_BLOCKERS


def _face(entity: Entity, dx: float, dy: float) -> None:
    if abs(dx) > abs(dy):
        entity.facing = "right" if dx > 0 else "left"
    elif dy:
        entity.facing = "down" if dy > 0 else "up"


def _boxes_overlap(ax: float, ay: float, ah: float, bx: float, by: float, bh: float) -> bool:
    return abs(ax - bx) < ah + bh and abs(ay - by) < ah + bh


class World:
    def __init__(self, tilemap: TileMap, db: Database, rng: random.Random | None = None):
        self.map = tilemap
        self.db = db
        self.rng = rng or random.Random()
        self.time = 0.0
        self._next_id = 1
        self.players: dict[int, Player] = {}
        self.enemies: dict[int, Enemy] = {}
        self.projectiles: dict[int, Projectile] = {}
        self._fx: list[dict] = []  # efeitos visuais deste tick (dano, golpes, level up)
        self._outbox: list[Outgoing] = []
        self._save_timer = C.AUTOSAVE_SECONDS
        for x, y in tilemap.enemy_spawns:
            self._spawn_enemy(x, y)

    def _new_id(self) -> int:
        new_id = self._next_id
        self._next_id += 1
        return new_id

    # ================================================================ sessão

    def login(self, name: str, password: str) -> tuple[Player | None, str | None]:
        """Entra com um personagem existente ou cria um novo. Devolve (jogador, erro)."""
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return None, "O nome deve ter de 3 a 16 caracteres (letras, números ou _)."
        if len(password) < 4:
            return None, "A senha deve ter pelo menos 4 caracteres."
        if any(p.name.lower() == name.lower() for p in self.players.values()):
            return None, "Esse personagem já está online."

        record = self.db.load_character(name)
        if record is None:
            record = self.db.create_character(name, password)
            is_new = True
        elif not self.db.verify_password(record, password):
            return None, "Senha incorreta."
        else:
            is_new = False

        sx, sy = self.map.spawn
        player = Player(id=self._new_id(), name=record.name, x=sx, y=sy)
        player.load_dict(record.data)
        if is_new:
            player.hp = player.max_hp
            player.energy = player.max_energy
        if self.map.box_blocked(player.x, player.y, player.half, _player_blocks):
            player.x, player.y = sx, sy

        self.players[player.id] = player
        self._system(f"{player.name} entrou no jogo.")
        if is_new:
            self._private(player, "Bem-vindo! Personagem criado. Treine meditando (M) e lutando.")
        return player, None

    def logout(self, player: Player) -> None:
        if self.players.pop(player.id, None) is None:
            return
        self._save(player)
        self._system(f"{player.name} saiu do jogo.")

    def welcome_payload(self, player: Player) -> dict:
        return {
            "t": "welcome",
            "id": player.id,
            "tick_rate": C.TICK_RATE,
            "map": {"name": self.map.name, "tile_size": self.map.tile_size, "rows": self.map.rows},
            "stat_labels": STAT_LABELS,
        }

    def handle_message(self, player: Player, data: dict) -> None:
        kind = data.get("t")
        if kind == "input":
            player.input = {k: bool(data.get(k)) for k in MOVE_KEYS}
        elif kind in ACTIONS:
            if len(player.actions) < MAX_QUEUED_ACTIONS:
                player.actions.append(kind)
        elif kind == "chat":
            text = str(data.get("text", ""))[: C.CHAT_MAX_LEN].strip()
            if text:
                self._chat(player, text)

    def save_all(self) -> None:
        for player in self.players.values():
            self._save(player)

    def _save(self, player: Player) -> None:
        data = player.to_dict()
        if not player.alive:  # quem sai morto volta no ponto de nascimento
            data["x"], data["y"] = self.map.spawn
            data["hp"] = player.max_hp
        self.db.save_character(player.name, data)

    def drain_outbox(self) -> list[Outgoing]:
        out, self._outbox = self._outbox, []
        return out

    # ================================================================ mensagens

    def _system(self, text: str) -> None:
        self._outbox.append((None, {"t": "sys", "text": text}))

    def _private(self, player: Player, text: str) -> None:
        self._outbox.append((player.id, {"t": "sys", "text": text}))

    def _chat(self, player: Player, text: str) -> None:
        if text.startswith("/"):
            command = text.split()[0].lower()
            if command == "/who":
                names = ", ".join(sorted(p.name for p in self.players.values()))
                self._private(player, f"Online ({len(self.players)}): {names}")
            else:
                self._private(player, f"Comando desconhecido: {command}. Disponível: /who")
            return
        self._outbox.append((None, {"t": "chat", "from": player.name, "text": text}))

    # ================================================================ tick

    def step(self, dt: float) -> None:
        self.time += dt
        for player in list(self.players.values()):
            self._update_player(player, dt)
        for enemy in list(self.enemies.values()):
            self._update_enemy(enemy, dt)
        self._update_projectiles(dt)

        self._save_timer -= dt
        if self._save_timer <= 0:
            self._save_timer = C.AUTOSAVE_SECONDS
            self.save_all()

        self._emit_snapshots()

    def _emit_snapshots(self) -> None:
        ents = [e.snapshot() for e in chain(self.players.values(), self.enemies.values())]
        projs = [p.snapshot() for p in self.projectiles.values()]
        fx, self._fx = self._fx, []
        for player in self.players.values():
            self._outbox.append(
                (player.id, {"t": "snap", "ents": ents, "projs": projs, "fx": fx, "you": player.private_snapshot()})
            )

    # ================================================================ jogadores

    def _update_player(self, p: Player, dt: float) -> None:
        if not p.alive:
            p.dead_timer -= dt
            if p.dead_timer <= 0:
                p.x, p.y = self.map.spawn
                p.hp, p.energy = p.max_hp, p.max_energy
                p.state = "idle"
            p.actions.clear()
            return

        p.attack_cd = max(0.0, p.attack_cd - dt)
        p.blast_cd = max(0.0, p.blast_cd - dt)

        actions, p.actions = p.actions, []
        for action in actions:
            if action == "meditate":
                self._toggle_meditate(p)
            elif action == "rest":
                p.state = "idle" if p.state == "rest" else "rest"
            elif action == "attack":
                self._melee(p)
            elif action == "blast":
                self._blast(p)
            if not p.alive:
                return

        self._move_player(p, dt)
        self._regen_player(p, dt)

    def _toggle_meditate(self, p: Player) -> None:
        if p.state == "meditate":
            p.state = "idle"
        elif p.fatigue >= C.MEDITATE_FATIGUE_LIMIT:
            self._private(p, "Você está cansado demais para meditar. Descanse (R).")
        else:
            p.state = "meditate"

    def _move_player(self, p: Player, dt: float) -> None:
        dx = int(p.input["right"]) - int(p.input["left"])
        dy = int(p.input["down"]) - int(p.input["up"])
        if not dx and not dy:
            if p.state == "walk":
                p.state = "idle"
            return
        if p.state in ("meditate", "rest"):  # andar interrompe meditação/descanso
            p.state = "idle"
        _face(p, dx, dy)
        scale = p.speed * dt * (math.sqrt(0.5) if dx and dy else 1.0)
        old_x, old_y = p.x, p.y
        p.x, p.y = self.map.move_box(p.x, p.y, p.half, dx * scale, dy * scale, _player_blocks)
        moved = math.hypot(p.x - old_x, p.y - old_y)
        p.state = "walk" if moved > 0 else "idle"
        if moved > 0:
            self._train(p, "agility", moved * C.AGILITY_XP_PER_PX)

    def _regen_player(self, p: Player, dt: float) -> None:
        if p.state == "meditate":
            hp_rate, energy_rate = 0.0, C.ENERGY_REGEN_MEDITATE
            p.fatigue += C.FATIGUE_MEDITATE * dt
            self._train(p, "spirit", C.SPIRIT_XP_MEDITATE * dt)
            if p.fatigue >= C.MAX_FATIGUE:
                p.state = "idle"
                self._private(p, "Você está exausto. Descanse (R) antes de meditar de novo.")
        elif p.state == "rest":
            hp_rate, energy_rate = C.HP_REGEN_REST, C.ENERGY_REGEN_REST
            p.fatigue -= C.FATIGUE_RECOVER_REST * dt
        else:
            hp_rate, energy_rate = C.HP_REGEN_IDLE, C.ENERGY_REGEN_IDLE
            p.fatigue -= C.FATIGUE_RECOVER_IDLE * dt
        p.hp = min(p.max_hp, p.hp + hp_rate * dt)
        p.energy = min(p.max_energy, p.energy + energy_rate * dt)
        p.fatigue = max(0.0, min(C.MAX_FATIGUE, p.fatigue))

    def _train(self, p: Player, stat: str, amount: float) -> None:
        gained = p.stats[stat].add_xp(amount)
        if gained:
            level = p.stats[stat].level
            self._private(p, f"{STAT_LABELS[stat]} subiu para o nível {level}!")
            self._fx.append({"k": "lvl", "id": p.id, "x": round(p.x), "y": round(p.y)})

    # ================================================================ combate

    def _living_targets(self, exclude: Entity | None) -> Iterator[Entity]:
        for e in chain(self.players.values(), self.enemies.values()):
            if e.alive and e is not exclude:
                yield e

    def _can_hit(self, attacker: Entity, target: Entity) -> bool:
        if not target.alive or attacker is target:
            return False
        if isinstance(attacker, Enemy) and isinstance(target, Enemy):
            return False
        if isinstance(target, Player):
            if self.map.is_safe(target.x, target.y):
                return False
            if isinstance(attacker, Player) and self.map.is_safe(attacker.x, attacker.y):
                return False
        return True

    def _melee(self, p: Player) -> None:
        if p.attack_cd > 0:
            return
        p.attack_cd = C.MELEE_COOLDOWN
        if p.state in ("meditate", "rest"):
            p.state = "idle"
        fdx, fdy = DIRECTIONS[p.facing]
        cx, cy = p.x + fdx * C.MELEE_REACH, p.y + fdy * C.MELEE_REACH
        self._fx.append({"k": "swing", "id": p.id})
        hit_any = False
        for target in list(self._living_targets(exclude=p)):
            if _boxes_overlap(cx, cy, C.MELEE_HALF, target.x, target.y, target.half) and self._can_hit(p, target):
                self._damage(target, p.melee_damage, p, via="strength")
                hit_any = True
        if hit_any:
            self._train(p, "strength", C.STRENGTH_XP_PER_HIT)

    def _blast(self, p: Player) -> None:
        if p.blast_cd > 0:
            return
        if p.energy < C.BLAST_COST:
            self._private(p, "Energia insuficiente.")
            return
        p.energy -= C.BLAST_COST
        p.blast_cd = C.BLAST_COOLDOWN
        if p.state in ("meditate", "rest"):
            p.state = "idle"
        fdx, fdy = DIRECTIONS[p.facing]
        proj = Projectile(
            id=self._new_id(),
            owner_id=p.id,
            x=p.x + fdx * (p.half + 4),
            y=p.y + fdy * (p.half + 4),
            vx=fdx * C.BLAST_SPEED,
            vy=fdy * C.BLAST_SPEED,
            damage=p.blast_damage,
            ttl=C.BLAST_TTL,
        )
        self.projectiles[proj.id] = proj

    def _update_projectiles(self, dt: float) -> None:
        for proj in list(self.projectiles.values()):
            proj.x += proj.vx * dt
            proj.y += proj.vy * dt
            proj.ttl -= dt
            owner = self.players.get(proj.owner_id)
            if owner is None or proj.ttl <= 0 or _shot_blocks(self.map.tile_at_px(proj.x, proj.y)):
                del self.projectiles[proj.id]
                continue
            for target in list(self._living_targets(exclude=owner)):
                if _boxes_overlap(proj.x, proj.y, C.BLAST_RADIUS, target.x, target.y, target.half):
                    if self._can_hit(owner, target):
                        self._damage(target, proj.damage, owner, via="spirit")
                        self._train(owner, "spirit", C.SPIRIT_XP_PER_HIT)
                        del self.projectiles[proj.id]
                        break

    def _damage(self, target: Entity, amount: float, source: Entity, via: str | None = None) -> None:
        amount = max(1, round(amount))
        target.hp -= amount
        self._fx.append({"k": "dmg", "id": target.id, "x": round(target.x), "y": round(target.y), "v": amount})
        if isinstance(target, Player):
            if target.state in ("meditate", "rest"):
                target.state = "idle"
            self._train(target, "vitality", amount * C.VITALITY_XP_PER_DAMAGE)
        elif isinstance(target, Enemy) and isinstance(source, Player):
            target.target_id = source.id
        if target.hp <= 0:
            self._kill(target, source, via)

    def _kill(self, target: Entity, killer: Entity, via: str | None) -> None:
        target.hp = 0
        target.state = "dead"
        if isinstance(target, Player):
            target.dead_timer = C.RESPAWN_SECONDS
            target.input = dict.fromkeys(MOVE_KEYS, False)
            who = killer.name if isinstance(killer, Player) else f"um {killer.name}"
            self._system(f"{target.name} foi derrotado por {who}.")
        else:
            target.dead_timer = C.ENEMY_RESPAWN_SECONDS
            if isinstance(killer, Player) and via:
                self._train(killer, via, C.KILL_BONUS_XP)

    # ================================================================ inimigos

    def _spawn_enemy(self, x: float, y: float) -> None:
        enemy = Enemy(id=self._new_id(), name=C.ENEMY_NAME, x=x, y=y, home_x=x, home_y=y)
        enemy.hp = enemy.max_hp
        self.enemies[enemy.id] = enemy

    def _update_enemy(self, e: Enemy, dt: float) -> None:
        if not e.alive:
            e.dead_timer -= dt
            if e.dead_timer <= 0:
                e.x, e.y = e.home_x, e.home_y
                e.hp = e.max_hp
                e.state = "idle"
                e.target_id = None
            return

        e.attack_cd = max(0.0, e.attack_cd - dt)
        target = self._enemy_target(e)
        if target is None:
            e.hp = min(e.max_hp, e.hp + C.ENEMY_HP_REGEN * dt)
            self._enemy_wander(e, dt)
            return

        dx, dy = target.x - e.x, target.y - e.y
        dist = math.hypot(dx, dy)
        _face(e, dx, dy)
        if dist <= C.ENEMY_ATTACK_RANGE:
            e.state = "idle"
            if e.attack_cd <= 0 and self._can_hit(e, target):
                e.attack_cd = C.ENEMY_ATTACK_COOLDOWN
                self._fx.append({"k": "swing", "id": e.id})
                self._damage(target, e.damage, e)
        else:
            step = C.ENEMY_SPEED * dt
            e.x, e.y = self.map.move_box(e.x, e.y, e.half, dx / dist * step, dy / dist * step, _enemy_blocks)
            e.state = "walk"

    def _enemy_target(self, e: Enemy) -> Player | None:
        """Mantém o alvo atual se ainda for válido; senão procura o jogador mais próximo."""

        def valid(p: Player | None) -> bool:
            return p is not None and p.alive and not self.map.is_safe(p.x, p.y)

        if math.hypot(e.x - e.home_x, e.y - e.home_y) > C.ENEMY_LEASH:
            e.target_id = None
            return None
        current = self.players.get(e.target_id) if e.target_id is not None else None
        if valid(current):
            return current
        e.target_id = None
        best, best_dist = None, C.ENEMY_AGGRO_RADIUS
        for p in self.players.values():
            if valid(p):
                d = math.hypot(p.x - e.x, p.y - e.y)
                if d <= best_dist:
                    best, best_dist = p, d
        if best is not None:
            e.target_id = best.id
        return best

    def _enemy_wander(self, e: Enemy, dt: float) -> None:
        e.wander_timer -= dt
        if e.wander_timer <= 0:
            e.wander_timer = self.rng.uniform(1.5, 3.0)
            hx, hy = e.home_x - e.x, e.home_y - e.y
            home_dist = math.hypot(hx, hy)
            if home_dist > C.ENEMY_WANDER_RADIUS:
                e.wander_dir = (hx / home_dist, hy / home_dist)
            elif self.rng.random() < 0.4:
                e.wander_dir = (0.0, 0.0)
            else:
                angle = self.rng.uniform(0, 2 * math.pi)
                e.wander_dir = (math.cos(angle), math.sin(angle))
        wx, wy = e.wander_dir
        if not wx and not wy:
            e.state = "idle"
            return
        step = C.ENEMY_SPEED * 0.5 * dt
        old = (e.x, e.y)
        e.x, e.y = self.map.move_box(e.x, e.y, e.half, wx * step, wy * step, _enemy_blocks)
        _face(e, wx, wy)
        e.state = "walk" if (e.x, e.y) != old else "idle"
