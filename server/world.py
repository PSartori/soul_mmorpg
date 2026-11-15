"""Simulação do mundo.

O servidor é a autoridade: o cliente só manda intenções (movimento, skills, chat)
e recebe de volta o estado. Esta classe não sabe nada de rede; tudo que precisa
ser enviado vai para uma "caixa de saída" que o main.py esvazia a cada tick.

Skills, efeitos e inimigos vêm de data/*.yaml (veja content.py). Este arquivo só
sabe executar os *tipos* de skill (melee, projectile, area, dash, self) e os
*tipos* de efeito (modificadores, flags, dano/cura periódicos).
"""
from __future__ import annotations

import logging
import math
import random
import re
from itertools import chain
from typing import Any, Iterator

from . import config as C
from .content import EQUIP_SLOTS, ClassDef, Content, EffectApplication, ItemDef, SkillDef
from .db import Database
from .duels import DuelSystem
from .orgs import OrgSystem
from .entities import (
    DIRECTIONS, MOVE_KEYS, ActiveEffect, Enemy, Entity, GroundItem, MoveKeys, Player, Projectile,
)
from .inventory import Inventory
from .rules import rule_matches
from .saves import SaveVersionError, migrate
from .stats import STAT_LABELS
from .tilemap import SAFE, SHOT_BLOCKERS, WALK_BLOCKERS, TileMap

log = logging.getLogger("world")

NAME_RE = re.compile(r"[A-Za-z0-9_]{3,16}")


