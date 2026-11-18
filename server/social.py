"""Ferramentas de roleplay: fala local, ações, mensagem privada, ignorar e perfil.

- **Fala local** (canal "Local"): só quem está no mesmo mapa e perto ouve
  (C.LOCAL_CHAT_RANGE); o cliente mostra a fala num balão acima do personagem.
- **Ação** (canal "Ação" ou /me): narra o que o personagem faz ("* Ana acena *"),
  com o mesmo alcance e balão da fala local.
- **Mensagem privada** (menu do jogador ou /p Nome texto; /r responde): só os dois veem.
- **Ignorar** (menu do jogador; lista na janela de perfil): quem você ignora não te
  manda mensagens (de nenhum canal), desafios nem convites de grupo. A lista vai no save.
  Avisos do sistema e anúncios da equipe sempre chegam, e não dá para ignorar a equipe.
- **Perfil** (janela P; "Ver perfil" no menu do jogador): descrição livre do personagem
  (aparência, história), salva no save. A equipe com a permissão `mute` pode apagar.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from . import config as C
from .entities import Player

if TYPE_CHECKING:
    from .world import World

# caracteres de controle (menos a quebra de linha) não entram no perfil
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def clean_profile(text: str) -> str:
    """Texto do perfil: sem caracteres de controle, no máximo 2 linhas em branco seguidas, cortado."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[: C.PROFILE_MAX_LEN].strip()


def _level(p: Player) -> int:
    """Nível do personagem: a média dos atributos (o mesmo do painel)."""
    levels = [s.level for s in p.stats.values()]
    return sum(levels) // len(levels) if levels else 1


