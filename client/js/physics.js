// Previsão de movimento: cópia fiel da colisão de server/tilemap.py (move_box) e do
// movimento de server/world.py (_move_player e _face). O servidor continua sendo a
// autoridade; isto só deixa o próprio personagem responder na hora, sem esperar a rede.
// Se mudar a lógica lá, mude aqui também.

const WALK_BLOCKERS = new Set(["#", "~", "T"]);
const EPS = 1e-3;
const DIAGONAL = Math.sqrt(0.5);

export class Physics {
  constructor(map, half, tickRate) {
    this.rows = map.rows;
    this.ts = map.tile_size;
    this.w = map.rows[0].length;
    this.h = map.rows.length;
    this.half = half;
    this.dt = 1.0 / tickRate;
  }

  tileAt(tx, ty) {
    if (ty >= 0 && ty < this.h && tx >= 0 && tx < this.w) return this.rows[ty][tx];
    return "#";
  }

  boxBlocked(x, y) {
    const { ts, half } = this;
    const x0 = Math.floor((x - half) / ts);
    const x1 = Math.floor((x + half - 1e-6) / ts);
    const y0 = Math.floor((y - half) / ts);
    const y1 = Math.floor((y + half - 1e-6) / ts);
    for (let ty = y0; ty <= y1; ty++) {
      for (let tx = x0; tx <= x1; tx++) {
        if (WALK_BLOCKERS.has(this.tileAt(tx, ty))) return true;
      }
    }
    return false;
  }

  moveAxis(pos, other, d, horizontal) {
    if (!d) return pos;
    const blocked = (p) => (horizontal ? this.boxBlocked(p, other) : this.boxBlocked(other, p));
    let next = pos + d;
    if (!blocked(next)) return next;
    const { ts, half } = this;
    if (d > 0) next = Math.floor((next + half) / ts) * ts - half - EPS;
    else next = (Math.floor((next - half) / ts) + 1) * ts + half + EPS;
    if ((d > 0 && next < pos) || (d < 0 && next > pos) || blocked(next)) return pos;
    return next;
  }

  // Aplica um comando de movimento (um tick) em pos = {x, y, f}.
  step(pos, keys, speed) {
    const dx = (keys.right ? 1 : 0) - (keys.left ? 1 : 0);
    const dy = (keys.down ? 1 : 0) - (keys.up ? 1 : 0);
    if ((!dx && !dy) || speed <= 0) return;
    if (Math.abs(dx) > Math.abs(dy)) pos.f = dx > 0 ? "right" : "left";
    else if (dy) pos.f = dy > 0 ? "down" : "up";
    const scale = speed * this.dt * (dx && dy ? DIAGONAL : 1.0);
    pos.x = this.moveAxis(pos.x, pos.y, dx * scale, true);
    pos.y = this.moveAxis(pos.y, pos.x, dy * scale, false);
  }
}