class _KeepUnknown(dict):
    """Para str.format_map: deixa {marcador} desconhecido como está, em vez de dar erro."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"

# (id do jogador destinatário, ou None para todos; mensagem)
Outgoing = tuple[int | None, dict]


def _player_blocks(ch: str) -> bool:
    return ch in WALK_BLOCKERS


def _enemy_blocks(ch: str) -> bool:
    return ch in WALK_BLOCKERS or ch == SAFE


def _shot_blocks(ch: str) -> bool:
    return ch in SHOT_BLOCKERS


def _face(entity: Entity, dx: float, dy: float) -> None:
    """Olha para o eixo dominante; na diagonal exata, o vertical vence.

    client/js/physics.js repete esta regra para a previsão de movimento.
    """
    if abs(dx) > abs(dy):
        entity.facing = "right" if dx > 0 else "left"
    elif dy:
        entity.facing = "down" if dy > 0 else "up"


def _boxes_overlap(ax: float, ay: float, ah: float, bx: float, by: float, bh: float) -> bool:
    return abs(ax - bx) < ah + bh and abs(ay - by) < ah + bh


class World:
    def __init__(self, tilemap: TileMap, db: Database, content: Content, rng: random.Random | None = None):
        self.map = tilemap
        self.db = db
        self.content = content
        self.rng = rng or random.Random()
        self.time = 0.0
        self._next_id = 1
        self.players: dict[int, Player] = {}
        self.enemies: dict[int, Enemy] = {}
        self.projectiles: dict[int, Projectile] = {}
        self.drops: dict[int, GroundItem] = {}  # itens e moedas no chão
        self._fx: list[dict] = []  # efeitos visuais deste tick (dano, golpes, level up...)
        self._outbox: list[Outgoing] = []
        self._save_timer = C.AUTOSAVE_SECONDS
        self.duels = DuelSystem(self)
        self.orgs = OrgSystem(self)

        by_mark = content.enemy_by_mark
        for mark, x, y in tilemap.enemy_spawns:
            if mark not in by_mark:
                raise ValueError(f"Mapa {tilemap.name!r}: nenhum inimigo com mark {mark!r} em enemies.yaml")
            defn = by_mark[mark]
            enemy = Enemy(id=self._new_id(), name=defn.name, x=x, y=y, defn=defn, home_x=x, home_y=y)
            enemy.hp = enemy.max_hp
            self.enemies[enemy.id] = enemy

    def _new_id(self) -> int:
        new_id = self._next_id
        self._next_id += 1
        return new_id

    def _entity(self, entity_id: int | None) -> Entity | None:
        if entity_id is None:
            return None
        return self.players.get(entity_id) or self.enemies.get(entity_id)

    # ================================================================ sessão

    def login(self, name: str, password: str, race: str | None = None) -> tuple[Player | None, str | None]:
        """Entra com um personagem existente ou cria um novo. Devolve (jogador, erro).

        `race` só é usada para criar personagem (precisa ser uma raça selecionável).
        """
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return None, "O nome deve ter de 3 a 16 caracteres (letras, números ou _)."
        if len(password) < 4:
            return None, "A senha deve ter pelo menos 4 caracteres."
        if any(p.name.lower() == name.lower() for p in self.players.values()):
            return None, "Esse personagem já está online."

        record = self.db.load_character(name)
        if record is None:
            race_def = self.content.races.get(race) if isinstance(race, str) else None
            if race_def is None or not race_def.selectable:
                return None, "Escolha uma raça para criar o personagem."
            record = self.db.create_character(name, password)
            data: dict = {"race": race_def.id, "class": race_def.start_class}
            is_new = True
        elif not self.db.verify_password(record, password):
            return None, "Senha incorreta."
        else:
            try:
                data = migrate(record.data)
            except SaveVersionError:
                log.exception("save incompatível: %s", record.name)
                return None, "Não foi possível carregar este personagem (save incompatível)."
            is_new = False

        sx, sy = self.map.spawn
        player = Player(id=self._new_id(), name=record.name, x=sx, y=sy)
        # antes do load: a vida máxima depende da raça e do equipamento
        player.inventory = Inventory.from_dict(data.get("inventory") or {}, self.content.items)
        self._set_class(player, self._resolve_class(data))
        player.load_dict(data)
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

    def _resolve_class(self, data: dict) -> ClassDef:
        """Classe do save; se sumiu do conteúdo, a inicial da raça; senão, a da raça padrão."""
        races, classes = self.content.races, self.content.classes
        race = races.get(data.get("race"))
        cls = classes.get(data.get("class"))
        if cls and (race is None or cls.race == race.id):
            return cls
        if race:
            return classes[race.start_class]
        return classes[races[self.content.default_race].start_class]

    def _set_class(self, p: Player, cls: ClassDef) -> None:
        """Troca classe e, junto, a raça (a da classe). As skills vêm da classe."""
        p.class_def = cls
        p.race_def = self.content.races[cls.race]
        p.skills = list(cls.skills)
        for slot, item in list(p.inventory.equipment.items()):
            if not self._can_equip(p, item):  # a raça/classe nova não pode usar: volta para a bolsa
                del p.inventory.equipment[slot]
                self._give_item(p, item, 1)
                self._private(p, f"{item.name} não serve para {p.race_def.name}/{cls.name} e foi desequipado.")
        p.inventory.dirty = True
        self.orgs.on_race_change(p)
        p.hp = min(p.hp, p.max_hp)
        p.energy = min(p.energy, p.max_energy)

    def logout(self, player: Player) -> None:
        if player.id not in self.players:
            return
        self.duels.on_logout(player)  # antes de sair da lista: o duelo termina com ele presente
        self.players.pop(player.id, None)
        self._save(player)
        self._system(f"{player.name} saiu do jogo.")

    def welcome_payload(self, player: Player) -> dict:
        return {
            "t": "welcome",
            "id": player.id,
            "tick_rate": C.TICK_RATE,
            "half": player.half,
            "map": {
                "name": self.map.name,
                "tile_size": self.map.tile_size,
                "rows": self.map.rows,
                "zones": [z.client_info() for z in self.map.zones],
            },
            "stat_labels": STAT_LABELS,
            **self.content.client_payload(),
        }

    def handle_message(self, player: Player, data: dict) -> None:
        kind = data.get("t")
        if kind == "move":
            seq = data.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= player.last_seq:
                return
            player.last_seq = seq
            keys: MoveKeys = tuple(bool(data.get(k)) for k in MOVE_KEYS)  # type: ignore[assignment]
            player.move_queue.append((seq, keys))
            while len(player.move_queue) > C.MAX_MOVE_QUEUE:
                player.move_queue.popleft()
        elif kind == "skill":
            skill_id = data.get("id")
            facing = data.get("f") if data.get("f") in DIRECTIONS else None
            if skill_id in player.skills and len(player.actions) < C.MAX_QUEUED_ACTIONS:
                player.actions.append(("skill", skill_id, facing))
        elif kind in ("meditate", "rest"):
            if len(player.actions) < C.MAX_QUEUED_ACTIONS:
                player.actions.append((kind, None, None))
        elif kind == "item":
            op, slot = data.get("op"), data.get("slot")
            valid = (
                (op in ("use", "drop") and isinstance(slot, int) and not isinstance(slot, bool))
                or (op == "unequip" and slot in EQUIP_SLOTS)
            )
            if valid and len(player.actions) < C.MAX_QUEUED_ACTIONS:
                player.actions.append(("item", op, slot))
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
            command, *args = text.split()
            command = command.lower()
            if self.orgs.handle(player, command, args, text):
                return
            if command == "/who":
                names = ", ".join(sorted(p.name for p in self.players.values()))
                self._private(player, f"Online ({len(self.players)}): {names}")
            elif command == "/duelo":
                if args:
                    self.duels.challenge(player, args[0])
                else:
                    self._private(player, "Use: /duelo Nome")
            elif command == "/aceitar":
                self.duels.accept(player)
            elif command == "/recusar":
                self.duels.decline(player)
            elif command == "/render":
                self.duels.forfeit(player)
            else:
                self._private(player, f"Comando desconhecido: {command}. Disponíveis: /who, /duelo Nome, "
                                      "/aceitar, /recusar, /render, /org, /entrar, /sair, /o, /assumir, "
                                      "/disputar, /promover, /rebaixar, /expulsar")
            return
        self._outbox.append((None, {"t": "chat", "from": player.name, "text": text}))

    # ================================================================ tick

    def step(self, dt: float) -> None:
        self.time += dt
        for player in list(self.players.values()):
            self._update_player(player, dt)
        self.duels.update()
        for enemy in list(self.enemies.values()):
            self._update_enemy(enemy, dt)
        self._update_projectiles(dt)
        self._update_drops()

        self._save_timer -= dt
        if self._save_timer <= 0:
            self._save_timer = C.AUTOSAVE_SECONDS
            self.save_all()

        self._emit_snapshots()

    def _emit_snapshots(self) -> None:
        ents = []
        for e in chain(self.players.values(), self.enemies.values()):
            snap = e.snapshot()
            if isinstance(e, Player) and (opponent := self.duels.opponent_id(e)) is not None:
                snap["du"] = opponent  # em duelo com
            if isinstance(e, Player) and (org := self.orgs.snapshot(e)):
                snap["o"] = org  # [organização, cargo, vaga]
            ents.append(snap)
        projs = [p.snapshot() for p in self.projectiles.values()]
        drops = [d.snapshot() for d in self.drops.values()]
        fx, self._fx = self._fx, []
        for player in self.players.values():
            you = player.private_snapshot()
            zone = self.map.zone_at(player.x, player.y)
            you["zone"] = {"t": zone.type, "n": zone.name}
            you["duel"] = self.duels.client_info(player)
            if player.inventory.dirty:  # inventário só vai quando muda
                you["inv"] = player.inventory.client_info()
                player.inventory.dirty = False
            self._outbox.append(
                (player.id, {"t": "snap", "ents": ents, "projs": projs, "drops": drops, "fx": fx, "you": you})
            )

    # ================================================================ jogadores

    def _update_player(self, p: Player, dt: float) -> None:
        if not p.alive:
            p.dead_timer -= dt
            p.actions.clear()
            if p.move_queue:  # descarta comandos, mas confirma para o cliente não ficar esperando
                p.ack = p.move_queue[-1][0]
                p.move_queue.clear()
            if p.dead_timer <= 0:
                p.x, p.y = self.map.spawn
                p.hp, p.energy = p.max_hp, p.max_energy
                p.state = "idle"
            return

        self._tick_cooldowns(p, dt)
        for item_id in list(p.item_cooldowns):
            p.item_cooldowns[item_id] -= dt
            if p.item_cooldowns[item_id] <= 0:
                del p.item_cooldowns[item_id]
        self._update_effects(p, dt)
        if not p.alive:
            return

        # Movimento antes das ações, para um golpe sair na direção para onde acabou de virar
        commands = 2 if len(p.move_queue) > C.MOVE_QUEUE_CATCHUP else 1
        moved = False
        for _ in range(min(commands, len(p.move_queue))):
            seq, keys = p.move_queue.popleft()
            p.ack = seq
            moved = self._move_player(p, dt, keys) or moved
        if not moved and p.state == "walk":
            p.state = "idle"

        actions, p.actions = p.actions, []
        for kind, skill_id, facing in actions:
            if kind == "meditate":
                self._toggle_meditate(p)
            elif kind == "rest":
                p.state = "idle" if p.state == "rest" else "rest"
            elif kind == "skill":
                self._use_skill(p, self.content.skills[skill_id], facing)
            elif kind == "item":
                self._item_action(p, skill_id, facing)  # (op, espaço)
            if not p.alive:
                return

        self._pickup(p)
        self._regen_player(p, dt)

    def _toggle_meditate(self, p: Player) -> None:
        if p.state == "meditate":
            p.state = "idle"
        elif p.has_flag("stunned"):
            return
        elif p.fatigue >= C.MEDITATE_FATIGUE_LIMIT:
            self._private(p, "Você está cansado demais para meditar. Descanse (R).")
        else:
            p.state = "meditate"

    def _move_player(self, p: Player, dt: float, keys: MoveKeys) -> bool:
        """Executa um comando de movimento (um tick). Devolve se andou.

        client/js/physics.js repete este cálculo para a previsão de movimento.
        """
        up, down, left, right = keys
        dx = int(right) - int(left)
        dy = int(down) - int(up)
        speed = p.speed
        if (not dx and not dy) or speed <= 0:
            return False
        if p.state in ("meditate", "rest"):  # andar interrompe meditação/descanso
            p.state = "idle"
        _face(p, dx, dy)
        scale = speed * dt * (math.sqrt(0.5) if dx and dy else 1.0)
        old_x, old_y = p.x, p.y
        p.x, p.y = self.map.move_box(p.x, p.y, p.half, dx * scale, dy * scale, _player_blocks)
        distance = math.hypot(p.x - old_x, p.y - old_y)
        p.state = "walk" if distance > 0 else "idle"
        if distance > 0:
            self._train(p, "agility", distance * C.AGILITY_XP_PER_PX)
        return distance > 0

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
        p.energy = min(p.max_energy, p.energy + energy_rate * p.modifier("energy_regen") * dt)
        p.fatigue = max(0.0, min(C.MAX_FATIGUE, p.fatigue))

    def _train(self, p: Player, stat: str, amount: float, depth: int = 0, use_rate: bool = True) -> None:
        """Dá XP a um atributo, aplicando o multiplicador da classe (train_rate)."""
        if use_rate and p.class_def:
            amount *= p.class_def.train_rate.get(stat, 1.0)
        gained = p.stats[stat].add_xp(amount)
        if gained:
            level = p.stats[stat].level
            self._private(p, f"{STAT_LABELS[stat]} subiu para o nível {level}!")
            self._fx.append({"k": "lvl", "id": p.id, "x": round(p.x), "y": round(p.y)})
            self._fire("level_up", p, {"stat": stat, "level": level}, depth)

    # ================================================================ regras (data/rules.yaml)

    def _fire(self, event: str, p: Player, ctx: dict, depth: int = 0) -> None:
        """Avalia as regras de um evento para o jogador p, de cima para baixo."""
        if depth > C.MAX_RULE_DEPTH:
            log.warning("regras encadeadas demais em '%s' para %s; parando", event, p.name)
            return
        membership = self.orgs.of(p)
        full = {  # raça/classe/organização de ANTES das ações
            "race": p.race_id,
            "class": p.class_id,
            "org": membership.org if membership else None,
            "rank": membership.rank if membership else None,
            **ctx,
        }
        for rule in self.content.rules_by_event[event]:
            if rule_matches(rule, full, p.flags, self.rng):
                for name, args in rule.actions:
                    self._do_action(p, name, args, full, depth)
                if rule.stop:
                    break

    def _do_action(self, p: Player, name: str, args: Any, ctx: dict, depth: int) -> None:
        if name == "message":
            self._private(p, self._format(args, p, ctx))
        elif name == "broadcast":
            self._system(self._format(args, p, ctx))
        elif name == "lose_xp":
            self._lose_xp(p, args["percent"], args["stat"])
        elif name == "gain_xp":
            self._train(p, args["stat"], args["amount"], depth + 1, use_rate=False)
        elif name == "set_class":
            self._set_class(p, self.content.classes[args])
        elif name == "set_race":
            self._set_class(p, self.content.classes[args["class"]])
        elif name == "set_flag":
            p.flags.add(args)
        elif name == "clear_flag":
            p.flags.discard(args)
        elif name == "add_effect":
            self._apply_effect(p, args, None)
        elif name == "give_item":
            self._give_item(p, self.content.items[args["id"]], args["qty"])
        elif name == "give_gold":
            p.inventory.add_gold(args)
        elif name == "lose_gold":
            gold = p.inventory.gold
            lost = int(gold * args["percent"] / 100) if "percent" in args else min(gold, args["amount"])
            p.inventory.add_gold(-lost)

    @staticmethod
    def _lose_xp(p: Player, percent: float, which: str) -> None:
        """Perde uma % do XP necessário para o próximo nível; nunca perde nível."""
        if which == "highest":
            stats = [max(p.stats.values(), key=lambda s: (s.level, s.xp))]
        elif which == "all":
            stats = list(p.stats.values())
        else:
            stats = [p.stats[which]]
        for s in stats:
            s.xp = max(0.0, s.xp - s.xp_to_next() * percent / 100)

    @staticmethod
    def _format(text: str, p: Player, ctx: dict) -> str:
        values = _KeepUnknown(
            name=p.name,
            race=p.race_def.name if p.race_def else "",
            **{"class": p.class_def.name if p.class_def else ""},
            killer=ctx.get("killer_name") or "",
            target=ctx.get("victim_name") or "",
            stat=STAT_LABELS.get(ctx.get("stat", ""), ""),
            level=ctx.get("level", ""),
        )
        try:
            return text.format_map(values)
        except (ValueError, AttributeError, IndexError, KeyError):
            return text  # texto com chaves malformadas: mostra como está

    # ================================================================ skills

    @staticmethod
    def _tick_cooldowns(e: Entity, dt: float) -> None:
        for skill_id in list(e.cooldowns):
            e.cooldowns[skill_id] -= dt
            if e.cooldowns[skill_id] <= 0:
                del e.cooldowns[skill_id]

    def _use_skill(self, caster: Entity, skill: SkillDef, facing: str | None = None) -> bool:
        """Tenta usar uma skill. Devolve se foi usada."""
        if not caster.alive or caster.has_flag("stunned") or caster.has_flag("silenced"):
            return False
        if caster.cooldowns.get(skill.id, 0) > 0:
            return False
        if isinstance(caster, Player):
            if caster.energy < skill.cost_energy:
                self._private(caster, f"Energia insuficiente para {skill.name}.")
                return False
            caster.energy -= skill.cost_energy
            if caster.state in ("meditate", "rest"):
                caster.state = "idle"
            if facing:  # o cliente diz para onde estava olhando ao apertar a tecla
                caster.facing = facing
        caster.cooldowns[skill.id] = skill.cooldown

        for app in skill.self_effects:
            self._apply_effect(caster, app, caster)

        if skill.type == "melee":
            self._cast_melee(caster, skill)
        elif skill.type == "projectile":
            self._cast_projectile(caster, skill)
        elif skill.type == "area":
            self._cast_area(caster, skill)
        elif skill.type == "dash":
            self._cast_dash(caster, skill)
        elif skill.type == "self":
            self._fx.append({"k": "buff", "id": caster.id, "c": skill.color})
            if isinstance(caster, Player):
                self._train_from_skill(caster, skill)
        return True

    def _skill_damage(self, caster: Entity, skill: SkillDef) -> float:
        raw = skill.damage_base + sum(scale * caster.stat_level(stat) for stat, scale in skill.damage_scale.items())
        return raw * caster.modifier("damage")

    def _train_from_skill(self, p: Player, skill: SkillDef) -> None:
        for stat, xp in skill.train.items():
            self._train(p, stat, xp)

    def _living_targets(self, exclude: Entity | None) -> Iterator[Entity]:
        for e in chain(self.players.values(), self.enemies.values()):
            if e.alive and e is not exclude:
                yield e

    def _can_hit(self, attacker: Entity, target: Entity) -> bool:
        """Quem pode ferir quem.

        - jogador × jogador: em duelo, só entre os dois (em qualquer zona); fora de
          duelo, só se os dois estiverem numa zona PvP;
        - inimigo × jogador: em qualquer lugar menos na zona segura;
        - jogador × inimigo: sempre; inimigo × inimigo: nunca.
        """
        if not target.alive or attacker is target:
            return False
        if isinstance(attacker, Enemy) and isinstance(target, Enemy):
            return False
        if isinstance(attacker, Player) and isinstance(target, Player):
            if self.duels.opponent_id(attacker) == target.id:
                return self.duels.fighting(attacker, target)  # não vale durante a contagem
            if self.duels.duel_of(attacker) or self.duels.duel_of(target):
                return False  # ninguém de fora entra (nem sai) de um duelo
            return (self.map.zone_at(attacker.x, attacker.y).type == "pvp"
                    and self.map.zone_at(target.x, target.y).type == "pvp")
        if isinstance(target, Player):
            return not self.map.is_safe(target.x, target.y)
        return True

    def _cast_melee(self, caster: Entity, skill: SkillDef) -> None:
        fdx, fdy = DIRECTIONS[caster.facing]
        cx, cy = caster.x + fdx * skill.reach, caster.y + fdy * skill.reach
        self._fx.append({"k": "swing", "id": caster.id, "r": skill.reach + skill.size})
        damage = self._skill_damage(caster, skill)
        for target in list(self._living_targets(exclude=caster)):
            if _boxes_overlap(cx, cy, skill.size, target.x, target.y, target.half) and self._can_hit(caster, target):
                self._skill_hit(caster, target, skill, damage)

    def _cast_projectile(self, caster: Entity, skill: SkillDef) -> None:
        fdx, fdy = DIRECTIONS[caster.facing]
        base_angle = math.atan2(fdy, fdx)
        damage = self._skill_damage(caster, skill)
        for i in range(skill.count):
            angle = base_angle + math.radians(skill.spread) * (i - (skill.count - 1) / 2)
            vx, vy = math.cos(angle), math.sin(angle)
            proj = Projectile(
                id=self._new_id(),
                owner_id=caster.id,
                skill_id=skill.id,
                x=caster.x + vx * (caster.half + 4),
                y=caster.y + vy * (caster.half + 4),
                vx=vx * skill.speed,
                vy=vy * skill.speed,
                damage=damage,
                ttl=skill.ttl,
                radius=skill.radius,
            )
            self.projectiles[proj.id] = proj

    def _cast_area(self, caster: Entity, skill: SkillDef) -> None:
        self._fx.append({"k": "area", "id": caster.id, "r": skill.radius, "c": skill.color})
        damage = self._skill_damage(caster, skill)
        for target in list(self._living_targets(exclude=caster)):
            reach = skill.radius + target.half
            if math.hypot(target.x - caster.x, target.y - caster.y) <= reach and self._can_hit(caster, target):
                self._skill_hit(caster, target, skill, damage)

    def _cast_dash(self, caster: Entity, skill: SkillDef) -> None:
        blocks = _player_blocks if isinstance(caster, Player) else _enemy_blocks
        fdx, fdy = DIRECTIONS[caster.facing]
        start = (caster.x, caster.y)
        remaining = skill.distance
        while remaining > 0:
            step = min(C.DASH_STEP, remaining)
            nx, ny = self.map.move_box(caster.x, caster.y, caster.half, fdx * step, fdy * step, blocks)
            if (nx, ny) == (caster.x, caster.y):
                break  # bateu numa parede
            caster.x, caster.y = nx, ny
            remaining -= step
        if caster.state in ("meditate", "rest"):
            caster.state = "idle"
        self._fx.append({"k": "dash", "id": caster.id, "x0": round(start[0]), "y0": round(start[1]),
                         "x1": round(caster.x), "y1": round(caster.y)})
        if isinstance(caster, Player):
            self._train_from_skill(caster, skill)

    def _skill_hit(self, caster: Entity | None, target: Entity, skill: SkillDef, damage: float) -> None:
        if damage > 0:
            self._damage(target, damage, caster, skill)
        if target.alive:
            for app in skill.effects:
                self._apply_effect(target, app, caster)
        if isinstance(caster, Player):
            self._train_from_skill(caster, skill)

    def _update_projectiles(self, dt: float) -> None:
        for proj in list(self.projectiles.values()):
            proj.x += proj.vx * dt
            proj.y += proj.vy * dt
            proj.ttl -= dt
            owner = self._entity(proj.owner_id)
            if owner is None or proj.ttl <= 0 or _shot_blocks(self.map.tile_at_px(proj.x, proj.y)):
                del self.projectiles[proj.id]
                continue
            skill = self.content.skills[proj.skill_id]
            for target in list(self._living_targets(exclude=owner)):
                if _boxes_overlap(proj.x, proj.y, proj.radius, target.x, target.y, target.half):
                    if self._can_hit(owner, target):
                        self._skill_hit(owner, target, skill, proj.damage)
                        del self.projectiles[proj.id]
                        break

    # ================================================================ dano e efeitos

    def _damage(
        self,
        target: Entity,
        amount: float,
        source: Entity | None,
        skill: SkillDef | None = None,
        effect_id: str | None = None,
    ) -> None:
        amount = max(1, round(amount * target.modifier("damage_taken")))
        duel_defeat = (
            isinstance(target, Player)
            and isinstance(source, Player)
            and self.duels.fighting(target, source)
            and target.hp - amount < 1
        )
        if duel_defeat:  # duelo não mata: fica com 1 de vida e perde
            amount = max(0, math.ceil(target.hp) - 1)
        lost = min(amount, max(0.0, target.hp))  # vida que realmente saiu (golpe exagerado não treina mais)
        target.hp -= amount
        if duel_defeat:
            target.hp = max(1.0, target.hp)
        self._fx.append({"k": "dmg", "id": target.id, "x": round(target.x), "y": round(target.y), "v": amount})
        if isinstance(target, Player):
            if target.state in ("meditate", "rest"):
                target.state = "idle"
            self._train(target, "vitality", lost * C.VITALITY_XP_PER_DAMAGE)
        elif isinstance(target, Enemy) and isinstance(source, Player):
            target.target_id = source.id
        if duel_defeat:
            self.duels.finish(self.duels.duel_of(target), loser=target, reason="defeat")  # type: ignore[arg-type]
        elif target.hp <= 0:
            self._kill(target, source, skill, effect_id)

    def _kill(
        self, target: Entity, killer: Entity | None, skill: SkillDef | None, effect_id: str | None = None
    ) -> None:
        zone = self.map.zone_at(target.x, target.y).type
        effects_at_death = sorted(target.effects)
        was_dueling = isinstance(target, Player) and self.duels.on_death(target)
        target.hp = 0
        target.state = "dead"
        target.effects.clear()
        target.cooldowns.clear()

        if isinstance(target, Player):
            target.dead_timer = C.RESPAWN_SECONDS
            if killer is None:
                self._system(f"{target.name} foi derrotado.")
            else:
                who = killer.name if isinstance(killer, Player) else f"um {killer.name}"
                self._system(f"{target.name} foi derrotado por {who}.")
            killer_player = killer if isinstance(killer, Player) else None
            self._fire("death", target, {
                "killer": "player" if killer_player else "enemy" if isinstance(killer, Enemy) else "none",
                "killer_enemy": killer.defn.id if isinstance(killer, Enemy) else None,
                "killer_race": killer_player.race_id if killer_player else None,
                "killer_class": killer_player.class_id if killer_player else None,
                "killer_name": killer.name if killer else None,  # só para textos ({killer})
                "skill": skill.id if skill else None,
                "effect": effect_id,
                "effects": effects_at_death,
                "zone": zone,
                "duel": was_dueling,  # estava num duelo (que a morte encerrou)
            })
        elif isinstance(target, Enemy):
            target.dead_timer = target.defn.respawn
            self._spawn_loot(target, killer)
            # Bônus de abate vai para o atributo principal (o primeiro de "train") da skill usada
            if isinstance(killer, Player) and skill and skill.train:
                self._train(killer, next(iter(skill.train)), target.defn.kill_xp)

        if isinstance(killer, Player):
            victim_player = target if isinstance(target, Player) else None
            self._fire("kill", killer, {
                "victim": "player" if victim_player else "enemy",
                "victim_enemy": target.defn.id if isinstance(target, Enemy) else None,
                "victim_race": victim_player.race_id if victim_player else None,
                "victim_class": victim_player.class_id if victim_player else None,
                "victim_name": target.name,  # só para textos ({target})
                "skill": skill.id if skill else None,
                "zone": zone,
            })

    def _apply_effect(self, target: Entity, app: EffectApplication, source: Entity | None) -> None:
        if not target.alive:
            return
        defn = self.content.effects[app.effect]
        source_id = source.id if source else None
        current = target.effects.get(defn.id)
        if current:  # reaplicar só renova a duração
            current.remaining = max(current.remaining, app.duration)
            current.source_id = source_id
        else:
            target.effects[defn.id] = ActiveEffect(defn, app.duration, defn.tick_every, source_id)
        if "stunned" in defn.flags and target.state in ("meditate", "rest", "walk"):
            target.state = "idle"

    def _update_effects(self, e: Entity, dt: float) -> None:
        for effect_id, active in list(e.effects.items()):
            active.remaining -= dt
            d = active.defn
            if d.tick_every > 0:
                active.tick_timer -= dt
                if active.tick_timer <= 1e-9:
                    active.tick_timer += d.tick_every
                    if d.tick_heal:
                        healed = min(e.max_hp - e.hp, d.tick_heal)
                        e.hp += healed
                        if healed > 0:
                            self._fx.append({"k": "heal", "id": e.id, "x": round(e.x), "y": round(e.y), "v": round(healed)})
                    # Na zona segura, efeitos não ferem jogadores (a não ser vindos do oponente de duelo)
                    source = self._entity(active.source_id)
                    protected = (
                        isinstance(e, Player)
                        and self.map.is_safe(e.x, e.y)
                        and not (isinstance(source, Player) and self.duels.fighting(e, source))
                    )
                    if d.tick_damage and not protected:
                        self._damage(e, d.tick_damage, source, effect_id=d.id)
                        if not e.alive:
                            return
            if active.remaining <= 1e-9:
                e.effects.pop(effect_id, None)

    # ================================================================ itens

    def _can_equip(self, p: Player, item: ItemDef) -> bool:
        return (not item.races or p.race_id in item.races) and (not item.classes or p.class_id in item.classes)

    def _item_action(self, p: Player, op: str, slot: int | str) -> None:
        inv = p.inventory
        if op == "unequip":
            item = inv.equipment.get(slot)  # type: ignore[arg-type]
            if item is None:
                return
            if inv.add(item, 1):
                self._private(p, "Inventário cheio.")
                return
            del inv.equipment[slot]  # type: ignore[arg-type]
            self._clamp_resources(p)
            return
        stack = inv.slots[slot] if 0 <= slot < inv.size else None  # type: ignore[operator]
        if stack is None:
            return
        if op == "drop":
            taken = inv.take(slot, stack.qty)  # type: ignore[arg-type]
            if taken:
                fdx, fdy = DIRECTIONS[p.facing]
                x, y = p.x + fdx * C.DROP_DISTANCE, p.y + fdy * C.DROP_DISTANCE
                if self.map.box_blocked(x, y, 4, _player_blocks):
                    x, y = p.x, p.y
                drop = self._drop(taken.item, taken.qty, x, y, owner=None, scatter=False)
                drop.ignore_id, drop.ignore_until = p.id, self.time + C.DROP_PICKUP_DELAY
        elif stack.item.type == "consumable":
            self._use_consumable(p, slot, stack.item)  # type: ignore[arg-type]
        elif stack.item.type == "equipment":
            self._equip(p, slot, stack.item)  # type: ignore[arg-type]
        else:
            self._private(p, f"{stack.item.name} não tem uso direto.")

    def _use_consumable(self, p: Player, slot: int, item: ItemDef) -> None:
        if p.item_cooldowns.get(item.id, 0) > 0:
            self._private(p, f"Espere {p.item_cooldowns[item.id]:.0f}s para usar {item.name} de novo.")
            return
        useless = (
            not item.effects
            and (not item.heal or p.hp >= p.max_hp)
            and (not item.energy or p.energy >= p.max_energy)
        )
        if useless:
            self._private(p, f"Não precisa usar {item.name} agora.")
            return
        p.inventory.take(slot, 1)
        p.item_cooldowns[item.id] = item.cooldown
        if item.heal:
            healed = min(item.heal, p.max_hp - p.hp)
            p.hp += healed
            if healed > 0:
                self._fx.append({"k": "heal", "id": p.id, "x": round(p.x), "y": round(p.y), "v": round(healed)})
        if item.energy:
            p.energy = min(p.max_energy, p.energy + item.energy)
        for app in item.effects:
            self._apply_effect(p, app, p)

    def _equip(self, p: Player, slot: int, item: ItemDef) -> None:
        if not self._can_equip(p, item):
            who = ", ".join(
                [self.content.races[r].name for r in item.races] + [self.content.classes[c].name for c in item.classes]
            )
            self._private(p, f"{item.name} é só para: {who}.")
            return
        inv = p.inventory
        inv.take(slot, 1)
        previous = inv.equipment.get(item.slot)  # type: ignore[arg-type]
        inv.equipment[item.slot] = item  # type: ignore[index]
        if previous:
            inv.add(previous, 1)  # sempre cabe: o espaço do item novo acabou de vagar
        inv.dirty = True
        self._clamp_resources(p)

    @staticmethod
    def _clamp_resources(p: Player) -> None:
        p.hp = min(p.hp, p.max_hp)
        p.energy = min(p.energy, p.max_energy)

    def _give_item(self, p: Player, item: ItemDef, qty: int) -> None:
        """Põe no inventário; o que não couber cai no chão aos pés dele (só ele pega)."""
        leftover = p.inventory.add(item, qty)
        if leftover:
            self._drop(item, leftover, p.x, p.y, owner=p.id, scatter=False)
            self._private(p, "Inventário cheio: o que não coube ficou no chão.")

    def _drop(self, item: ItemDef | None, qty: int, x: float, y: float, owner: int | None,
              scatter: bool = True) -> GroundItem:
        if scatter:
            angle = self.rng.uniform(0, 2 * math.pi)
            dist = self.rng.uniform(0, C.LOOT_SCATTER)
            nx, ny = x + math.cos(angle) * dist, y + math.sin(angle) * dist
            if not self.map.box_blocked(nx, ny, 4, _player_blocks):
                x, y = nx, ny
        drop = GroundItem(
            id=self._new_id(), item=item, qty=qty, x=x, y=y, owner_id=owner,
            owner_until=self.time + C.LOOT_OWNER_SECONDS, expires_at=self.time + C.LOOT_EXPIRE_SECONDS,
        )
        self.drops[drop.id] = drop
        return drop

    def _spawn_loot(self, enemy: Enemy, killer: Entity | None) -> None:
        owner = killer.id if isinstance(killer, Player) else None
        low, high = enemy.defn.gold
        gold = self.rng.randint(low, high) if high > 0 else 0
        if gold:
            self._drop(None, gold, enemy.x, enemy.y, owner)
        for entry in enemy.defn.loot:
            if self.rng.random() < entry.chance:
                qty = self.rng.randint(entry.qty_min, entry.qty_max)
                self._drop(self.content.items[entry.item], qty, enemy.x, enemy.y, owner)

    def _pickup(self, p: Player) -> None:
        for drop in list(self.drops.values()):
            if math.hypot(drop.x - p.x, drop.y - p.y) > C.PICKUP_RADIUS:
                continue
            if drop.owner_id not in (None, p.id) and self.time < drop.owner_until:
                continue  # ainda é de quem matou
            if drop.ignore_id == p.id and self.time < drop.ignore_until:
                continue  # acabou de largar
            if drop.item is None:
                p.inventory.add_gold(drop.qty)
                taken = drop.qty
                label = f"+{taken} moedas"
            else:
                taken = drop.qty - p.inventory.add(drop.item, drop.qty)
                label = f"+{taken} {drop.item.name}"
            if taken == 0:
                continue  # inventário cheio
            drop.qty -= taken
            if drop.qty == 0:
                del self.drops[drop.id]
            self._fx.append({"k": "pick", "id": p.id, "x": round(p.x), "y": round(p.y), "t": label,
                             "r": drop.item.rarity if drop.item else "gold"})

    def _update_drops(self) -> None:
        for drop in list(self.drops.values()):
            if self.time >= drop.expires_at:
                del self.drops[drop.id]

    # ================================================================ inimigos

    def _update_enemy(self, e: Enemy, dt: float) -> None:
        if not e.alive:
            e.dead_timer -= dt
            if e.dead_timer <= 0:
                e.x, e.y = e.home_x, e.home_y
                e.hp = e.max_hp
                e.state = "idle"
                e.target_id = None
            return

        self._tick_cooldowns(e, dt)
        self._update_effects(e, dt)
        if not e.alive:
            return
        if e.has_flag("stunned"):
            e.state = "idle"
            return

        target = self._enemy_target(e)
        if target is None:
            e.hp = min(e.max_hp, e.hp + C.ENEMY_HP_REGEN * dt)
            self._enemy_wander(e, dt)
            return

        dx, dy = target.x - e.x, target.y - e.y
        dist = math.hypot(dx, dy)
        _face(e, dx, dy)

        skills = [self.content.skills[sid] for sid in e.defn.skills]
        for skill in skills:
            if e.cooldowns.get(skill.id, 0) <= 0 and dist <= skill.ai_range and self._can_hit(e, target):
                if self._use_skill(e, skill):
                    break

        # Aproxima até o alcance da skill de menor alcance (quem tem golpe chega perto)
        attack_ranges = [s.ai_range for s in skills if s.type != "self"]
        keep_distance = min(attack_ranges) if attack_ranges else 0.0
        if dist > keep_distance and dist > 0:
            self._enemy_chase_step(e, dx / dist, dy / dist, dt)
        else:
            e.state = "idle"

    def _enemy_chase_step(self, e: Enemy, ux: float, uy: float, dt: float) -> None:
        """Anda na direção do alvo; se travar num obstáculo, contorna pelo lado por um tempo.

        Não é pathfinding: resolve árvores e quinas, mas não labirintos.
        """
        old = (e.x, e.y)
        step = e.speed * dt

        def sidestep(side: int) -> bool:  # perpendicular à direção do alvo
            e.x, e.y = self.map.move_box(e.x, e.y, e.half, -uy * side * step, ux * side * step, _enemy_blocks)
            return (e.x, e.y) != old

        if e.detour_timer > 0:
            e.detour_timer -= dt
            if not sidestep(e.detour_side):
                e.detour_side = -e.detour_side
                e.detour_timer = 0.0
        else:
            e.x, e.y = self.map.move_box(e.x, e.y, e.half, ux * step, uy * step, _enemy_blocks)
            if (e.x, e.y) == old:  # travou: começa a contornar (tenta um lado, depois o outro)
                for side in (e.detour_side, -e.detour_side):
                    if sidestep(side):
                        e.detour_side = side
                        e.detour_timer = C.ENEMY_DETOUR_SECONDS
                        break
        e.state = "walk" if (e.x, e.y) != old else "idle"

    def _enemy_target(self, e: Enemy) -> Player | None:
        """Mantém o alvo atual se ainda for válido; senão procura o jogador mais próximo."""

        def valid(p: Player | None) -> bool:
            return p is not None and p.alive and not self.map.is_safe(p.x, p.y)

        if math.hypot(e.x - e.home_x, e.y - e.home_y) > e.defn.leash:
            e.target_id = None
            return None
        current = self.players.get(e.target_id) if e.target_id is not None else None
        if valid(current):
            return current
        e.target_id = None
        best, best_dist = None, e.defn.aggro_radius
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
            if home_dist > e.defn.wander_radius:
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
        step = e.speed * 0.5 * dt
        old = (e.x, e.y)
        e.x, e.y = self.map.move_box(e.x, e.y, e.half, wx * step, wy * step, _enemy_blocks)
        _face(e, wx, wy)
        e.state = "walk" if (e.x, e.y) != old else "idle"