class SocialSystem:
    def __init__(self, world: World):
        self.world = world

    # ------------------------------------------------------------ ignorar

    @staticmethod
    def ignores(who: Player, other: Player) -> bool:
        """`who` ignora `other`?"""
        return other.name.lower() in {n.lower() for n in who.ignored}

    def deliver(self, sender: Player, recipients, msg: dict) -> None:
        """Manda `msg` para cada destinatário que não ignora quem falou."""
        for other in recipients:
            if other is sender or not self.ignores(other, sender):
                self.world._outbox.append((other.id, msg))

    def ignore(self, p: Player, name: object) -> None:
        w = self.world
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            return
        if name.lower() == p.name.lower():
            w._private(p, "Você não pode ignorar a si mesmo.")
            return
        online = self._online(name)
        record = None if online else w.db.load_character(name)
        if online is None and record is None:
            w._private(p, f"Nenhum personagem chamado {name!r}.")
            return
        real = online.name if online else record.name  # type: ignore[union-attr]
        if w.admin.role_of_name(real):
            w._private(p, "Não dá para ignorar alguém da equipe.")
            return
        if any(n.lower() == real.lower() for n in p.ignored):
            w._private(p, f"Você já ignora {real}.")
            return
        if len(p.ignored) >= C.IGNORE_MAX:
            w._private(p, f"Você já ignora {C.IGNORE_MAX} personagens; deixe de ignorar alguém antes.")
            return
        p.ignored.append(real)
        self._drop_pending_from(p, real)
        w._private(p, f"Você está ignorando {real}: não verá mais mensagens, desafios nem convites desse personagem.")

    def unignore(self, p: Player, name: object) -> None:
        name = name.strip() if isinstance(name, str) else ""
        found = next((n for n in p.ignored if n.lower() == name.lower()), None)
        if found is None:
            self.world._private(p, f"Você não ignora {name or 'ninguém com esse nome'}.")
            return
        p.ignored.remove(found)
        self.world._private(p, f"Você deixou de ignorar {found}.")

    def _drop_pending_from(self, p: Player, name: str) -> None:
        """Ignorou alguém: some o desafio ou convite de grupo que ele mandou e ainda está na tela."""
        w = self.world
        challenge = w.duels.challenges.get(p.id)
        if challenge and (c := w.players.get(challenge.challenger_id)) and c.name.lower() == name.lower():
            del w.duels.challenges[p.id]
        invite = w.parties.invites.get(p.id)
        if invite and (i := w.players.get(invite.inviter_id)) and i.name.lower() == name.lower():
            del w.parties.invites[p.id]

    def snapshot_for(self, p: Player) -> tuple[bool, list[str]]:
        """(mudou, lista de ignorados). A lista só vai ao cliente quando muda."""
        names = sorted(p.ignored, key=str.lower)
        key = json.dumps(names)
        if key == p.social_sent:
            return False, names
        p.social_sent = key
        return True, names

    # ------------------------------------------------------------ fala local e ações

    def _near(self, p: Player) -> list[Player]:
        """Quem ouve a fala local de p: mesmo mapa e até C.LOCAL_CHAT_RANGE (inclusive ele)."""
        r2 = C.LOCAL_CHAT_RANGE ** 2
        return [o for o in self.world.players.values()
                if o.map_id == p.map_id and (o.x - p.x) ** 2 + (o.y - p.y) ** 2 <= r2]

    def _can_speak(self, p: Player) -> bool:
        if muted := self.world.admin.mute_message(p):
            self.world._private(p, muted)
            return False
        return True

    def say(self, p: Player, text: str) -> None:
        """Fala local: quem está perto vê no chat e num balão acima de p."""
        text = text.strip()[: C.CHAT_MAX_LEN]
        if text and self._can_speak(p):
            self.deliver(p, self._near(p), {"t": "chat", "from": p.name, "text": text, "ch": "local", "id": p.id})

    def emote(self, p: Player, text: str) -> None:
        """Ação narrada (/me): "* Ana acena *", com o alcance e o balão da fala local."""
        text = text.strip()[: C.CHAT_MAX_LEN]
        if not text:
            self.world._private(p, "Descreva a ação. Ex.: acena para todos.")
            return
        if self._can_speak(p):
            self.deliver(p, self._near(p), {"t": "chat", "from": p.name, "text": text, "ch": "emote", "id": p.id})

    # ------------------------------------------------------------ mensagem privada

    def whisper(self, p: Player, to: object, text: str) -> None:
        w = self.world
        text = text.strip()[: C.CHAT_MAX_LEN]
        name = to.strip() if isinstance(to, str) else ""
        if not name:
            w._private(p, "Para quem? Clique num jogador e escolha \"Mensagem privada\".")
            return
        if not text:
            return
        target = self._online(name)
        if target is None:
            w._private(p, f"{name} não está online.")
        elif target is p:
            w._private(p, "Você não pode mandar mensagem para si mesmo.")
        elif self._can_speak(p):
            if self.ignores(target, p):
                w._private(p, f"{target.name} não está recebendo mensagens suas.")
                return
            w._outbox.append((target.id, {"t": "chat", "from": p.name, "text": text, "ch": "pm"}))
            w._outbox.append((p.id, {"t": "chat", "from": p.name, "to": target.name, "text": text, "ch": "pm"}))
            target.last_pm = p.name
            p.last_pm = target.name

    def reply(self, p: Player, text: str) -> None:
        if not p.last_pm:
            self.world._private(p, "Ninguém para responder ainda.")
            return
        self.whisper(p, p.last_pm, text)

    # ------------------------------------------------------------ perfil

    def set_profile(self, p: Player, text: object) -> None:
        if not isinstance(text, str):
            return
        if not self._can_speak(p):  # silenciado também não muda o perfil
            return
        p.profile = clean_profile(text)
        self.world._private(p, "Perfil salvo." if p.profile else "Descrição apagada.")
        self.send_profile(p, p)

    def view(self, p: Player, name: object) -> None:
        name = name.strip() if isinstance(name, str) else ""
        target = self._online(name) if name else p
        if target is None:
            self.world._private(p, f"{name} não está online.")
            return
        self.send_profile(p, target)

    def send_profile(self, viewer: Player, target: Player) -> None:
        w = self.world
        role = viewer.staff_role
        other_role = target.staff_role
        can_clear = (
            target is not viewer and role is not None and role.can("mute")
            and (other_role is None or other_role.level < role.level)
        )
        w._outbox.append((viewer.id, {
            "t": "profile",
            "name": target.name,
            "race": target.race_id,
            "cls": target.class_id,
            "lv": _level(target),
            "o": w.orgs.snapshot(target),
            "st": other_role.id if other_role else None,
            "text": target.profile,
            "me": target is viewer,
            "ignored": self.ignores(viewer, target),
            "can_clear": can_clear,
        }))

    # ------------------------------------------------------------ pedidos do cliente

    def handle_op(self, p: Player, data: dict) -> None:
        """Mensagens {t: "social", op: ...} da interface."""
        op = data.get("op")
        if op == "ignore":
            self.ignore(p, data.get("name"))
        elif op == "unignore":
            self.unignore(p, data.get("name"))
        elif op == "profile":
            self.view(p, data.get("name"))
        elif op == "set_profile":
            self.set_profile(p, data.get("text"))

    def _online(self, name: str) -> Player | None:
        return next((o for o in self.world.players.values() if o.name.lower() == name.lower()), None)
