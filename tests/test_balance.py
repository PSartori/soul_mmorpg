"""Testes da ferramenta de balanceamento (tools/)."""
import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path

from server import config as C
from server.content import ClassDef, Content
from tools.balance import Report, main
from tools.simulator import Build, Simulator

BASE = Content.load(C.DATA_DIR)
# Classe de teste que só tem o golpe básico: o dano/s esperado é fácil de calcular
ONLY_STRIKE = ClassDef(id="so_golpe", name="Só golpe", race="humano", description="", start_skills=("golpe",), train_rate={})
CONTENT = dataclasses.replace(BASE, classes={**BASE.classes, "so_golpe": ONLY_STRIKE}, trainers={})  # sem treinadores: só o golpe


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator(CONTENT)

    def test_strike_only_matches_formula(self):
        result = self.sim.dummy_output(Build("so_golpe", 1), seconds=20)
        golpe = CONTENT.skills["golpe"]
        damage = golpe.damage_base + golpe.damage_scale["strength"] * 1
        # A recarga é contada em ticks de 50 ms: 0,45 s vira 9 ou 10 ticks
        self.assertAlmostEqual(result.dps(result.a), damage / golpe.cooldown, delta=damage / golpe.cooldown * 0.15)
        self.assertEqual(set(result.a.by_source), {"golpe"})

    def test_stats_do_not_drift_during_fight(self):
        short = self.sim.dummy_output(Build("so_golpe", 1), seconds=5)
        long = self.sim.dummy_output(Build("so_golpe", 1), seconds=60)  # treina muito, mas fica congelado
        self.assertAlmostEqual(short.dps(short.a), long.dps(long.a), delta=1.0)

    def test_poison_is_attributed_to_effect(self):
        result = self.sim.dummy_output(Build("assombracao", 1), seconds=15)
        self.assertGreater(result.a.by_source.get("efeito:veneno", 0), 0)

    def test_enemy_fights_back(self):
        result = self.sim.fight(Build("aprendiz", 1), "bruto", seconds=10)
        self.assertGreater(result.b.damage, 0)  # o bruto bateu
        self.assertGreater(result.a.damage, 0)
        self.assertLess(result.time_to_kill(result.a, result.b), float("inf"))

    def test_pvp_is_symmetric_for_mirror(self):
        result = self.sim.fight(Build("aprendiz", 5), Build("aprendiz", 5), seconds=10)
        self.assertAlmostEqual(result.dps(result.a), result.dps(result.b), delta=result.dps(result.a) * 0.15)

    def test_equipment_and_restrictions(self):
        plain = self.sim.profile(Build("aprendiz", 1))
        armored = self.sim.profile(Build("aprendiz", 1, ("gibao_de_couro",)))
        self.assertGreater(armored["effective_hp"], plain["effective_hp"])
        staff = self.sim.profile(Build("aprendiz", 1, ("cajado_de_foco",)))  # só desperto
        self.assertEqual(staff["skipped"], ["cajado_de_foco"])

    def test_higher_level_hits_harder(self):
        low = self.sim.dummy_output(Build("aprendiz", 1), seconds=10)
        high = self.sim.dummy_output(Build("aprendiz", 10), seconds=10)
        self.assertGreater(high.dps(high.a), low.dps(low.a))


class ReportTests(unittest.TestCase):
    def test_report_sections_and_alerts(self):
        text = Report(BASE, [1, 10], 5.0, ()).generate()
        for section in ("## Alertas", "## Nível 1", "## Nível 10", "### Classes", "### PvE", "### PvP", "### Equipamentos"):
            self.assertIn(section, text)
        own_level = text.split("## Inimigos no próprio nível")[1]  # cada inimigo testado no nível dele
        for enemy in BASE.enemies.values():
            label = f"{enemy.name} (chefe)" if enemy.boss else enemy.name
            self.assertIn(f"| {label} | {enemy.level} |", own_level)
        self.assertIn("(chefe)", own_level)
        self.assertIn("(elite)", own_level)

    def test_cli_writes_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "relatorio.md"
            self.assertEqual(main(["--levels", "1", "--seconds", "3", "--out", str(out)]), 0)
            self.assertIn("# Relatório de balanceamento", out.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp)

    def test_cli_rejects_bad_input(self):
        self.assertEqual(main(["--equip", "espada_de_ouro", "--levels", "1", "--seconds", "1"]), 1)
        self.assertEqual(main(["--levels", "0", "--seconds", "1"]), 1)


if __name__ == "__main__":
    unittest.main()
