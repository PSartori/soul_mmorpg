"""Controle da economia: de onde as moedas entram, para onde vão e quantas existem.

Regra do jogo: moedas só ENTRAM no mundo quando alguém vende um item para uma loja de
NPC ("venda"). Inimigos não deixam moedas, e missões, regras e eventos não dão moedas
(dão itens, que podem ser vendidos). A equipe pode dar moedas para corrigir um problema
("equipe"; também fica no registro da equipe).

Moedas SAEM do mundo (somem) quando alguém compra de uma loja ("loja"), paga uma receita
de oficina ("oficina"), aprende uma skill num treinador ("treinador") ou perde moedas por
uma regra ("regra", ex.: morrer). Trocas entre jogadores e o banco só MUDAM moedas de
lugar ("troca", "banco"): não criam nem destroem.

Cada movimento é somado por dia, categoria e detalhe (o item vendido, a loja, a oficina...)
e vai para o banco de dados no salvamento automático (a cada 60 s) e ao desligar o servidor.
O relatório (janela de administração e ./admin.sh economia) junta isso com quantas moedas
existem hoje nas bolsas e nos bancos.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .content import Content
    from .db import Database
    from .world import World

SOURCES = ("venda", "equipe")  # criam moedas
SINKS = ("loja", "oficina", "treinador", "regra")  # destroem moedas
MOVES = ("troca", "banco")  # só mudam de lugar
LABELS = {
    "venda": "Vendas para lojas", "equipe": "Dadas pela equipe",
    "loja": "Compras em lojas", "oficina": "Preço de receitas", "treinador": "Treinadores", "regra": "Perdas por regras",
    "troca": "Trocas entre jogadores", "banco": "Banco (guardar e sacar)",
}
GROWTH_ALERT = 0.25  # avisa se as moedas em circulação cresceram mais que isto no período


def day_of(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


class Economy:
    def __init__(self, world: World):
        self.world = world
        # (dia, categoria, detalhe) -> [moedas, vezes], ainda não gravado no banco de dados
        self.pending: dict[tuple[str, str, str], list[int]] = {}

    def record(self, kind: str, detail: str, amount: int) -> None:
        """Anota um movimento de moedas (amount > 0)."""
        if amount <= 0:
            return
        entry = self.pending.setdefault((day_of(self.world.clock()), kind, detail), [0, 0])
        entry[0] += amount
        entry[1] += 1

    def flush(self) -> None:
        if self.pending:
            self.world.db.add_economy([(d, k, det, a, c) for (d, k, det), (a, c) in self.pending.items()])
            self.pending.clear()

    def report(self, days: int = 7) -> dict:
        """Relatório com o que está em memória já gravado e as moedas atuais de quem está online."""
        self.flush()
        w = self.world
        live_bags = {p.name.lower(): p.inventory.gold for p in w.players.values()}
        live_banks = {p.account_id: p.bank.gold for p in w.players.values()
                      if p.bank is not None and p.account_id is not None}
        return economy_report(w.db, w.content, days, w.clock(), live_bags, live_banks)


def economy_report(db: Database, content: Content, days: int, now: float,
                   live_bags: dict[str, int] | None = None, live_banks: dict[int, int] | None = None) -> dict:
    """Junta os movimentos dos últimos `days` dias (hoje incluso) e as moedas que existem agora.

    live_bags / live_banks: valores de quem está online (mais novos que o último salvamento).
    """
    days = max(1, min(days, 365))
    day_list = [day_of(now - 86400 * i) for i in reversed(range(days))]
    rows = db.economy_rows(day_list[0])

    daily = {d: {"in": 0, "out": 0, "moved": 0} for d in day_list}
    by_kind: dict[str, int] = {}
    by_detail: dict[tuple[str, str], list[int]] = {}
    for day, kind, detail, amount, count in rows:
        if day not in daily:
            continue
        side = "in" if kind in SOURCES else "out" if kind in SINKS else "moved"
        daily[day][side] += amount
        by_kind[kind] = by_kind.get(kind, 0) + amount
        entry = by_detail.setdefault((kind, detail), [0, 0])
        entry[0] += amount
        entry[1] += count

    def detail_label(kind: str, detail: str) -> str:
        if kind in ("venda", "loja"):
            return getattr(content.items.get(detail), "name", detail)
        if kind == "oficina":
            return getattr(content.crafting.get(detail), "name", detail)
        if kind == "treinador":
            return getattr(content.trainers.get(detail), "name", detail)
        return detail

    def top(kinds: tuple[str, ...], n: int) -> list[dict]:
        found = [(kind, detail, a, c) for (kind, detail), (a, c) in by_detail.items() if kind in kinds]
        found.sort(key=lambda r: -r[2])
        return [{"kind": LABELS.get(k, k), "what": detail_label(k, d), "amount": a, "count": c}
                for k, d, a, c in found[:n]]

    bags, banks = db.gold_holdings()
    live_bags = live_bags or {}
    live_banks = live_banks or {}
    bags = [(name, live_bags.get(name.lower(), gold)) for name, gold in bags]
    banks = [(account, live_banks.get(account_id, gold)) for account, account_id, gold in banks]
    bag_total = sum(g for _, g in bags)
    bank_total = sum(g for _, g in banks)
    total = bag_total + bank_total

    period_in = sum(v["in"] for v in daily.values())
    period_out = sum(v["out"] for v in daily.values())
    net = period_in - period_out
    alerts = []
    before = total - net
    if net > 0 and before > 0 and net / before > GROWTH_ALERT:
        alerts.append(f"As moedas em circulação cresceram {round(100 * net / before)}% em {days} dia(s): "
                      "entram bem mais moedas (vendas) do que saem. Considere baixar o que as lojas pagam "
                      "ou criar mais formas de gastar.")
    if by_kind.get("equipe"):
        alerts.append(f"A equipe deu {by_kind['equipe']} moedas no período (confira no registro da equipe).")

    today = daily[day_list[-1]]
    return {
        "days": days,
        "supply": {"bags": bag_total, "banks": bank_total, "total": total, "characters": len(bags)},
        "today": {"in": today["in"], "out": today["out"], "net": today["in"] - today["out"]},
        "period": {"in": period_in, "out": period_out, "net": net},
        "daily": [{"day": d[8:10] + "/" + d[5:7], **v, "net": v["in"] - v["out"]} for d, v in daily.items()],
        "kinds": [{"kind": k, "label": LABELS[k], "side": "in" if k in SOURCES else "out" if k in SINKS else "moved",
                   "amount": by_kind.get(k, 0)} for k in SOURCES + SINKS + MOVES],
        "top_sold": top(("venda",), 8),
        "top_spent": top(SINKS, 8),
        "richest": [{"name": n, "gold": g} for n, g in sorted(bags, key=lambda r: -r[1])[:10] if g > 0],
        "richest_banks": [{"name": n, "gold": g} for n, g in sorted(banks, key=lambda r: -r[1])[:5] if g > 0],
        "alerts": alerts,
    }


def report_lines(rep: dict[str, Any]) -> list[str]:
    """O relatório em texto (terminal)."""
    s = rep["supply"]
    p = rep["period"]
    lines = [
        f"Moedas existentes: {s['total']} ({s['bags']} nas bolsas de {s['characters']} personagens, "
        f"{s['banks']} nos bancos)",
        f"Últimos {rep['days']} dia(s): entraram {p['in']}, saíram {p['out']}, saldo {p['net']:+d}",
        "",
        "Dia     entraram   saíram    saldo  mudaram de lugar",
    ]
    for d in rep["daily"]:
        lines.append(f"{d['day']}  {d['in']:>9} {d['out']:>8} {d['net']:>+8}  {d['moved']:>10}")
    lines.append("")
    for k in rep["kinds"]:
        sign = {"in": "+", "out": "-", "moved": "="}[k["side"]]
        lines.append(f"  {sign} {k['label']:<26} {k['amount']}")
    if rep["top_sold"]:
        lines += ["", "Itens que mais trouxeram moedas (vendas):"]
        lines += [f"  {r['what']:<24} {r['amount']:>7} moedas em {r['count']} venda(s)" for r in rep["top_sold"]]
    if rep["top_spent"]:
        lines += ["", "Onde mais se gastou:"]
        lines += [f"  {r['kind']}: {r['what']:<20} {r['amount']:>7} moedas" for r in rep["top_spent"]]
    if rep["richest"]:
        lines += ["", "Mais moedas na bolsa:"] + [f"  {r['name']:<16} {r['gold']}" for r in rep["richest"]]
    if rep["richest_banks"]:
        lines += ["", "Mais moedas no banco (conta):"] + [f"  {r['name']:<16} {r['gold']}" for r in rep["richest_banks"]]
    if rep["alerts"]:
        lines += ["", "Alertas:"] + [f"  ! {a}" for a in rep["alerts"]]
    return lines
