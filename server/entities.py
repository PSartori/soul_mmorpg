"""Entidades do mundo: jogadores, inimigos, NPCs e projéteis."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from . import config as C
from .content import ClassDef, EffectDef, EnemyDef, ItemDef, NpcDef, RaceDef, RoleDef
from .inventory import Inventory
from .saves import SAVE_VERSION
from .stats import STAT_NAMES, Stat

DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
MOVE_KEYS = ("up", "down", "left", "right")

MoveKeys = tuple[bool, bool, bool, bool]  # up, down, left, right


@dataclass(eq=False)
class ActiveEffect:
    defn: EffectDef
    remaining: float
    tick_timer: float
    source_id: int | None


@dataclass(eq=False)
class Entity:
    id: int
    name: str
    x: float
    y: float
    hp: float = 1.0
    facing: str = "down"
    state: str = "idle"  # idle | walk | meditate | rest | dead
    dead_timer: float = 0.0
    half: float = C.ENTITY_HALF
    map_id: str = ""  # em qual mapa está (nome do arquivo em data/maps/)
    cooldowns: dict[str, float] = field(default_factory=dict)  # skill -> segundos restantes
    effects: dict[str, ActiveEffect] = field(default_factory=dict)  # efeito -> estado

    kind: ClassVar[str] = "?"

    @property
    def alive(self) -> bool:
        return self.state != "dead"

    @property
    def max_hp(self) -> float:
        raise NotImplementedError

    @property
    def base_speed(self) -> float:
        raise NotImplementedError

    def stat_level(self, stat: str) -> int:
        """Inimigos não têm atributos: o dano das skills deles é só o 'base'."""
        return 0

    @property
    def power(self) -> float:
        """Multiplicador do dano das skills além dos efeitos (inimigos: damage, elite, fase)."""
        return 1.0

    # --- efeitos de status ---
    def modifier(self, name: str) -> float:
        result = 1.0
        for active in self.effects.values():
            result *= active.defn.modifiers.get(name, 1.0)
        return result

    def has_flag(self, flag: str) -> bool:
        return any(flag in active.defn.flags for active in self.effects.values())

    @property
    def speed(self) -> float:
        if self.has_flag("stunned"):
            return 0.0
        return self.base_speed * self.modifier("speed")

    def snapshot(self) -> dict:
        """O que todo mundo vê desta entidade."""
        return {
            "id": self.id,
            "k": self.kind,
            "n": self.name,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "f": self.facing,
            "s": self.state,
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
            "ef": sorted(self.effects),
        }


@dataclass(eq=False)
class Player(Entity):
    energy: float = 0.0
    fatigue: float = 0.0
    stats: dict[str, Stat] = field(default_factory=lambda: {n: Stat() for n in STAT_NAMES})
    race_def: RaceDef | None = None
    class_def: ClassDef | None = None
    flags: set[str] = field(default_factory=set)  # marcas de progressão (data/rules.yaml)
    quests: dict[str, list[int]] = field(default_factory=dict)  # missões em andamento -> progresso por objetivo
    quests_done: dict[str, list] = field(default_factory=dict)  # missões concluídas -> [vezes, horário da última]
    quests_ready: set[str] = field(default_factory=set)  # já avisou que está pronta (não vai para o save)
    quests_sent: str = ""  # último diário enviado ao cliente (só manda quando muda)
    inventory: Inventory = field(default_factory=Inventory)
    item_cooldowns: dict[str, float] = field(default_factory=dict)  # item -> segundos restantes
    portal_lock: bool = False  # acabou de atravessar um portal: só usa outro depois de sair dele
    staff_role: RoleDef | None = None  # papel na equipe (data/roles.yaml), vindo do banco
    muted: bool = False  # silenciado no chat
    muted_until: float | None = None  # até quando (horário real; None = para sempre)
    known: list[str] = field(default_factory=list)  # skills que o personagem sabe, na ordem em que aprendeu
    bar: list[str | None] = field(default_factory=list)  # barra de atalhos (C.BAR_SIZE espaços; None = vazio)
    skills_sent: str = ""  # últimas skills conhecidas enviadas ao cliente (só manda quando muda)
    combat_until: float = 0.0  # horário do mundo até quando conta como "em combate" (trava a barra)
    last_stat: str | None = None  # atributo principal da última skill usada (bônus de abate do grupo)
    account_id: int | None = None  # conta dona do personagem
    ip: str = ""  # de onde está conectado (bans por IP)
    chat_times: deque[float] = field(default_factory=deque)  # horários das últimas mensagens de chat
    party_sent: str = ""  # último estado do grupo enviado ao cliente (só manda quando muda)
    profile: str = ""  # descrição do personagem (perfil), escrita pelo jogador
    ignored: list[str] = field(default_factory=list)  # personagens que ele ignora (nomes)
    social_sent: str = ""  # última lista de ignorados enviada ao cliente (só manda quando muda)
    last_pm: str | None = None  # com quem trocou a última mensagem privada (/r responde)
    bank: Inventory | None = None  # banco da conta (carregado ao entrar; salvo junto com o personagem)
    trade_sent: str = ""  # último estado da troca enviado ao cliente (só manda quando muda)
    events_sent: str = ""  # últimos eventos em ondas enviados ao cliente (só manda quando muda)
    actions: list[tuple] = field(default_factory=list)  # ações pedidas, executadas no próximo tick
    # Movimento: o cliente manda um comando numerado por tick; o servidor executa um por tick
    # e devolve o número do último executado ("ack") para o cliente corrigir a previsão.
    move_queue: deque[tuple[int, MoveKeys]] = field(default_factory=deque)
    last_seq: int = 0
    ack: int = 0

    kind: ClassVar[str] = "player"

    def stat_level(self, stat: str) -> int:
        return self.stats[stat].level

    @property
    def bar_skills(self) -> list[str]:
        """As skills que estão na barra (sem os espaços vazios), na ordem."""
        return [sid for sid in self.bar if sid]

    @property
    def race_id(self) -> str | None:
        return self.race_def.id if self.race_def else None

    @property
    def class_id(self) -> str | None:
        return self.class_def.id if self.class_def else None

    def _bonus(self, key: str) -> float:
        """Bônus somado da raça e dos equipamentos."""
        total = self.race_def.bonus.get(key, 0.0) if self.race_def else 0.0
        for item in self.inventory.equipment.values():
            total += item.bonus.get(key, 0.0)
        return total

    def modifier(self, name: str) -> float:
        """Multiplicador dos efeitos ativos e dos equipamentos."""
        result = super().modifier(name)
        for item in self.inventory.equipment.values():
            result *= item.modifiers.get(name, 1.0)
        return result

    # --- Valores derivados dos atributos, da raça e do equipamento ---
    @property
    def max_hp(self) -> float:
        return max(10.0, 80 + 20 * self.stat_level("vitality") + self._bonus("max_hp"))

    @property
    def max_energy(self) -> float:
        return max(10.0, 40 + 15 * self.stat_level("spirit") + self._bonus("max_energy"))

    @property
    def base_speed(self) -> float:
        speed = C.BASE_SPEED + C.SPEED_PER_AGILITY * self.stat_level("agility") + self._bonus("speed")
        return max(30.0, min(C.MAX_SPEED, speed))

    def private_snapshot(self) -> dict:
        """O que só o próprio jogador vê (HUD e previsão de movimento)."""
        return {
            "hp": round(self.hp),
            "mhp": round(self.max_hp),
            "en": round(self.energy),
            "men": round(self.max_energy),
            "fat": round(self.fatigue),
            "mfat": round(C.MAX_FATIGUE),
            "s": self.state,
            "race": self.race_id,
            "cls": self.class_id,
            "x": self.x,
            "y": self.y,
            "spd": self.speed,
            "ack": self.ack,
            "skills": self.bar,  # barra de atalhos (null = espaço vazio)
            "cd": {sid: round(t, 2) for sid, t in self.cooldowns.items() if t > 0},
            "icd": {iid: round(t, 1) for iid, t in self.item_cooldowns.items() if t > 0},
            "gold": self.inventory.gold,
            "ef": [{"id": eid, "t": round(a.remaining, 1)} for eid, a in self.effects.items()],
            "stats": {
                n: {"lv": s.level, "xp": int(s.xp), "next": s.xp_to_next()} for n, s in self.stats.items()
            },
        }

    # --- Persistência ---
    def to_dict(self) -> dict:
        return {
            "v": SAVE_VERSION,
            "race": self.race_id,
            "class": self.class_id,
            "flags": sorted(self.flags),
            "skills": {"known": list(self.known), "bar": list(self.bar)},
            "quests": {"active": {qid: list(p) for qid, p in self.quests.items()},
                       "done": {qid: {"n": d[0], "at": d[1]} for qid, d in self.quests_done.items()}},
            "inventory": self.inventory.to_dict(),
            "social": {"profile": self.profile, "ignore": list(self.ignored)},
            "map": self.map_id,
            "x": self.x,
            "y": self.y,
            "hp": self.hp,
            "energy": self.energy,
            "fatigue": self.fatigue,
            "facing": self.facing,
            "stats": {n: {"level": s.level, "xp": s.xp} for n, s in self.stats.items()},
        }

    def load_dict(self, data: dict) -> None:
        """Carrega o que for válido e ignora o resto (dados corrompidos).

        Espera um save já migrado (saves.migrate). Raça e classe são resolvidas
        pelo World antes, porque dependem do conteúdo carregado.
        """
        flags = data.get("flags")
        if isinstance(flags, list):
            self.flags = {f for f in flags if isinstance(f, str)}
        quests = data.get("quests") if isinstance(data.get("quests"), dict) else {}
        active = quests.get("active") if isinstance(quests.get("active"), dict) else {}
        self.quests = {
            qid: [max(0, v) for v in prog] for qid, prog in active.items()
            if isinstance(qid, str) and isinstance(prog, list)
            and all(isinstance(v, int) and not isinstance(v, bool) for v in prog)
        }
        done = quests.get("done") if isinstance(quests.get("done"), dict) else {}
        self.quests_done = {
            qid: [d["n"], float(d["at"])] for qid, d in done.items()
            if isinstance(d, dict) and isinstance(d.get("n"), int) and isinstance(d.get("at"), (int, float))
        }
        social = data.get("social") if isinstance(data.get("social"), dict) else {}
        if isinstance(social.get("profile"), str):
            self.profile = social["profile"][: C.PROFILE_MAX_LEN]
        if isinstance(social.get("ignore"), list):
            self.ignored = [n for n in social["ignore"] if isinstance(n, str)][: C.IGNORE_MAX]
        for key in ("x", "y", "hp", "energy", "fatigue"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                setattr(self, key, float(value))
        if data.get("facing") in DIRECTIONS:
            self.facing = data["facing"]
        for name, raw in (data.get("stats") or {}).items():
            if name in self.stats and isinstance(raw, dict):
                level = raw.get("level", 1)
                xp = raw.get("xp", 0.0)
                if isinstance(level, int) and isinstance(xp, (int, float)):
                    self.stats[name] = Stat(max(1, min(level, C.MAX_STAT_LEVEL)), max(0.0, float(xp)))
        self.hp = max(1.0, min(self.hp, self.max_hp))
        self.energy = max(0.0, min(self.energy, self.max_energy))
        self.fatigue = max(0.0, min(self.fatigue, C.MAX_FATIGUE))


@dataclass(eq=False)
class Enemy(Entity):
    defn: EnemyDef | None = None
    home_x: float = 0.0
    home_y: float = 0.0
    elite: bool = False  # sorteado a cada nascimento (defn.elite_chance)
    phase: int = 0  # chefes: quantas fases já começaram (0 = a inicial)
    summoner_id: int | None = None  # invocado por este chefe: não renasce, some depois de morto
    event_id: str | None = None  # invasor de um evento em ondas: não renasce, não desiste por distância
    calm_timer: float = 0.0  # chefes: há quanto tempo está sem alvo
    target_id: int | None = None
    wander_dir: tuple[float, float] = (0.0, 0.0)
    wander_timer: float = 0.0
    detour_side: int = 1  # lado (+1/-1) para contornar obstáculos ao perseguir
    detour_timer: float = 0.0  # enquanto > 0, continua contornando em vez de ir reto
    path: list[tuple[float, float]] = field(default_factory=list)  # pontos do caminho (A*) até o alvo
    path_goal: tuple[int, int] | None = None  # tile para onde o caminho leva
    path_timer: float = 0.0  # recalcula o caminho quando chega a 0
    ignore_id: int | None = None  # jogador sem caminho possível: ignorado por ignore_timer segundos
    ignore_timer: float = 0.0

    kind: ClassVar[str] = "enemy"

    @property
    def max_hp(self) -> float:
        return self.defn.hp * (C.ELITE_HP if self.elite else 1.0)

    @property
    def base_speed(self) -> float:
        speed = self.defn.speed
        for phase in self.defn.phases[: self.phase]:
            speed *= phase.speed
        return speed

    @property
    def power(self) -> float:
        power = self.defn.damage * (C.ELITE_DAMAGE if self.elite else 1.0)
        for phase in self.defn.phases[: self.phase]:
            power *= phase.damage
        return power

    @property
    def skills(self) -> tuple[str, ...]:
        """Skills da fase atual (a última fase que trocou as skills) ou as do tipo."""
        for phase in reversed(self.defn.phases[: self.phase]):
            if phase.skills is not None:
                return phase.skills
        return self.defn.skills

    def body_half(self) -> float:
        return C.ENTITY_HALF * self.defn.size * (C.ELITE_SIZE if self.elite else 1.0)

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["t"] = self.defn.id  # tipo, para o cliente escolher a aparência
        snap["lv"] = self.defn.level
        if self.half != C.ENTITY_HALF:
            snap["h"] = round(self.half, 2)  # corpo maior (chefe, elite): desenho e colisão
        if self.elite:
            snap["el"] = 1
        if self.defn.boss:
            snap["boss"] = 1
            snap["ph"] = self.phase
        if self.event_id:
            snap["inv"] = 1  # invasor de um evento (o cliente destaca)
        return snap


@dataclass(eq=False)
class Npc(Entity):
    """Personagem do mundo (data/npcs.yaml): fica parado, não luta e não pode ser ferido."""

    defn: NpcDef | None = None

    kind: ClassVar[str] = "npc"

    @property
    def max_hp(self) -> float:
        return 1.0

    @property
    def base_speed(self) -> float:
        return 0.0

    def snapshot(self) -> dict:
        return {"id": self.id, "k": self.kind, "n": self.name, "x": round(self.x), "y": round(self.y),
                "f": self.facing, "s": "idle", "t": self.defn.id}


@dataclass(eq=False)
class Projectile:
    id: int
    owner_id: int
    skill_id: str
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    ttl: float
    radius: float
    map_id: str = ""

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "s": self.skill_id,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "vx": round(self.vx, 1),
            "vy": round(self.vy, 1),
        }


@dataclass(eq=False)
class GroundItem:
    """Item (ou moedas, se item for None) caído no chão."""

    id: int
    item: ItemDef | None
    qty: int
    x: float
    y: float
    owner_id: int | None  # só ele pega até owner_until
    owner_until: float
    expires_at: float
    ignore_id: int | None = None  # quem largou o item não pega de volta até ignore_until
    ignore_until: float = 0.0
    map_id: str = ""
    party_id: int | None = None  # saque do grupo: qualquer membro pega durante a exclusividade

    def snapshot(self) -> dict:
        return {"id": self.id, "i": self.item.id if self.item else "gold", "q": self.qty,
                "x": round(self.x), "y": round(self.y)}
