"""Relatos de bug: o jogador descreve o problema e o servidor guarda junto onde e quando.

O cliente manda {"t": "bug", "text": "...", "client": {...}} pelo botão "Relatar bug" e
recebe {"t": "bug_result", "ok", "text"}.
O servidor completa com personagem, conta, mapa, posição, zona e hora (tabela
bug_reports) e avisa a equipe online. "client" traz o que só o navegador sabe (visão
2D/3D, navegador, tamanho da tela, erros recentes do JavaScript), com tamanho limitado.

A equipe lê e marca como resolvido na janela de administração (permissão "log"), ou
pelo terminal: ./admin.sh bugs  e  ./admin.sh bug-resolvido N.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import config as C

if TYPE_CHECKING:
    from .entities import Player
    from .world import World

CLIENT_KEYS = {"view": 10, "agent": 300, "screen": 30, "errors": 5}  # campo -> tamanho máximo
ZONE_LABELS = {"safe": "zona segura", "pve": "PvE", "pvp": "PvP"}  # zonas sem nome


def _clean_client(raw: Any) -> dict:
    """Só os campos conhecidos, com texto curto (o cliente pode mandar qualquer coisa)."""
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, limit in CLIENT_KEYS.items():
        value = raw.get(key)
        if key == "errors" and isinstance(value, list):
            clean[key] = [str(e)[:300] for e in value[:limit]]
        elif isinstance(value, str):
            clean[key] = value[:limit]
    return clean


class BugReports:
    def __init__(self, world: World):
        self.world = world
        self.last_report: dict[str, float] = {}  # personagem -> horário do mundo do último relato

    def report(self, p: Player, data: dict) -> None:
        w = self.world
        text = str(data.get("text", "")).strip()[: C.BUG_REPORT_MAX_LEN]
        if len(text) < 5:
            self._answer(p, False, "Descreva o problema com um pouco mais de detalhe.")
            return
        last = self.last_report.get(p.name)
        if last is not None and w.time - last < C.BUG_REPORT_COOLDOWN:
            self._answer(p, False, f"Espere {C.BUG_REPORT_COOLDOWN - (w.time - last):.0f} s para mandar outro relato.")
            return
        self.last_report[p.name] = w.time
        account = w.db.account_by_id(p.account_id) if p.account_id is not None else None
        zone = w._map(p).zone_at(p.x, p.y)
        report_id = w.db.add_bug_report(
            p.name, account.name if account else "", p.map_id, round(p.x), round(p.y),
            zone.name or ZONE_LABELS.get(zone.type, zone.type), text, _clean_client(data.get("client")),
        )
        self._answer(p, True, f"Obrigado! Relato #{report_id} enviado para a equipe.")
        for other in w.players.values():
            if other.staff_role and other.staff_role.can("log") and other is not p:
                w._private(other, f"Novo relato de bug #{report_id} de {p.name}: {text[:80]}")

    def _answer(self, p: Player, ok: bool, text: str) -> None:
        """Resposta para a janela de relato (bug_result) e a mesma linha no chat."""
        self.world._outbox.append((p.id, {"t": "bug_result", "ok": ok, "text": text}))
        self.world._private(p, text)
