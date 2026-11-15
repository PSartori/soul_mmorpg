"""Relatório de balanceamento: classes, skills, PvE, PvP e equipamentos, com números.

Uso:
  ./balance.sh                                  # relatório no terminal
  ./balance.sh --out docs/balanceamento.md      # salva em arquivo (Markdown)
  ./balance.sh --levels 1 20 50 --seconds 60
  ./balance.sh --equip espada_curta,gibao_de_couro   # todos lutam com esse equipamento

As lutas rodam no motor do jogo (veja tools/simulator.py), então os números
acompanham qualquer mudança nos YAML de data/.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from server import config as C
from server.content import Content, ContentError
from tools.simulator import Build, FightResult, Simulator

# Limites dos alertas
DPS_SPREAD = 0.25  # classe com dano/s 25% acima ou abaixo da mediana
TOP_SOURCE_SHARE = 0.75  # uma única fonte responde por 75%+ do dano
LOW_SKILL_SHARE = 0.05  # skill de dano que rende menos de 5% do total
PVP_RATIO = 2.0  # um lado vence em menos da metade do tempo do outro
ITEM_MIN_GAIN = 0.01  # equipamento que muda menos de 1% em dano e em vida efetiva
TRIVIAL_MARGIN = 20.0  # inimigo que você mata 20× mais rápido do que ele te mata: não é desafio


def num(x: float, digits: int = 1) -> str:
    if x == float("inf"):
        return "∞"
    return f"{x:.{digits}f}".replace(".", ",")


def pct(x: float) -> str:
    return f"{x * 100:+.0f}%".replace(".", ",")


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines + [""]


def pve_verdict(margin: float) -> str:
    """margin = quanto tempo o inimigo leva para te matar ÷ quanto você leva para matá-lo."""
    if margin >= 3:
        return "fácil"
    if margin >= 1.5:
        return "ok"
    if margin >= 1:
        return "apertado"
    return "**perde**"


@dataclass
class Report:
    content: Content
    levels: list[int]
    seconds: float
    equipment: tuple[str, ...]
    lines: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    # alertas que se repetem por nível: (tipo, classe, alvo) -> {nível: valor}
    grouped: dict[tuple[str, str, str], dict[int, float]] = field(default_factory=dict)

    def note(self, kind: str, who: str, what: str, level: int, value: float) -> None:
        self.grouped.setdefault((kind, who, what), {})[level] = value

    def build(self, class_id: str, level: int, extra: tuple[str, ...] = ()) -> Build:
        return Build(class_id, level, self.equipment + extra)

    def generate(self) -> str:
        sim = Simulator(self.content)
        classes = list(self.content.classes)
        enemies = list(self.content.enemies)
        started = time.time()
        body: list[str] = []
        used_skills: dict[str, set[str]] = {c: set() for c in classes}

        for level in self.levels:
            body += [f"## Nível {level}", ""]
            profiles = {c: sim.profile(self.build(c, level)) for c in classes}
            sustained = {c: sim.dummy_output(self.build(c, level), self.seconds) for c in classes}
            burst = {c: sim.dummy_output(self.build(c, level), 5.0) for c in classes}

            # ---- classes
            body += [f"### Classes", ""]
            rows = []
            for c in classes:
                cls, prof = self.content.classes[c], profiles[c]
                rows.append([
                    cls.name, self.content.races[cls.race].name, num(prof["max_hp"], 0), num(prof["max_energy"], 0),
                    num(prof["speed"], 0), num(prof["effective_hp"], 0),
                    num(sustained[c].dps(sustained[c].a)), num(burst[c].dps(burst[c].a)),
                ])
                for item in prof["skipped"]:
                    self.alerts.append(f"Nível {level}: {cls.name} não pode usar {self.content.items[item].name} "
                                       "(ficou de fora da simulação).")
            body += table(["Classe", "Raça", "Vida", "Energia", "Velocidade", "Vida efetiva",
                           f"Dano/s ({num(self.seconds, 0)} s)", "Dano/s (5 s)"], rows)
            median = statistics.median(sustained[c].dps(sustained[c].a) for c in classes)
            for c in classes:
                dps = sustained[c].dps(sustained[c].a)
                if median > 0 and abs(dps / median - 1) > DPS_SPREAD:
                    side = "acima" if dps > median else "abaixo"
                    self.alerts.append(f"Nível {level}: **{self.content.classes[c].name}** tem dano/s {pct(dps / median - 1)} "
                                       f"{side} da mediana das classes ({num(dps)} contra {num(median)}).")

            # ---- rotação
            body += ["### De onde vem o dano (alvo parado)", ""]
            rows = []
            for c in classes:
                result = sustained[c]
                total = result.a.damage or 1.0
                for source, dealt in sorted(result.a.by_source.items(), key=lambda kv: -kv[1]):
                    name = self._source_name(source)
                    uses = result.a.uses.get(source, 0)
                    rows.append([self.content.classes[c].name, name, str(uses) if uses else "–",
                                 num(dealt, 0), num(dealt / total * 100, 0) + "%"])
                    used_skills[c].add(source)
                for source, uses in result.a.uses.items():
                    used_skills[c].add(source)
                top_source, top_damage = max(result.a.by_source.items(), key=lambda kv: kv[1], default=("", 0))
                if top_damage / total >= TOP_SOURCE_SHARE:
                    self.note("dominance", c, top_source, level, top_damage / total)
                for sid in self.content.classes[c].skills:
                    skill = self.content.skills[sid]
                    if skill.type not in ("melee", "projectile", "area") or self._has_utility(skill):
                        continue  # o valor dela não é (só) dano
                    dealt = result.a.by_source.get(sid, 0.0) + sum(
                        result.a.by_source.get(f"efeito:{app.effect}", 0.0) for app in skill.effects)
                    if sid in used_skills[c] and dealt / total < LOW_SKILL_SHARE:
                        self.note("low", c, sid, level, dealt / total)
            body += table(["Classe", "Fonte", "Usos", "Dano", "% do total"], rows)

            # ---- PvE
            body += ["### PvE", "",
                     "*Você mata em*: tempo para zerar a vida do inimigo. *Ele mata você em*: o contrário. "
                     "*Folga*: quantas vezes você é mais rápido (≥ 3 fácil, ≥ 1,5 ok, ≥ 1 apertado, < 1 perde).", ""]
            rows = []
            for c in classes:
                for enemy_id in enemies:
                    r = sim.fight(self.build(c, level), enemy_id, self.seconds)
                    you, it = r.time_to_kill(r.a, r.b), r.time_to_kill(r.b, r.a)
                    margin = it / you if you > 0 else float("inf")
                    verdict = pve_verdict(margin)
                    rows.append([self.content.classes[c].name, self.content.enemies[enemy_id].name,
                                 num(you) + " s", num(it) + " s", num(margin), verdict])
                    if margin < 1:
                        self.note("loses", c, enemy_id, level, margin)
                    elif margin >= TRIVIAL_MARGIN:
                        self.note("trivial", c, enemy_id, level, margin)
            body += table(["Classe", "Inimigo", "Você mata em", "Ele mata você em", "Folga", "Veredito"], rows)

            # ---- PvP
            body += ["### PvP (mesmo nível, lutando parados)", "",
                     "Tempo para a classe da linha vencer a da coluna.", ""]
            matrix = {}
            for c1 in classes:
                for c2 in classes:
                    r = sim.fight(self.build(c1, level), self.build(c2, level), self.seconds)
                    matrix[c1, c2] = (r.time_to_kill(r.a, r.b), r.time_to_kill(r.b, r.a))
            names = [self.content.classes[c].name for c in classes]
            rows = [[self.content.classes[c1].name] + [num(matrix[c1, c2][0]) + " s" for c2 in classes] for c1 in classes]
            body += table(["ataca \\ defende"] + names, rows)
            for i, c1 in enumerate(classes):
                for c2 in classes[i + 1:]:
                    t1, t2 = matrix[c1, c2]
                    if min(t1, t2) > 0 and max(t1, t2) / min(t1, t2) >= PVP_RATIO:
                        winner, loser = (c1, c2) if t1 < t2 else (c2, c1)
                        self.alerts.append(
                            f"Nível {level}: em PvP, **{self.content.classes[winner].name}** vence "
                            f"{self.content.classes[loser].name} com folga ({num(min(t1, t2))} s contra {num(max(t1, t2))} s).")

            # ---- equipamentos
            gear = [i for i in self.content.items.values() if i.type == "equipment" and i.id not in self.equipment]
            if gear:
                body += ["### Equipamentos (ganho sobre não usar o item)", ""]
                rows = []
                for item in gear:
                    gains = []
                    for c in classes:
                        prof_with = sim.profile(self.build(c, level, (item.id,)))
                        if item.id in prof_with["skipped"]:
                            continue
                        with_item = sim.dummy_output(self.build(c, level, (item.id,)), self.seconds)
                        base_dps = sustained[c].dps(sustained[c].a)
                        dps_gain = with_item.dps(with_item.a) / base_dps - 1 if base_dps else 0.0
                        ehp_gain = prof_with["effective_hp"] / profiles[c]["effective_hp"] - 1
                        gains.append((dps_gain, ehp_gain))
                        rows.append([item.name, self.content.classes[c].name, pct(dps_gain), pct(ehp_gain)])
                    if gains and all(abs(d) < ITEM_MIN_GAIN and abs(e) < ITEM_MIN_GAIN for d, e in gains):
                        self.alerts.append(f"Nível {level}: **{item.name}** quase não muda dano nem vida efetiva.")
                body += table(["Item", "Classe", "Dano/s", "Vida efetiva"], rows)

        # ---- skills que nunca entram na rotação
        for c in classes:
            for sid in self.content.classes[c].skills:
                skill = self.content.skills[sid]
                if skill.type != "dash" and sid not in used_skills[c]:
                    self.alerts.append(f"**{skill.name}** nunca foi usada pela rotação de "
                                       f"{self.content.classes[c].name} (em nenhum nível testado).")

        header = [
            "# Relatório de balanceamento", "",
            f"Gerado em {time.strftime('%d/%m/%Y %H:%M')} · níveis: {', '.join(map(str, self.levels))} · "
            f"lutas de {num(self.seconds, 0)} s · equipamento: "
            f"{', '.join(self.content.items[i].name for i in self.equipment) or 'nenhum'} · "
            f"{num(time.time() - started, 1)} s para gerar", "",
            "Os números vêm de lutas simuladas no próprio motor do jogo. Todos os atributos no nível indicado; "
            "os lutadores ficam parados a 28 px, então alcance e esquiva **não** contam — classes de longe "
            "aparecem piores do que seriam com um jogador se movendo. *Vida efetiva* = vida ÷ multiplicador "
            "de dano recebido.", "",
            "## Alertas", "",
        ]
        alerts = self._grouped_alerts() + list(dict.fromkeys(self.alerts))
        alerts = alerts or ["Nenhum alerta."]
        return "\n".join(header + [f"- {a}" for a in alerts] + [""] + body)

    def _grouped_alerts(self) -> list[str]:
        out = []
        cls_name = lambda c: self.content.classes[c].name  # noqa: E731
        for (kind, who, what), values in self.grouped.items():
            share = lambda v: num(v * 100, 0) + "%"  # noqa: E731
            if kind == "dominance":
                out.append(f"**{self._source_name(what)}** responde pela maior parte do dano de **{cls_name(who)}**: "
                           f"{_levels(values, share)}. As outras skills pesam pouco.")
            elif kind == "low":
                out.append(f"**{self._source_name(what)}** rende pouco para {cls_name(who)}: {_levels(values, share)} "
                           "do dano (contando os efeitos que ela aplica).")
            elif kind == "loses":
                out.append(f"**{cls_name(who)}** perde para {self.content.enemies[what].name}: folga "
                           f"{_levels(values, num)}.")
            elif kind == "trivial":
                out.append(f"{self.content.enemies[what].name} não é desafio para {cls_name(who)}: folga "
                           f"{_levels(values, lambda v: num(v, 0) + '×')}. Faltam inimigos para esse nível.")
        return out

    def _has_utility(self, skill) -> bool:
        """Aplica efeito de controle/apoio (lento, atordoado, buff...), além de dano."""
        effects = [self.content.effects[a.effect] for a in skill.effects + skill.self_effects]
        return any(e.modifiers or e.flags for e in effects)

    def _source_name(self, source: str) -> str:
        if source.startswith("efeito:"):
            effect_id = source.split(":", 1)[1]
            effect = self.content.effects.get(effect_id)
            origin = [s.name for s in self.content.skills.values() if any(a.effect == effect_id for a in s.effects)]
            if effect and origin:
                return f"{effect.name} (de {' / '.join(origin)})"
            return f"{effect.name} (efeito)" if effect else source
        skill = self.content.skills.get(source)
        return skill.name if skill else source


def _levels(values: dict[int, float], fmt) -> str:  # type: ignore[no-untyped-def]
    return ", ".join(f"{fmt(v)} (nível {lv})" for lv, v in sorted(values.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relatório de balanceamento do jogo.")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 10, 25], help="níveis dos atributos (padrão: 1 10 25)")
    parser.add_argument("--seconds", type=float, default=30.0, help="duração de cada luta simulada (padrão: 30)")
    parser.add_argument("--equip", default="", help="equipamentos para todos, separados por vírgula")
    parser.add_argument("--out", type=Path, help="salva o relatório neste arquivo (Markdown)")
    parser.add_argument("--data", type=Path, default=C.DATA_DIR, help="pasta de dados (padrão: data/)")
    args = parser.parse_args(argv)

    try:
        content = Content.load(args.data)
    except ContentError as exc:
        print(f"Erro no conteúdo: {exc}", file=sys.stderr)
        return 1
    equipment = tuple(i.strip() for i in args.equip.split(",") if i.strip())
    for item_id in equipment:
        if item_id not in content.items or content.items[item_id].type != "equipment":
            print(f"Equipamento desconhecido: {item_id}", file=sys.stderr)
            return 1
    if any(level < 1 or level > C.MAX_STAT_LEVEL for level in args.levels):
        print(f"Níveis devem estar entre 1 e {C.MAX_STAT_LEVEL}.", file=sys.stderr)
        return 1

    text = Report(content, args.levels, args.seconds, equipment).generate()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Relatório salvo em {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
