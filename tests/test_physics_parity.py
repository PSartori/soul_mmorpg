"""Garante que a previsão de movimento do cliente (client/js/physics.js) calcula
exatamente o mesmo que o servidor. Precisa do Node instalado; sem ele, o teste é pulado."""
import json
import random
import shutil
import subprocess
import unittest

from server import config as C
from server.content import Content
from server.db import Database
from server.stats import Stat
from server.tilemap import load_map
from server.world import World

DT = 1.0 / C.TICK_RATE
PHYSICS_JS = (C.CLIENT_DIR / "js" / "physics.js").as_uri()

NODE_SCRIPT = """
const { Physics } = await import(process.argv[1]);
const data = JSON.parse(require("fs").readFileSync(0, "utf8"));
const physics = new Physics(data.map, data.half, data.tick_rate);
const pos = { x: data.start[0], y: data.start[1], f: "down" };
const out = [];
for (const keys of data.commands) {
  physics.step(pos, keys, data.speed);
  out.push([pos.x, pos.y, pos.f]);
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node não instalado")
class PhysicsParityTests(unittest.TestCase):
    def test_random_walk_matches_server(self):
        content = Content.load(C.DATA_DIR)
        world = World(load_map(C.DEFAULT_MAP, content.enemy_by_mark), Database(":memory:"), content)
        world.enemies.clear()
        player, _ = world.login("Paridade", "1234")
        # Agilidade no máximo: a velocidade não muda no meio do teste (e fica alta, testando mais colisões)
        player.stats["agility"] = Stat(level=C.MAX_STAT_LEVEL)
        speed = player.speed
        rng = random.Random(42)

        commands = []
        for _ in range(60):  # trechos de 5 a 30 ticks na mesma direção, inclusive diagonais
            keys = {k: rng.random() < 0.35 for k in ("up", "down", "left", "right")}
            commands += [keys] * rng.randint(5, 30)

        start = (player.x, player.y)
        expected = []
        for seq, keys in enumerate(commands, start=1):
            world.handle_message(player, {"t": "move", "seq": seq, **keys})
            world.step(DT)
            expected.append([player.x, player.y, player.facing])

        payload = {
            "map": {"rows": world.map.rows, "tile_size": world.map.tile_size},
            "half": player.half,
            "tick_rate": C.TICK_RATE,
            "speed": speed,
            "start": start,
            "commands": commands,
        }
        result = subprocess.run(
            ["node", "--input-type=commonjs", "-e", f"(async () => {{ {NODE_SCRIPT} }})()", PHYSICS_JS],
            input=json.dumps(payload), capture_output=True, text=True, check=True,
        )
        actual = json.loads(result.stdout)

        self.assertEqual(player.speed, speed)
        self.assertEqual(len(actual), len(expected))
        for i, (a, e) in enumerate(zip(actual, expected)):
            self.assertEqual(a, e, f"diverge no comando {i + 1}")
        self.assertGreater(abs(player.x - start[0]) + abs(player.y - start[1]), 100)  # andou de verdade


if __name__ == "__main__":
    unittest.main()
