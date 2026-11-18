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
import os
import random
import re
import time
from collections.abc import Callable
from itertools import chain
from typing import Any, Iterator

from . import config as C
from .content import EQUIP_SLOTS, ClassDef, Content, ContentError, EffectApplication, EnemyDef, ItemDef, SkillDef
from .admin import AdminSystem
from .db import Database, hash_password
from .duels import DuelSystem
from .npcs import NpcSystem
from .orgs import OrgSystem, total_level
from .pathfinding import clear_line, find_path, free_spot, tile_of
from .quests import QuestSystem
from .training import TrainingSystem
from .crafting import CraftingSystem
from .parties import PartySystem
from .bugs import BugReports
from .social import SocialSystem
from .trade import TradeSystem
from .events import EventSystem
from .economy import Economy
from .travel import TravelSystem
from .upkeep import Upkeep
from .entities import (
    DIRECTIONS, MOVE_KEYS, ActiveEffect, Enemy, Entity, GroundItem, MoveKeys, Player, Projectile,
)
from .inventory import Inventory
from .rules import rule_matches
from .saves import SaveVersionError, migrate
from .stats import STAT_LABELS
from .tilemap import SAFE, SHOT_BLOCKERS, WALK_BLOCKERS, Solid, TileMap

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
    def __init__(
        self,
        maps: TileMap | dict[str, TileMap],
        db: Database,
        content: Content,
        rng: random.Random | None = None,
        start_map: str | None = None,
        clock: Callable[[], float] | None = None,
    ):
        if isinstance(maps, TileMap):
            maps = {maps.name: maps}
        self.maps = maps
        # mapa onde nascem personagens novos
        self.start_map_id = next(m for m in (start_map, C.START_MAP, next(iter(maps))) if m in maps)
        self.db = db
        self.content = content
        self.clock = clock or time.time  # horário real (bans e silêncios); nos testes dá para trocar
        # de onde vem o conteúdo numa recarga (main.py aponta para data/; os testes trocam)
        self.reload_source: Callable[[], tuple[Content, dict[str, TileMap]]] | None = None
        self.rng = rng or random.Random()
        self.time = 0.0
        self._next_id = 1
        self.players: dict[int, Player] = {}
        self.enemies: dict[int, Enemy] = {}
        self.projectiles: dict[int, Projectile] = {}
        self.drops: dict[int, GroundItem] = {}  # itens e moedas no chão
        self._fx: list[tuple[str, float, float, dict]] = []  # efeitos visuais do tick: (mapa, x, y, dados)
        self._outbox: list[Outgoing] = []
        self._save_timer = C.AUTOSAVE_SECONDS
        self.duels = DuelSystem(self)
        self.orgs = OrgSystem(self)
        self.admin = AdminSystem(self)
        self.npcs = NpcSystem(self)  # personagens do mundo: conversas e lojas
        self.quests = QuestSystem(self)  # missões (data/quests.yaml)
        self.training = TrainingSystem(self)  # skills aprendidas, barra e treinadores (data/trainers.yaml)
        self.crafting = CraftingSystem(self)  # oficinas e receitas (data/crafting.yaml)
        self.parties = PartySystem(self)  # grupos de jogadores
        self.bugs = BugReports(self)  # relatos de bug dos jogadores
        self.social = SocialSystem(self)  # roleplay: fala local, ações, privado, ignorar e perfil
        self.trades = TradeSystem(self)  # troca entre jogadores
        self.events = EventSystem(self)  # eventos em ondas (data/events.yaml)
        self.economy = Economy(self)  # de onde as moedas entram e para onde vão
        self.upkeep = Upkeep(self)  # desgaste e conserto, ampliações e taxas
        self.travel = TravelSystem(self)  # viagem rápida entre NPCs

        self._spawn_enemies()
        self.npcs.spawn()

    def _spawn_enemies(self) -> None:
        """Cria os inimigos de todos os mapas, com vida cheia, nos pontos de nascimento."""
        self.enemies.clear()
        by_mark = self.content.enemy_by_mark
        for map_id, tilemap in self.maps.items():
            # mapas em texto marcam com a letra do inimigo; mapas do Tiled, com o id
            for ref, x, y in tilemap.enemy_spawns:
                defn = by_mark.get(ref) or self.content.enemies.get(ref)
                if defn is None:
                    raise ValueError(f"Mapa {tilemap.name!r}: nenhum inimigo {ref!r} em enemies.yaml")
                self._new_enemy(defn, map_id, x, y)

    def _new_enemy(self, defn: EnemyDef, map_id: str, x: float, y: float, summoner: Enemy | None = None) -> Enemy:
        enemy = Enemy(id=self._new_id(), name=defn.name, x=x, y=y, defn=defn, home_x=x, home_y=y,
                      map_id=map_id, summoner_id=summoner.id if summoner else None)
        self._reset_enemy(enemy)
        self.enemies[enemy.id] = enemy
        return enemy

    def _reset_enemy(self, e: Enemy) -> None:
        """Nascer (ou renascer, ou um chefe desistir da luta): sorteia elite, vida cheia, fase inicial."""
        e.elite = e.summoner_id is None and self.rng.random() < e.defn.elite_chance
        e.half = e.body_half()
        e.phase = 0
        e.hp = e.max_hp
        e.state = "idle"
        e.target_id = None
        e.effects.clear()
        e.cooldowns.clear()
        e.calm_timer = 0.0
        self._despawn_summons(e)

    def _despawn_summons(self, boss: Enemy) -> None:
        for minion in [m for m in self.enemies.values() if m.summoner_id == boss.id]:
            del self.enemies[minion.id]

    # ================================================================ recarga de conteúdo

    def reload_from_disk(self) -> tuple[bool, str]:
        """Lê de novo conteúdo e mapas. Com erro, nada muda e devolve a mensagem do validador."""
        if self.reload_source is None:
            return False, "Este servidor não sabe de onde recarregar."
        try:
            content, maps = self.reload_source()
        except (ContentError, ValueError, OSError) as exc:
            return False, str(exc)
        notes = self.reload(content, maps)
        summary = (f"{len(content.skills)} skills, {len(content.items)} itens, {len(content.enemies)} inimigos, "
                   f"{len(content.classes)} classes, {len(maps)} mapas.")
        return True, summary + (" Ajustes: " + "; ".join(notes) if notes else "")

    def reload(self, content: Content, maps: dict[str, TileMap]) -> list[str]:
        """Troca conteúdo e mapas com o jogo rodando. Devolve os ajustes que precisou fazer.

        Inimigos renascem com os valores novos e projéteis em voo somem. Para quem
        está online: classe/skills, inventário, efeitos e recargas são relidos; o que
        não existe mais é descartado; quem ficou num mapa removido ou dentro de uma
        parede vai para o ponto de nascimento.
        """
        notes: list[str] = []
        self.content = content
        self.maps = maps
        if self.start_map_id not in maps:
            self.start_map_id = next(m for m in (C.START_MAP, next(iter(maps))) if m in maps)
        self.orgs.content = content
        self.admin.roles = content.roles
        self._spawn_enemies()
        self.events.reset()  # os invasores sumiram junto com os outros inimigos
        self.npcs.spawn()  # encerra as conversas abertas (as falas podem ter mudado)
        self.projectiles.clear()
        for drop in list(self.drops.values()):
            if drop.map_id not in maps or (drop.item and drop.item.id not in content.items):
                del self.drops[drop.id]
            elif drop.item:
                drop.item = content.items[drop.item.id]
        notes += self.orgs.revalidate()
        for p in self.players.values():
            self.quests.normalize(p)  # missões removidas saem; objetivos mudados são ajustados

        # primeiro as definições novas, depois os mapas (o cliente redesenha com elas)
        self._outbox.append((None, {"t": "content", **content.client_payload()}))
        for p in list(self.players.values()):
            p.staff_role = content.roles.get(p.staff_role.id) if p.staff_role else None
            p.inventory = Inventory.from_dict(p.inventory.to_dict(), content.items, max_size=C.BAG_MAX_SIZE)
            if p.bank is not None:
                p.bank = Inventory.from_dict(p.bank.to_dict(), content.items, size=C.BANK_SIZE,
                                             max_size=C.BANK_MAX_SIZE)
            old_class = p.class_id
            self._set_class(p, self._resolve_class({"race": p.race_id, "class": p.class_id}))
            if p.class_id != old_class:
                notes.append(f"{p.name}: classe {old_class} não existe mais, foi para {p.class_id}")
            for effect_id, active in list(p.effects.items()):
                if effect_id in content.effects:
                    active.defn = content.effects[effect_id]
                else:
                    del p.effects[effect_id]
            p.cooldowns = {k: v for k, v in p.cooldowns.items() if k in content.skills}
            p.item_cooldowns = {k: v for k, v in p.item_cooldowns.items() if k in content.items}
            tilemap = maps.get(p.map_id)
            map_id, x, y = p.map_id, p.x, p.y
            if tilemap is None:
                notes.append(f"{p.name}: o mapa {p.map_id} não existe mais")
                map_id, (x, y) = self.start_map_id, self.map.spawn
            elif tilemap.box_blocked(x, y, p.half, _player_blocks):
                x, y = tilemap.spawn
            self.teleport(p, map_id, x, y)  # manda o mapa (talvez alterado) para o cliente
        return notes

    @property
    def map(self) -> TileMap:
        """O mapa inicial (onde nascem personagens novos)."""
        return self.maps[self.start_map_id]

    def _map(self, e: Entity) -> TileMap:
        """O mapa onde a entidade está."""
        return self.maps.get(e.map_id) or self.map

    def _map_payload(self, tilemap: TileMap) -> dict:
        """Mapa para o cliente, com o nome do destino de cada portal e onde ficam os NPCs.

        Os NPCs não se movem: o minimapa mostra todos (com as marcas de missão ! e ?),
        mesmo os que estão longe demais para vir no snapshot.
        """
        info = tilemap.client_info()
        for portal in info["portals"]:
            dest = self.maps.get(portal["to"])
            portal["title"] = (dest.title or dest.name) if dest else portal["to"]
        info["npcs"] = [
            {"id": n.id, "t": n.defn.id if n.defn else "", "x": n.x, "y": n.y}
            for n in self.npcs.entities.values() if self.maps.get(n.map_id) is tilemap
        ]
        return info

    def _solids(self, e: Entity) -> list[Solid]:
        """Corpos que bloqueiam o movimento de `e` (config: PLAYERS_BLOCK_PLAYERS e cia.), perto dele."""
        is_player = isinstance(e, Player)
        groups: list = []
        if (C.PLAYERS_BLOCK_PLAYERS if is_player else C.PLAYERS_BLOCK_ENEMIES):
            groups.append(self.players.values())
        if (C.PLAYERS_BLOCK_ENEMIES if is_player else C.ENEMIES_BLOCK_ENEMIES):
            groups.append(self.enemies.values())
        if C.NPCS_BLOCK:
            groups.append(self.npcs.entities.values())
        solids = []
        for other in chain(*groups):
            if other is e or not other.alive or other.map_id != e.map_id:
                continue
            near = e.half + other.half + C.SOLID_SEARCH
            if abs(other.x - e.x) < near and abs(other.y - e.y) < near:
                solids.append((other.x, other.y, other.half))
        return solids

    def _fx_at(self, who: Entity, data: dict) -> None:
        """Efeito visual na posição de `who` (só vai para quem está perto, no mesmo mapa)."""
        self._fx.append((who.map_id, who.x, who.y, data))

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
        """Atalho em um passo (testes e ferramentas): conta e personagem com o mesmo nome.

        Cria a conta e o personagem se não existirem. O jogo usa o caminho de contas
        (server/accounts.py), que confere a senha fora do loop e depois chama enter().
        """
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return None, "O nome deve ter de 3 a 16 caracteres (letras, números ou _)."
        account = self.db.load_account(name)
        if account is None:
            if self.db.load_character(name) is not None:
                return None, "Esse nome já é usado por outro personagem."
            race_def = self.content.races.get(race) if isinstance(race, str) else None
            if race_def is None or not race_def.selectable:
                return None, "Escolha uma raça para criar o personagem."
            salt = os.urandom(16)
            account = self.db.create_account(name, hash_password(password, salt), salt)
        elif not self.db.verify_password(account, password):
            return None, "Senha incorreta."
        assert account is not None
        if self.db.load_character(name) is None:
            if (error := self.create_character(account.id, name, race)) is not None:
                return None, error
        return self.enter(account.id, name)

    def create_character(self, account_id: int, name: str, race: str | None) -> str | None:
        """Cria um personagem novo na conta. Devolve o erro, ou None se criou."""
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            return "O nome deve ter de 3 a 16 caracteres (letras, números ou _)."
        if self.db.load_character(name) is not None:
            return "Já existe um personagem com esse nome."
        if len(self.db.list_characters(account_id)) >= C.MAX_CHARACTERS:
            return f"Cada conta pode ter até {C.MAX_CHARACTERS} personagens."
        race_def = self.content.races.get(race) if isinstance(race, str) else None
        if race_def is None or not race_def.selectable:
            return "Escolha uma raça para criar o personagem."
        self.db.create_character(name, account_id=account_id,
                                 data={"race": race_def.id, "class": race_def.start_class, "new": True})
        return None

    def enter(self, account_id: int, name: str, ip: str = "") -> tuple[Player | None, str | None]:
        """Entra no mundo com um personagem da conta. Devolve (jogador, erro)."""
        record = self.db.load_character(name.strip())
        if record is None or record.account_id != account_id:
            return None, "Personagem não encontrado nesta conta."
        if any(p.name.lower() == record.name.lower() for p in self.players.values()):
            return None, "Esse personagem já está online."
        if any(p.account_id == account_id for p in self.players.values()):
            return None, "Outro personagem desta conta já está no jogo."
        if ban := self.admin.ban_message(record.name):
            return None, ban
        is_new = bool(record.data.get("new")) and "v" not in record.data
        if is_new:
            data: dict = {"race": record.data.get("race"), "class": record.data.get("class")}
        else:
            try:
                data = migrate(record.data)
            except SaveVersionError:
                log.exception("save incompatível: %s", record.name)
                return None, "Não foi possível carregar este personagem (save incompatível)."

        sx, sy = self.map.spawn
        player = Player(id=self._new_id(), name=record.name, x=sx, y=sy, map_id=self.start_map_id)
        player.account_id = account_id
        player.ip = ip
        # antes do load: a vida máxima depende da raça e do equipamento
        player.inventory = Inventory.from_dict(data.get("inventory") or {}, self.content.items,
                                               max_size=C.BAG_MAX_SIZE)
        self.training.load(player, data.get("skills"))  # conferidas com a classe logo abaixo
        self._set_class(player, self._resolve_class(data))
        player.load_dict(data)
        if is_new:
            player.hp = player.max_hp
            player.energy = player.max_energy
        saved_map = data.get("map")
        if saved_map in self.maps:
            player.map_id = saved_map
        elif saved_map:  # o mapa foi removido: volta para o início
            player.x, player.y = sx, sy
        tilemap = self._map(player)
        if tilemap.box_blocked(player.x, player.y, player.half, _player_blocks):
            player.x, player.y = tilemap.spawn

        player.bank = Inventory.from_dict(self.db.load_bank(account_id), self.content.items, size=C.BANK_SIZE,
                                          max_size=C.BANK_MAX_SIZE)
        player.maps_seen.add(player.map_id)  # destinos da viagem rápida
        self.admin.load_player(player)
        self.quests.normalize(player)
        self.players[player.id] = player
        self._system(f"{player.name} entrou no jogo.")
        if is_new:
            self._save(player)  # já fica no formato de save atual
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
        """Troca classe e, junto, a raça (a da classe).

        Aprende as skills iniciais da classe; as que a raça/classe nova não pode usar
        continuam conhecidas, mas saem da barra (server/training.py).
        """
        p.class_def = cls
        p.race_def = self.content.races[cls.race]
        self.training.on_class_change(p)
        for slot, item in list(p.inventory.equipment.items()):
            if not self._can_equip(p, item):  # a raça/classe nova não pode usar: volta para a bolsa
                _, wear = p.inventory.unequip(slot)  # type: ignore[misc]
                self._give_item(p, item, 1, wear)
                self._private(p, f"{item.name} não serve para {p.race_def.name}/{cls.name} e foi desequipado.")
        p.inventory.dirty = True
        self.orgs.on_race_change(p)
        p.hp = min(p.hp, p.max_hp)
        p.energy = min(p.energy, p.max_energy)

    def logout(self, player: Player) -> None:
        if player.id not in self.players:
            return
        self.duels.on_logout(player)  # antes de sair da lista: o duelo termina com ele presente
        self.npcs.on_logout(player)
        self.parties.on_logout(player)
        self.trades.on_logout(player)
        self.players.pop(player.id, None)
        self._save(player)
        self._system(f"{player.name} saiu do jogo.")

    def welcome_payload(self, player: Player) -> dict:
        return {
            "t": "welcome",
            "id": player.id,
            "tick_rate": C.TICK_RATE,
            "half": player.half,
            "map": self._map_payload(self._map(player)),
            "stat_labels": STAT_LABELS,
            "npc_range": C.NPC_TALK_RANGE,  # distância para conversar com NPCs (tecla E)
            "bar_size": C.BAR_SIZE,  # espaços da barra de atalhos
            # o que bloqueia o jogador (a previsão de movimento do cliente repete a colisão)
            "solid": {"player": C.PLAYERS_BLOCK_PLAYERS, "enemy": C.PLAYERS_BLOCK_ENEMIES, "npc": C.NPCS_BLOCK},
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
            if skill_id in player.bar and len(player.actions) < C.MAX_QUEUED_ACTIONS:
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
            if not text or not self._chat_allowed(player):
                return
            channel = data.get("ch")
            if text.startswith("/"):  # comandos valem em qualquer canal
                self._chat(player, text)
            elif channel == "org":
                self.orgs.chat(player, text)
            elif channel == "party":
                self.parties.chat(player, text)
            elif channel == "local":
                self.social.say(player, text)
            elif channel == "emote":
                self.social.emote(player, text)
            elif channel == "pm":
                self.social.whisper(player, data.get("to"), text)
            else:
                self._chat(player, text)
        elif kind == "duel":
            self.duels.handle_op(player, data)
        elif kind == "org":
            self.orgs.handle_op(player, data)
        elif kind == "admin":
            self.admin.handle(player, data)
        elif kind == "npc":
            self.npcs.handle_op(player, data)
        elif kind == "quest":
            self.quests.handle_op(player, data)
        elif kind == "skills":
            self.training.handle_op(player, data)
        elif kind == "party":
            self.parties.handle_op(player, data)
        elif kind == "bug":
            self.bugs.report(player, data)
        elif kind == "social":
            self.social.handle_op(player, data)
        elif kind == "trade":
            self.trades.handle_op(player, data)

    def _chat_allowed(self, p: Player) -> bool:
        """No máximo C.CHAT_BURST mensagens de chat a cada C.CHAT_WINDOW segundos."""
        times = p.chat_times
        while times and times[0] <= self.time - C.CHAT_WINDOW:
            times.popleft()
        if len(times) >= C.CHAT_BURST:
            self._private(p, "Calma: espere um pouco para mandar outra mensagem.")
            return False
        times.append(self.time)
        return True

    def save_all(self) -> None:
        for player in self.players.values():
            self._save(player)
        self.economy.flush()

    def _save(self, player: Player) -> None:
        self.save_players([player])

    def _save_data(self, player: Player) -> dict:
        data = player.to_dict()
        if not player.alive:  # quem sai morto volta no ponto de nascimento
            data["x"], data["y"] = self._map(player).spawn
            data["hp"] = player.max_hp
        return data

    def save_players(self, players: list[Player], trade: tuple | None = None) -> None:
        """Salva os personagens e os bancos das contas deles numa transação só.

        Banco e troca chamam isto na hora (não esperam o salvamento automático): um item
        que mudou de lugar fica salvo nos dois lados juntos, ou em nenhum.
        """
        self.db.save_together(
            [(p.name, self._save_data(p)) for p in players],
            [(p.account_id, p.bank.to_dict()) for p in players if p.bank is not None and p.account_id is not None],
            trade,
        )

    def drain_outbox(self) -> list[Outgoing]:
        out, self._outbox = self._outbox, []
        return out

    # ================================================================ mensagens

    def _system(self, text: str) -> None:
        self._outbox.append((None, {"t": "sys", "text": text}))

    def _private(self, player: Player, text: str) -> None:
        self._outbox.append((player.id, {"t": "sys", "text": text}))

    def _chat(self, player: Player, text: str) -> None:
        if not text.startswith("/") and (muted := self.admin.mute_message(player)):
            self._private(player, muted)
            return
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
            elif command == "/grupo":
                if args:
                    self.parties.invite(player, args[0])
                else:
                    self._private(player, "Use: /grupo Nome (ou clique no jogador)")
            elif command == "/g":
                self.parties.chat(player, text[len(command):])
            elif command == "/me":
                self.social.emote(player, text[len(command):])
            elif command == "/l":
                self.social.say(player, text[len(command):])
            elif command == "/p":
                if args:
                    self.social.whisper(player, args[0], text.split(None, 2)[2] if len(args) > 1 else "")
                else:
                    self._private(player, "Use: /p Nome mensagem (ou clique no jogador)")
            elif command == "/r":
                self.social.reply(player, text[len(command):])
            elif command == "/ignorar":
                if args:
                    self.social.ignore(player, args[0])
                else:
                    self._private(player, "Use: /ignorar Nome (ou clique no jogador)")
            elif command == "/designorar":
                self.social.unignore(player, args[0] if args else "")
            elif command == "/troca":
                if args:
                    self.trades.invite(player, args[0])
                else:
                    self._private(player, "Use: /troca Nome (ou clique no jogador)")
            else:
                self._private(player, f"Comando desconhecido: {command}. Disponíveis: /who, /me, /l, /p Nome, /r, "
                                      "/ignorar Nome, /designorar Nome, /troca Nome, /duelo Nome, "
                                      "/aceitar, /recusar, /render, /grupo Nome, /g, /org, /entrar, /sair, /o, /assumir, "
                                      "/disputar, /promover, /rebaixar, /expulsar")
            return
        # canal Geral: todos os online, menos quem ignora quem falou
        self.social.deliver(player, self.players.values(), {"t": "chat", "from": player.name, "text": text})

    # ================================================================ tick

    def step(self, dt: float) -> None:
        self.time += dt
        for player in list(self.players.values()):
            self._update_player(player, dt)
        self.duels.update()
        self.parties.update()
        self.trades.update()
        self.events.update()
        self.npcs.update()
        self.quests.update()
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
        # Cada coisa vira (mapa, x, y, dados) uma vez; depois cada jogador recebe só o que
        # está no mesmo mapa e dentro de VIEW_RADIUS.
        ents = []
        for e in chain(self.players.values(), self.enemies.values(), self.npcs.entities.values()):
            snap = e.snapshot()
            if isinstance(e, Player) and (opponent := self.duels.opponent_id(e)) is not None:
                snap["du"] = opponent  # em duelo com
            if isinstance(e, Player) and (org := self.orgs.snapshot(e)):
                snap["o"] = org  # [organização, cargo, vaga]
            if isinstance(e, Player) and e.staff_role:
                snap["st"] = e.staff_role.id  # selo da equipe
            ents.append((e.map_id, e.x, e.y, snap))
        all_projs = [(p.map_id, p.x, p.y, p.snapshot()) for p in self.projectiles.values()]
        all_drops = [(d.map_id, d.x, d.y, d.snapshot()) for d in self.drops.values()]
        all_fx, self._fx = self._fx, []
        radius2 = C.VIEW_RADIUS ** 2

        for player in self.players.values():
            map_id, px, py = player.map_id, player.x, player.y

            def near(items: list) -> list:
                return [data for m, x, y, data in items if m == map_id and (x - px) ** 2 + (y - py) ** 2 <= radius2]

            you = player.private_snapshot()
            tilemap = self._map(player)
            zone = tilemap.zone_at(px, py)
            you["zone"] = {"t": zone.type, "n": zone.name, "m": tilemap.title or tilemap.name}
            you["duel"] = self.duels.client_info(player)
            you["invite"] = self.duels.invite_for(player)  # desafio esperando resposta
            you["pinv"] = self.parties.invite_for(player)  # convite de grupo esperando resposta
            you["tinv"] = self.trades.invite_for(player)  # pedido de troca esperando resposta
            changed, events = self.events.snapshot_for(player)
            if changed:  # eventos em ondas em andamento (lista vazia = nenhum)
                you["ev"] = events
            changed, trade = self.trades.snapshot_for(player)
            if changed:  # a troca só vai quando muda (null = acabou)
                you["trade"] = trade
            changed, party = self.parties.snapshot_for(player)
            if changed:  # o grupo só vai quando muda (null = saiu ou acabou)
                you["party"] = party
            if mates := self.parties.mates_on_map(player):  # minimapa: o grupo mesmo longe
                you["mates"] = mates
            changed, ignored = self.social.snapshot_for(player)
            if changed:  # lista de ignorados também
                you["ign"] = ignored
            you["staff"] = player.staff_role.id if player.staff_role else None
            if player.inventory.dirty:  # inventário só vai quando muda
                you["inv"] = player.inventory.client_info()
                player.inventory.dirty = False
            if (quests := self.quests.snapshot_for(player)) is not None:  # diário também
                you["q"] = quests
            if (known := self.training.snapshot_for(player)) is not None:  # skills conhecidas também
                you["known"] = known
            self._outbox.append((player.id, {
                "t": "snap", "ents": near(ents), "projs": near(all_projs), "drops": near(all_drops),
                "fx": near(all_fx), "you": you,
            }))

    # ================================================================ jogadores

    def _update_player(self, p: Player, dt: float) -> None:
        if not p.alive:
            p.dead_timer -= dt
            p.actions.clear()
            if p.move_queue:  # descarta comandos, mas confirma para o cliente não ficar esperando
                p.ack = p.move_queue[-1][0]
                p.move_queue.clear()
            if p.dead_timer <= 0:
                p.x, p.y = self._map(p).spawn
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
        self._check_portal(p)

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
        p.x, p.y = self._map(p).move_box(p.x, p.y, p.half, dx * scale, dy * scale, _player_blocks, self._solids(p))
        distance = math.hypot(p.x - old_x, p.y - old_y)
        p.state = "walk" if distance > 0 else "idle"
        if distance > 0:
            self._train(p, "agility", distance * C.AGILITY_XP_PER_PX)
        return distance > 0

    def _check_portal(self, p: Player) -> None:
        portal = self._map(p).portal_at(p.x, p.y)
        if portal is None:
            p.portal_lock = False  # saiu de cima do portal: o próximo vale
        elif not p.portal_lock:
            self.teleport(p, portal.to, *self.maps[portal.to].tile_center(*portal.at))

    def teleport(self, p: Player, map_id: str, x: float, y: float) -> None:
        """Leva o jogador para outro lugar (talvez outro mapa) e avisa o cliente."""
        changed = map_id != p.map_id
        p.map_id, p.x, p.y = map_id, x, y
        p.portal_lock = True  # chegou em cima de algo? só usa portal de novo depois de sair
        if p.state in ("meditate", "rest", "walk"):
            p.state = "idle"
        if p.move_queue:  # comandos de antes da viagem não valem no lugar novo
            p.ack = p.move_queue[-1][0]
            p.move_queue.clear()
        p.maps_seen.add(map_id)  # destino da viagem rápida
        tilemap = self._map(p)
        self._outbox.append((p.id, {"t": "map", "map": self._map_payload(tilemap), "x": x, "y": y}))
        if changed:
            self._private(p, f"Você chegou a {tilemap.title or tilemap.name}.")

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
            self._fx_at(p, {"k": "lvl", "id": p.id, "x": round(p.x), "y": round(p.y)})
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
        elif name == "lose_gold":
            gold = p.inventory.gold
            lost = int(gold * args["percent"] / 100) if "percent" in args else min(gold, args["amount"])
            p.inventory.add_gold(-lost)
            self.economy.record("regra", "perda", lost)
        elif name == "start_quest":
            self.quests.start(p, args)
        elif name == "learn_skill":
            self.training.learn(p, args)

    def _player_context(self, p: Player) -> dict:
        """O que as condições de diálogos e missões podem consultar sobre o personagem."""
        membership = self.orgs.of(p)
        return {
            "race": p.race_id,
            "class": p.class_id,
            "org": membership.org if membership else None,
            "rank": membership.rank if membership else None,
            "gold": p.inventory.gold,
            "total_level": total_level(p),
            "quest_done": set(p.quests_done),
            "quest_active": set(p.quests),
        }

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
            if skill.type in ("melee", "projectile", "area"):  # atacar trava a barra por um tempo
                self.training.mark_combat(caster)
            if skill.train:
                caster.last_stat = next(iter(skill.train))
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
            self._fx_at(caster, {"k": "buff", "id": caster.id, "c": skill.color})
            if isinstance(caster, Player):
                self._train_from_skill(caster, skill)
        return True

    def _skill_damage(self, caster: Entity, skill: SkillDef) -> float:
        raw = skill.damage_base + sum(scale * caster.stat_level(stat) for stat, scale in skill.damage_scale.items())
        return raw * caster.modifier("damage") * caster.power

    def _train_from_skill(self, p: Player, skill: SkillDef) -> None:
        for stat, xp in skill.train.items():
            self._train(p, stat, xp)

    def _living_targets(self, exclude: Entity | None, map_id: str) -> Iterator[Entity]:
        for e in chain(self.players.values(), self.enemies.values()):
            if e.alive and e is not exclude and e.map_id == map_id:
                yield e

    def _can_hit(self, attacker: Entity, target: Entity) -> bool:
        """Quem pode ferir quem.

        - jogador × jogador: em duelo, só entre os dois (em qualquer zona); fora de
          duelo, só se os dois estiverem numa zona PvP;
        - inimigo × jogador: em qualquer lugar menos na zona segura;
        - jogador × inimigo: sempre; inimigo × inimigo: nunca.
        """
        if not target.alive or attacker is target or attacker.map_id != target.map_id:
            return False
        if isinstance(attacker, Enemy) and isinstance(target, Enemy):
            return False
        if isinstance(attacker, Player) and isinstance(target, Player):
            if self.duels.opponent_id(attacker) == target.id:
                return self.duels.fighting(attacker, target)  # não vale durante a contagem
            if self.duels.duel_of(attacker) or self.duels.duel_of(target):
                return False  # ninguém de fora entra (nem sai) de um duelo
            if not C.PARTY_FRIENDLY_FIRE and self.parties.same_party(attacker, target):
                return False
            tilemap = self._map(attacker)
            return (tilemap.zone_at(attacker.x, attacker.y).type == "pvp"
                    and tilemap.zone_at(target.x, target.y).type == "pvp")
        if isinstance(target, Player):
            return not self._map(target).is_safe(target.x, target.y)
        return True

    @staticmethod
    def _bulk(e: Entity) -> float:
        """Quanto o corpo é maior que o normal: golpes e áreas partem da borda, não do centro."""
        return e.half - C.ENTITY_HALF

    def _cast_melee(self, caster: Entity, skill: SkillDef) -> None:
        fdx, fdy = DIRECTIONS[caster.facing]
        reach = skill.reach + self._bulk(caster)
        cx, cy = caster.x + fdx * reach, caster.y + fdy * reach
        self._fx_at(caster, {"k": "swing", "id": caster.id, "r": reach + skill.size})
        damage = self._skill_damage(caster, skill)
        for target in list(self._living_targets(exclude=caster, map_id=caster.map_id)):
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
                map_id=caster.map_id,
            )
            self.projectiles[proj.id] = proj

    def _cast_area(self, caster: Entity, skill: SkillDef) -> None:
        radius = skill.radius + self._bulk(caster)
        self._fx_at(caster, {"k": "area", "id": caster.id, "r": radius, "c": skill.color})
        damage = self._skill_damage(caster, skill)
        for target in list(self._living_targets(exclude=caster, map_id=caster.map_id)):
            reach = radius + target.half
            if math.hypot(target.x - caster.x, target.y - caster.y) <= reach and self._can_hit(caster, target):
                self._skill_hit(caster, target, skill, damage)

    def _cast_dash(self, caster: Entity, skill: SkillDef) -> None:
        blocks = _player_blocks if isinstance(caster, Player) else _enemy_blocks
        fdx, fdy = DIRECTIONS[caster.facing]
        start = (caster.x, caster.y)
        remaining = skill.distance
        solids = self._solids(caster)
        while remaining > 0:
            step = min(C.DASH_STEP, remaining)
            nx, ny = self._map(caster).move_box(caster.x, caster.y, caster.half, fdx * step, fdy * step, blocks,
                                                solids)
            if (nx, ny) == (caster.x, caster.y):
                break  # bateu numa parede ou num corpo
            caster.x, caster.y = nx, ny
            remaining -= step
        if caster.state in ("meditate", "rest"):
            caster.state = "idle"
        self._fx_at(caster, {"k": "dash", "id": caster.id, "x0": round(start[0]), "y0": round(start[1]),
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
            tilemap = self.maps.get(proj.map_id) or self.map
            if owner is None or proj.ttl <= 0 or _shot_blocks(tilemap.tile_at_px(proj.x, proj.y)):
                del self.projectiles[proj.id]
                continue
            skill = self.content.skills[proj.skill_id]
            for target in list(self._living_targets(exclude=owner, map_id=proj.map_id)):
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
        self.upkeep.on_hit(target, source, skill, effect_id)  # desgaste do equipamento
        self._fx_at(target, {"k": "dmg", "id": target.id, "x": round(target.x), "y": round(target.y), "v": amount})
        if isinstance(target, Player):
            self.training.mark_combat(target)
            if target.state in ("meditate", "rest"):
                target.state = "idle"
            self._train(target, "vitality", lost * C.VITALITY_XP_PER_DAMAGE)
        elif isinstance(target, Enemy) and isinstance(source, Player):
            target.target_id = source.id
            if target.event_id:  # invasor: conta como participação no evento
                self.events.on_damage(source, target, lost)
        if duel_defeat:
            self.duels.finish(self.duels.duel_of(target), loser=target, reason="defeat")  # type: ignore[arg-type]
        elif target.hp <= 0:
            self._kill(target, source, skill, effect_id)
        elif isinstance(target, Enemy) and target.defn.phases:
            self._check_boss_phase(target)

    def _kill(
        self, target: Entity, killer: Entity | None, skill: SkillDef | None, effect_id: str | None = None
    ) -> None:
        zone = self._map(target).zone_at(target.x, target.y).type
        effects_at_death = sorted(target.effects)
        was_dueling = isinstance(target, Player) and self.duels.on_death(target)
        target.hp = 0
        target.state = "dead"
        target.effects.clear()
        target.cooldowns.clear()

        if isinstance(target, Player):
            target.dead_timer = C.RESPAWN_SECONDS
            self.upkeep.on_death(target)  # o equipamento se desgasta
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
            target.dead_timer = (C.SUMMON_CORPSE_SECONDS if target.summoner_id or target.event_id
                                 else target.defn.respawn)
            self._spawn_loot(target, killer)
            # Bônus de abate vai para o atributo principal (o primeiro de "train") da skill usada
            reward = target.defn.kill_xp * (C.ELITE_REWARD if target.elite else 1.0)
            if isinstance(killer, Player) and skill and skill.train:
                self._train(killer, next(iter(skill.train)), reward)
            if isinstance(killer, Player):
                # grupo: quem está perto e lutou nos últimos segundos ganha parte do bônus
                for member in self.parties.near(killer, target.x, target.y):
                    if member.combat_until > self.time:
                        self._train(member, member.last_stat or "strength", reward * C.PARTY_XP_SHARE)
            if target.defn.boss:
                self._despawn_summons(target)
                who = f" por {killer.name}" if isinstance(killer, Player) else ""
                self._system(f"{target.name} foi derrotado{who}!")
            if isinstance(killer, Player):
                self.quests.on_kill(killer, target.defn.id)
                for member in self.parties.near(killer, target.x, target.y):  # o abate conta para o grupo
                    self.quests.on_kill(member, target.defn.id)

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
                            self._fx_at(e, {"k": "heal", "id": e.id, "x": round(e.x), "y": round(e.y), "v": round(healed)})
                    # Na zona segura, efeitos não ferem jogadores (a não ser vindos do oponente de duelo)
                    source = self._entity(active.source_id)
                    protected = (
                        isinstance(e, Player)
                        and self._map(e).is_safe(e.x, e.y)
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
            if inv.add(item, 1, inv.equip_wear.get(slot, 0)):  # type: ignore[arg-type]
                self._private(p, "Inventário cheio.")
                return
            inv.unequip(slot)  # type: ignore[arg-type]
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
                if self._map(p).box_blocked(x, y, 4, _player_blocks):
                    x, y = p.x, p.y
                drop = self._drop(taken.item, taken.qty, p.map_id, x, y, owner=None, scatter=False,
                                  wear=taken.wear)
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
                self._fx_at(p, {"k": "heal", "id": p.id, "x": round(p.x), "y": round(p.y), "v": round(healed)})
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
        taken = inv.take(slot, 1)
        assert taken is not None
        previous = inv.unequip(item.slot)  # type: ignore[arg-type]
        inv.equip(item.slot, item, taken.wear)  # type: ignore[arg-type]
        if previous:
            inv.add(previous[0], 1, previous[1])  # sempre cabe: o espaço do item novo acabou de vagar
        self._clamp_resources(p)

    @staticmethod
    def _clamp_resources(p: Player) -> None:
        p.hp = min(p.hp, p.max_hp)
        p.energy = min(p.energy, p.max_energy)

    def _give_item(self, p: Player, item: ItemDef, qty: int, wear: int = 0) -> None:
        """Põe no inventário; o que não couber cai no chão aos pés dele (só ele pega)."""
        leftover = p.inventory.add(item, qty, wear)
        if leftover:
            self._drop(item, leftover, p.map_id, p.x, p.y, owner=p.id, scatter=False, wear=wear)
            self._private(p, "Inventário cheio: o que não coube ficou no chão.")

    def _drop(self, item: ItemDef, qty: int, map_id: str, x: float, y: float, owner: int | None,
              scatter: bool = True, wear: int = 0) -> GroundItem:
        if scatter:
            angle = self.rng.uniform(0, 2 * math.pi)
            dist = self.rng.uniform(0, C.LOOT_SCATTER)
            nx, ny = x + math.cos(angle) * dist, y + math.sin(angle) * dist
            if not (self.maps.get(map_id) or self.map).box_blocked(nx, ny, 4, _player_blocks):
                x, y = nx, ny
        drop = GroundItem(
            id=self._new_id(), item=item, qty=qty, x=x, y=y, owner_id=owner,
            owner_until=self.time + C.LOOT_OWNER_SECONDS, expires_at=self.time + C.LOOT_EXPIRE_SECONDS,
            map_id=map_id, wear=wear,
        )
        self.drops[drop.id] = drop
        return drop

    def _spawn_loot(self, enemy: Enemy, killer: Entity | None) -> None:
        owner = killer.id if isinstance(killer, Player) else None
        party = self.parties.of(killer) if isinstance(killer, Player) else None
        drops = []  # só itens: moedas entram no jogo vendendo itens para lojas de NPC
        for _ in range(C.ELITE_LOOT_ROLLS if enemy.elite else 1):
            for entry in enemy.defn.loot:
                if self.rng.random() < entry.chance:
                    qty = self.rng.randint(entry.qty_min, entry.qty_max)
                    drops.append(self._drop(self.content.items[entry.item], qty, enemy.map_id, enemy.x, enemy.y,
                                            owner))
        for drop in drops:
            drop.party_id = party.id if party else None

    def _pickup(self, p: Player) -> None:
        for drop in list(self.drops.values()):
            if drop.map_id != p.map_id or math.hypot(drop.x - p.x, drop.y - p.y) > C.PICKUP_RADIUS:
                continue
            party = self.parties.of(p)
            same_party = party is not None and drop.party_id == party.id
            if drop.owner_id not in (None, p.id) and self.time < drop.owner_until and not same_party:
                continue  # ainda é de quem matou (ou do grupo dele)
            if drop.ignore_id == p.id and self.time < drop.ignore_until:
                continue  # acabou de largar
            taken = drop.qty - p.inventory.add(drop.item, drop.qty, drop.wear)
            label = f"+{taken} {drop.item.name}"
            if taken == 0:
                continue  # inventário cheio
            drop.qty -= taken
            if drop.qty == 0:
                del self.drops[drop.id]
            self._fx_at(p, {"k": "pick", "id": p.id, "x": round(p.x), "y": round(p.y), "t": label,
                             "r": drop.item.rarity})

    def _update_drops(self) -> None:
        for drop in list(self.drops.values()):
            if self.time >= drop.expires_at:
                del self.drops[drop.id]

    # ================================================================ inimigos

    def _update_enemy(self, e: Enemy, dt: float) -> None:
        if not e.alive:
            e.dead_timer -= dt
            if e.dead_timer <= 0:
                if e.summoner_id is not None or e.event_id is not None:  # invocado ou invasor: não renasce
                    self.enemies.pop(e.id, None)
                    return
                e.x, e.y = e.home_x, e.home_y
                self._reset_enemy(e)
            return

        self._tick_cooldowns(e, dt)
        self._update_effects(e, dt)
        if not e.alive:
            return
        if e.ignore_timer > 0:
            e.ignore_timer -= dt
        if e.has_flag("stunned"):
            e.state = "idle"
            return

        target = self._enemy_target(e)
        if target is None:
            e.hp = min(e.max_hp, e.hp + C.ENEMY_HP_REGEN * dt)
            if e.defn.boss and (e.phase or e.hp < e.max_hp):
                e.calm_timer += dt  # ex.: quem lutava morreu ou fugiu
                if e.calm_timer >= C.BOSS_RESET_SECONDS:
                    self._reset_enemy(e)
                    self._near_boss(e, f"{e.name} recua e se recompõe.")
            self._enemy_wander(e, dt)
            return
        e.calm_timer = 0.0

        dx, dy = target.x - e.x, target.y - e.y
        dist = math.hypot(dx, dy)
        _face(e, dx, dy)

        skills = [self.content.skills[sid] for sid in e.skills]
        # corpos grandes (dele ou do alvo) aumentam o alcance de golpes e áreas (contados da borda)
        extra = self._bulk(e) + self._bulk(target)

        def ai_range(s: SkillDef) -> float:
            return s.ai_range + (extra if s.type in ("melee", "area") else 0.0)

        # projéteis só saem com a linha de tiro livre (senão batem na parede)
        ranged = any(s.type == "projectile" for s in skills)
        line_of_fire = not ranged or clear_line(self._map(e), e.x, e.y, target.x, target.y, 2.0, _shot_blocks)

        for skill in skills:
            if skill.type == "projectile" and not line_of_fire:
                continue
            if e.cooldowns.get(skill.id, 0) <= 0 and dist <= ai_range(skill) and self._can_hit(e, target):
                if self._use_skill(e, skill):
                    break

        # Aproxima até o alcance da skill de menor alcance (quem tem golpe chega perto);
        # quem atira continua andando enquanto não tiver linha de tiro
        attack_ranges = [ai_range(s) for s in skills if s.type != "self"]
        keep_distance = min(attack_ranges) if attack_ranges else 0.0
        if (dist > keep_distance or not line_of_fire) and dist > 0:
            if not self._enemy_move_to(e, target.x, target.y, dt):
                e.ignore_id, e.ignore_timer = target.id, C.PATH_GIVE_UP_SECONDS  # sem caminho: desiste
                e.target_id = None
                e.state = "idle"
        else:
            e.state = "idle"

    def _enemy_move_to(self, e: Enemy, x: float, y: float, dt: float, speed_scale: float = 1.0) -> bool:
        """Anda em direção a (x, y): reto se a linha estiver livre; senão pelo caminho do A*.

        Devolve False se não existe caminho (dentro do limite de busca).
        """
        tilemap = self._map(e)
        tx, ty = x, y
        if clear_line(tilemap, e.x, e.y, x, y, e.half, _enemy_blocks):
            e.path = []
        else:
            goal = tile_of(tilemap, x, y)
            e.path_timer -= dt
            if not e.path or goal != e.path_goal or e.path_timer <= 0:
                e.path_goal, e.path_timer = goal, C.PATH_REPLAN_SECONDS
                tiles = find_path(tilemap, tile_of(tilemap, e.x, e.y), goal, min(e.half, C.PATH_MAX_HALF),
                                  _enemy_blocks, C.PATH_MAX_NODES)
                if tiles is None:
                    e.path = []
                    return False
                e.path = [free_spot(tilemap, *tilemap.tile_center(*t), e.half, _enemy_blocks)
                          for t in tiles[:-1]] + [(x, y)]
            while e.path and math.hypot(e.path[0][0] - e.x, e.path[0][1] - e.y) < C.WAYPOINT_REACHED:
                e.path.pop(0)
            if len(e.path) > 1 and clear_line(tilemap, e.x, e.y, *e.path[1], e.half, _enemy_blocks):
                e.path.pop(0)  # dá para cortar caminho até o ponto seguinte
            if e.path:
                tx, ty = e.path[0]
        dx, dy = tx - e.x, ty - e.y
        dist = math.hypot(dx, dy)
        if dist > 0:
            self._enemy_chase_step(e, dx / dist, dy / dist, dt, speed_scale)
            _face(e, dx, dy)
        return True

    def _enemy_chase_step(self, e: Enemy, ux: float, uy: float, dt: float, speed_scale: float = 1.0) -> None:
        """Um passo numa direção; se travar (num corpo, ou numa quina), contorna pelo lado por um tempo.

        O caminho em volta de paredes vem do A* (_enemy_move_to); isto resolve o que sobra.
        """
        old = (e.x, e.y)
        step = e.speed * speed_scale * dt
        solids = self._solids(e)

        def sidestep(side: int) -> bool:  # perpendicular à direção do alvo
            e.x, e.y = self._map(e).move_box(e.x, e.y, e.half, -uy * side * step, ux * side * step, _enemy_blocks,
                                             solids)
            return (e.x, e.y) != old

        if e.detour_timer > 0:
            e.detour_timer -= dt
            if not sidestep(e.detour_side):
                e.detour_side = -e.detour_side
                e.detour_timer = 0.0
        else:
            e.x, e.y = self._map(e).move_box(e.x, e.y, e.half, ux * step, uy * step, _enemy_blocks, solids)
            if math.hypot(e.x - old[0], e.y - old[1]) < step * 0.25 and ux and uy:
                # travou num eixo (andou quase nada): um passo inteiro no outro, para o lado do alvo,
                # para desencostar da quina (senão chega cada vez mais devagar e nunca passa)
                minor = (0.0, math.copysign(step, uy)) if abs(ux) >= abs(uy) else (math.copysign(step, ux), 0.0)
                e.x, e.y = self._map(e).move_box(e.x, e.y, e.half, *minor, _enemy_blocks, solids)
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
            if p is None or (p.id == e.ignore_id and e.ignore_timer > 0):  # sem caminho até ele, por ora
                return False
            return p.alive and p.map_id == e.map_id and not self._map(p).is_safe(p.x, p.y)

        # invasores de eventos não desistem por distância: a "casa" deles é para onde marcham
        if e.event_id is None and math.hypot(e.x - e.home_x, e.y - e.home_y) > e.defn.leash:
            if e.defn.boss and (e.phase or e.hp < e.max_hp):
                self._reset_enemy(e)  # chefe desistiu: volta inteiro, sem invocados, para a primeira fase
                self._near_boss(e, f"{e.name} recua e se recompõe.")
            e.target_id = None
            return None
        current = self.players.get(e.target_id) if e.target_id is not None else None
        if valid(current):
            return current
        e.target_id = None
        best, best_dist = None, e.defn.aggro_radius * (C.EVENT_AGGRO_MULT if e.event_id else 1.0)
        for p in self.players.values():
            if valid(p):
                d = math.hypot(p.x - e.x, p.y - e.y)
                if d <= best_dist:
                    best, best_dist = p, d
        if best is not None:
            e.target_id = best.id
        return best

    def _check_boss_phase(self, boss: Enemy) -> None:
        """A vida do chefe caiu: começa a(s) fase(s) cujo limite foi cruzado."""
        phases = boss.defn.phases
        while boss.phase < len(phases) and boss.hp <= phases[boss.phase].hp_below * boss.max_hp:
            phase = phases[boss.phase]
            boss.phase += 1
            if phase.message:
                self._near_boss(boss, phase.message)
            for app in phase.effects:
                self._apply_effect(boss, app, boss)
            self._fx_at(boss, {"k": "area", "id": boss.id, "r": 90, "c": "#ff5a5a"})
            index = 0
            for enemy_id, count in phase.summon:
                for _ in range(count):
                    self._summon(boss, self.content.enemies[enemy_id], index)
                    index += 1

    def _summon(self, boss: Enemy, defn: EnemyDef, index: int) -> None:
        """Invocado em volta do chefe (num ponto livre), já atrás do alvo do chefe."""
        tilemap = self._map(boss)
        x, y = boss.x, boss.y
        for attempt in range(8):
            angle = (index * 2.4 + attempt * 0.8) % (2 * math.pi)
            nx = boss.x + math.cos(angle) * C.SUMMON_SPREAD
            ny = boss.y + math.sin(angle) * C.SUMMON_SPREAD
            if not tilemap.box_blocked(nx, ny, C.ENTITY_HALF * defn.size, _enemy_blocks):
                x, y = nx, ny
                break
        minion = self._new_enemy(defn, boss.map_id, x, y, summoner=boss)
        minion.target_id = boss.target_id

    def _near_boss(self, boss: Enemy, text: str) -> None:
        """Aviso para quem está perto do chefe (no mesmo mapa)."""
        for p in self.players.values():
            if p.map_id == boss.map_id and math.hypot(p.x - boss.x, p.y - boss.y) <= C.BOSS_NEARBY:
                self._private(p, text)

    def _enemy_wander(self, e: Enemy, dt: float) -> None:
        if math.hypot(e.home_x - e.x, e.home_y - e.y) > e.defn.wander_radius + C.TILE_SIZE:
            # longe de casa (ex.: voltou de uma perseguição): volta pelo caminho, sem pressa
            if not self._enemy_move_to(e, e.home_x, e.home_y, dt, 0.5):
                e.state = "idle"
            return
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
        e.x, e.y = self._map(e).move_box(e.x, e.y, e.half, wx * step, wy * step, _enemy_blocks, self._solids(e))
        _face(e, wx, wy)
        e.state = "walk" if (e.x, e.y) != old else "idle"
