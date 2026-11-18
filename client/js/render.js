// Desenho no canvas. Arte provisória feita só com formas, até termos sprites.
import { RARITY_COLORS } from "./inventory.js";

const DIR = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
const FACING_ANGLE = { right: 0, down: Math.PI / 2, left: Math.PI, up: -Math.PI / 2 };
const TAU = Math.PI * 2;

const TILE_BASE = {
  "#": "#3a3d47",
  ".": "#4f6b34",
  ",": "#46622d",
  "~": "#2b5578",
  ":": "#8b8474",
  "T": "#4f6b34",
};

// Número pseudoaleatório estável por tile (para variar detalhes sem "piscar")
function tileNoise(x, y) {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function drawTile(g, ch, x, y, ts, r) {
  g.fillStyle = TILE_BASE[ch] ?? "#ff00ff";
  g.fillRect(x, y, ts, ts);
  switch (ch) {
    case ".":
      if (r < 0.4) {
        g.fillStyle = "rgba(255,255,255,0.05)";
        g.fillRect(x + 4 + r * 50, y + 6 + ((r * 977) % 1) * 18, 3, 3);
      }
      break;
    case ",":
      g.strokeStyle = "rgba(20,40,10,0.7)";
      g.lineWidth = 1;
      for (let i = 0; i < 4; i++) {
        const bx = x + 5 + ((r * (i + 1) * 53) % 1) * (ts - 10);
        const by = y + 6 + ((r * (i + 3) * 31) % 1) * (ts - 14);
        g.beginPath();
        g.moveTo(bx, by + 7); g.lineTo(bx - 2, by);
        g.moveTo(bx, by + 7); g.lineTo(bx + 2, by + 1);
        g.stroke();
      }
      break;
    case "~": {
      g.strokeStyle = "rgba(255,255,255,0.16)";
      g.lineWidth = 1;
      const wy = y + 8 + r * 14;
      g.beginPath();
      g.moveTo(x + 4, wy);
      g.quadraticCurveTo(x + ts / 2, wy - 4, x + ts - 4, wy);
      g.stroke();
      break;
    }
    case ":":
      g.strokeStyle = "rgba(0,0,0,0.14)";
      g.strokeRect(x + 0.5, y + 0.5, ts - 1, ts - 1);
      break;
    case "#":
      g.fillStyle = "#4a4e5a";
      g.fillRect(x, y, ts, ts * 0.35);
      g.strokeStyle = "rgba(0,0,0,0.35)";
      g.strokeRect(x + 0.5, y + 0.5, ts - 1, ts - 1);
      break;
    case "T":
      g.fillStyle = "#5a3d24";
      g.fillRect(x + ts / 2 - 3, y + ts * 0.55, 6, ts * 0.4);
      g.fillStyle = "#2c4a22";
      g.beginPath(); g.arc(x + ts / 2, y + ts * 0.42, ts * 0.42, 0, TAU); g.fill();
      g.fillStyle = "rgba(255,255,255,0.08)";
      g.beginPath(); g.arc(x + ts / 2 - 4, y + ts * 0.34, ts * 0.18, 0, TAU); g.fill();
      break;
  }
}

// Título do cargo: "Arauto 3", "Comandante"...
export function rankTitle(defs, o) {
  if (!o) return "";
  const [orgId, rankId, seat] = o;
  const rank = defs.orgs?.[orgId]?.ranks?.[rankId];
  if (!rank) return "";
  return rank.numbered && seat ? `${rank.name} ${seat}` : rank.name;
}

// Cor do nome do inimigo pela diferença de nível para você: cinza (fácil) até vermelho (perigoso)
export function levelColor(enemyLevel, myLevel) {
  const diff = enemyLevel - myLevel;
  if (diff <= -6) return "#9a9aa2";
  if (diff <= -3) return "#8fe08f";
  if (diff <= 2) return "#f0e6c8";
  if (diff <= 5) return "#ffb060";
  return "#ff6060";
}

export function nameColor(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360} 55% 55%)`;
}

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.mapLayer = null;
    this.defs = { skills: {}, effects: {}, enemies: {} }; // definições vindas do servidor
  }

  // O mapa é estático: desenha uma vez numa camada separada e só copia a cada frame
  setMap(map) {
    const ts = map.tile_size;
    const layer = document.createElement("canvas");
    layer.width = map.rows[0].length * ts;
    layer.height = map.rows.length * ts;
    const g = layer.getContext("2d");
    map.rows.forEach((row, ty) => {
      [...row].forEach((ch, tx) => drawTile(g, ch, tx * ts, ty * ts, ts, tileNoise(tx, ty)));
    });
    // Áreas PvP: tom avermelhado, borda tracejada e o nome no canto
    for (const zone of map.zones ?? []) {
      if (zone.type !== "pvp") continue;
      const [x0, y0, x1, y1] = zone.area;
      const x = x0 * ts, y = y0 * ts, w = (x1 - x0 + 1) * ts, h = (y1 - y0 + 1) * ts;
      g.fillStyle = "rgba(200, 50, 50, 0.13)";
      g.fillRect(x, y, w, h);
      g.strokeStyle = "rgba(255, 110, 110, 0.75)";
      g.lineWidth = 2;
      g.setLineDash([8, 6]);
      g.strokeRect(x + 1, y + 1, w - 2, h - 2);
      g.setLineDash([]);
      g.font = "bold 12px system-ui, sans-serif";
      g.textAlign = "left";
      g.fillStyle = "rgba(0,0,0,0.6)";
      g.fillText(`PvP · ${zone.name}`, x + 7, y + 17);
      g.fillStyle = "#ffb0b0";
      g.fillText(`PvP · ${zone.name}`, x + 6, y + 16);
    }
    // Portais: placa com o destino (o brilho animado é desenhado a cada quadro)
    for (const portal of map.portals ?? []) {
      const [x0, y0, x1] = portal.area;
      const label = `→ ${portal.title ?? portal.to}`;
      g.font = "bold 12px system-ui, sans-serif";
      g.textAlign = "center";
      const cx = ((x0 + x1 + 1) / 2) * ts;
      g.fillStyle = "rgba(0,0,0,0.7)";
      g.fillText(label, cx + 1, y0 * ts - 5);
      g.fillStyle = "#a8e4ff";
      g.fillText(label, cx, y0 * ts - 6);
    }
    this.map = map;
    this.mapLayer = layer;
  }

  drawPortals(now) {
    const { ctx } = this;
    const ts = this.map.tile_size;
    for (const portal of this.map.portals ?? []) {
      const [x0, y0, x1, y1] = portal.area;
      const x = x0 * ts, y = y0 * ts, w = (x1 - x0 + 1) * ts, h = (y1 - y0 + 1) * ts;
      const pulse = 0.5 + 0.5 * Math.sin(now / 350);
      const glow = ctx.createRadialGradient(x + w / 2, y + h / 2, 2, x + w / 2, y + h / 2, Math.max(w, h) * 0.75);
      glow.addColorStop(0, `rgba(170, 230, 255, ${0.55 + 0.25 * pulse})`);
      glow.addColorStop(1, "rgba(80, 140, 255, 0)");
      ctx.fillStyle = glow;
      ctx.fillRect(x - 8, y - 8, w + 16, h + 16);
      ctx.strokeStyle = `rgba(170, 230, 255, ${0.6 + 0.3 * pulse})`;
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 2, y + 2, w - 4, h - 4);
    }
  }

  draw(state, now) {
    const { ctx, canvas, mapLayer } = this;
    ctx.fillStyle = "#0b0c10";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (!mapLayer) return;

    const me = state.ents.get(state.myId);
    const cam = (center, view, size) =>
      size <= view ? (size - view) / 2 : Math.min(Math.max(center - view / 2, 0), size - view);
    const camX = Math.round(cam(me ? me.rx : 0, canvas.width, mapLayer.width));
    const camY = Math.round(cam(me ? me.ry : 0, canvas.height, mapLayer.height));
    this.camX = camX;
    this.camY = camY;

    ctx.save();
    ctx.translate(-camX, -camY);
    ctx.drawImage(mapLayer, 0, 0);
    this.drawPortals(now);

    for (const d of state.drops) this.drawDrop(d, now);
    for (const t of state.trails) this.drawTrail(t, now);
    const ents = [...state.ents.values()].sort((a, b) => a.ry - b.ry);
    this.questMarks = state.questMarks ?? {};
    this.myLevel = state.myLevel ?? 1;
    for (const e of ents) this.drawEntity(e, e.id === state.myId, now);
    for (const s of state.swings) this.drawSwing(s, state, now);
    for (const r of state.rings) this.drawRing(r, now);
    for (const p of state.projs) this.drawProjectile(p, now - state.snapTime);
    for (const f of state.floaters) this.drawFloater(f, now);
    ctx.restore();

    if (me && me.s === "dead") {
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#fff";
      ctx.font = "bold 24px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("Você foi derrotado…", canvas.width / 2, canvas.height / 2);
    }
  }

  // NPC: corpo de losango arredondado na cor dele, balão de fala, nome e título (sem barra de vida)
  drawNpc(e, now) {
    const { ctx } = this;
    const def = this.defs.npcs?.[e.t] ?? { name: e.n, title: "", color: "#e0c070" };
    const x = e.rx;
    const y = e.ry;
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath(); ctx.ellipse(x, y + 10, 10, 4, 0, 0, TAU); ctx.fill();

    ctx.fillStyle = def.color;
    ctx.strokeStyle = "rgba(0,0,0,0.65)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y - 13); ctx.lineTo(x + 11, y); ctx.lineTo(x, y + 11); ctx.lineTo(x - 11, y);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath(); ctx.arc(x, y + 4, 2.5, 0, TAU); ctx.fill(); // olha para baixo

    const mark = this.questMarks?.[e.id];
    if (mark) {
      // missão: "!" amarelo (tem missão para você) ou "?" (pronta para entregar), pulsando acima da cabeça
      const my = y - (def.title ? 50 : 40) + Math.sin(now / 250) * 2;
      ctx.font = "bold 18px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(0,0,0,0.8)";
      ctx.strokeText(mark, x, my);
      ctx.fillStyle = mark === "?" ? "#7ee07e" : "#ffd84a";
      ctx.fillText(mark, x, my);
    } else {
      // balão de fala, flutuando devagar
      const bx = x + 13;
      const by = y - 22 + Math.sin(now / 400 + e.id) * 1.5;
      ctx.fillStyle = "rgba(255, 248, 220, 0.95)";
      ctx.strokeStyle = "rgba(0,0,0,0.55)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(bx, by, 8, 6, 0, 0, TAU);
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#4a3b10";
      for (let i = -1; i <= 1; i++) ctx.fillRect(bx + i * 3 - 1, by - 1, 2, 2);
    }

    ctx.textAlign = "center";
    ctx.font = "bold 11px system-ui, sans-serif";
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillText(def.name, x + 1, y - 21);
    ctx.fillStyle = "#ffe9b0";
    ctx.fillText(def.name, x, y - 22);
    if (def.title) {
      ctx.font = "10px system-ui, sans-serif";
      ctx.fillStyle = "rgba(0,0,0,0.8)";
      ctx.fillText(`‹${def.title}›`, x + 1, y - 33);
      ctx.fillStyle = "#d8c8a0";
      ctx.fillText(`‹${def.title}›`, x, y - 34);
    }
  }

  drawEntity(e, isMe, now) {
    if (e.k === "npc") {
      this.drawNpc(e, now);
      return;
    }
    const { ctx } = this;
    const x = e.rx;
    const y = e.ry;

    if (e.s === "dead") {
      if (e.k === "player") {
        ctx.globalAlpha = 0.3;
        ctx.fillStyle = "#cfd3dc";
        ctx.beginPath(); ctx.arc(x, y, 11, 0, TAU); ctx.fill();
        ctx.globalAlpha = 1;
      }
      return;
    }

    // sombra
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath(); ctx.ellipse(x, y + 10, 10, 4, 0, 0, TAU); ctx.fill();

    // aura de meditação
    if (e.s === "meditate") {
      ctx.strokeStyle = "rgba(130,200,255,0.85)";
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 17 + Math.sin(now / 180) * 3, 0, TAU); ctx.stroke();
    }

    // em duelo: anel vermelho
    if (e.du) {
      ctx.strokeStyle = "rgba(255, 80, 80, 0.9)";
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 20, 0, TAU); ctx.stroke();
    }

    // efeitos de status: um anel tracejado girando por efeito, na cor dele
    (e.ef ?? []).forEach((effectId, i) => {
      const color = this.defs.effects[effectId]?.color ?? "#ffffff";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.lineDashOffset = -now / 40;
      ctx.beginPath(); ctx.arc(x, y, 15 + i * 4, 0, TAU); ctx.stroke();
      ctx.setLineDash([]);
    });

    // corpo
    ctx.lineWidth = 2;
    const k = (e.h ?? 10) / 10; // escala do corpo (chefes e elites são maiores)
    if (e.k === "enemy") {
      if (e.el || e.boss) {
        // elite: anel dourado tracejado; chefe: anel vermelho largo
        ctx.strokeStyle = e.boss ? "rgba(255, 90, 90, 0.85)" : "rgba(255, 214, 90, 0.9)";
        ctx.lineWidth = e.boss ? 3 : 2;
        ctx.setLineDash(e.boss ? [] : [5, 4]);
        ctx.lineDashOffset = now / 60;
        ctx.beginPath(); ctx.arc(x, y, 16 * k, 0, TAU); ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineWidth = 2;
      }
      ctx.fillStyle = this.defs.enemies[e.t]?.color ?? "#6d2f7d";
      ctx.strokeStyle = "rgba(0,0,0,0.6)";
      ctx.beginPath();
      for (let i = 0; i < 10; i++) {
        const a = (i / 10) * TAU + now / 900;
        const r = (i % 2 ? 8 : 13) * k;
        ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r);
      }
      ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#ff6b6b";
      ctx.fillRect(x - 4 * k, y - 3 * k, 3 * k, 3 * k);
      ctx.fillRect(x + 1 * k, y - 3 * k, 3 * k, 3 * k);
    } else {
      ctx.fillStyle = nameColor(e.n);
      ctx.strokeStyle = isMe ? "#ffffff" : "rgba(0,0,0,0.6)";
      ctx.beginPath(); ctx.arc(x, y, 11, 0, TAU); ctx.fill(); ctx.stroke();
    }

    // direção para onde olha
    const [dx, dy] = DIR[e.f] ?? DIR.down;
    ctx.fillStyle = "rgba(255,255,255,0.9)";
    ctx.beginPath(); ctx.arc(x + dx * 7, y + dy * 7, 2.5, 0, TAU); ctx.fill();

    if (e.s === "rest") {
      ctx.fillStyle = "#cfd8ff";
      ctx.font = "bold 12px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText("z", x + 10, y - 10 - ((now / 400) % 1) * 8);
    }

    // barra de vida e nome (acima do corpo, que pode ser maior que o normal)
    const top = y - 21 - (k - 1) * 12;
    const w = 28 * Math.max(1, k * 0.8);
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(x - w / 2, top, w, 4);
    ctx.fillStyle = e.k === "enemy" ? "#d9534f" : "#5cb85c";
    ctx.fillRect(x - w / 2, top, w * Math.max(0, Math.min(1, e.hp / e.mhp)), 4);

    ctx.font = e.boss ? "bold 12px system-ui, sans-serif" : "11px system-ui, sans-serif";
    const role = e.st ? this.defs.roles?.[e.st] : null;
    const tag = role ? `[${role.tag}] ` : "";
    // inimigos: "★ Nome · Nv 20" (★ = elite), na cor da dificuldade para você
    const label = e.k === "enemy" ? `${e.el ? "★ " : ""}${e.n}${e.lv ? ` · Nv ${e.lv}` : ""}` : e.n;
    const left = x - ctx.measureText(tag + label).width / 2;
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillText(tag + label, left + 1, top - 4);
    if (role) {
      ctx.fillStyle = role.color;  // selo da equipe, antes do nome
      ctx.fillText(tag, left, top - 5);
    }
    ctx.fillStyle = isMe ? "#ffe08a" : e.k === "enemy" ? levelColor(e.lv ?? 1, this.myLevel) : "#ffffff";
    ctx.fillText(label, left + ctx.measureText(tag).width, top - 5);
    ctx.textAlign = "center";

    // cargo na organização, na cor dela
    const title = rankTitle(this.defs, e.o);
    if (title) {
      ctx.font = "10px system-ui, sans-serif";
      ctx.fillStyle = "rgba(0,0,0,0.8)";
      ctx.fillText(title, x + 1, y - 37);
      ctx.fillStyle = this.defs.orgs?.[e.o[0]]?.color ?? "#d8dbe2";
      ctx.fillText(title, x, y - 38);
    }
  }

  drawSwing(s, state, now) {
    const e = state.ents.get(s.id);
    if (!e) return;
    const t = (now - s.born) / s.ttl;
    const a = FACING_ANGLE[e.f] ?? 0;
    const { ctx } = this;
    ctx.strokeStyle = `rgba(255,255,255,${0.8 * (1 - t)})`;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(e.rx, e.ry, s.r * 0.8, a - 0.9 + t * 0.4, a + 0.9 + t * 0.4);
    ctx.stroke();
  }

  drawDrop(d, now) {
    const { ctx } = this;
    const bob = Math.sin(now / 300 + d.id) * 2;
    const x = d.x;
    const y = d.y + bob;
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath(); ctx.ellipse(d.x, d.y + 7, 6, 2.5, 0, 0, TAU); ctx.fill();
    if (d.i === "gold") {
      ctx.fillStyle = "#ffd65a";
      ctx.strokeStyle = "#a07a10";
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, 5, 0, TAU); ctx.fill(); ctx.stroke();
      return;
    }
    const rarity = this.defs.items?.[d.i]?.rarity ?? "common";
    const color = RARITY_COLORS[rarity];
    const glow = ctx.createRadialGradient(x, y, 0, x, y, 12);
    glow.addColorStop(0, color);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.globalAlpha = 0.45;
    ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(x, y, 12, 0, TAU); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = color;
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, y - 6); ctx.lineTo(x + 5, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 5, y);
    ctx.closePath(); ctx.fill(); ctx.stroke();
  }

  drawRing(r, now) {
    const t = (now - r.born) / r.ttl;
    const { ctx } = this;
    ctx.globalAlpha = Math.max(0, 1 - t);
    ctx.strokeStyle = r.color;
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(r.x, r.y, r.r * (0.4 + 0.6 * t), 0, TAU); ctx.stroke();
    ctx.globalAlpha = 1;
  }

  drawTrail(tr, now) {
    const t = (now - tr.born) / tr.ttl;
    const { ctx } = this;
    ctx.strokeStyle = `rgba(220,235,255,${0.5 * (1 - t)})`;
    ctx.lineWidth = 14;
    ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(tr.x0, tr.y0); ctx.lineTo(tr.x1, tr.y1); ctx.stroke();
    ctx.lineCap = "butt";
  }

  drawProjectile(p, msSinceSnap) {
    // extrapola pela velocidade para o movimento ficar liso entre snapshots
    const t = Math.min(msSinceSnap, 100) / 1000;
    const x = p.x + p.vx * t;
    const y = p.y + p.vy * t;
    const color = this.defs.skills[p.s]?.color ?? "#8cc8ff";
    const { ctx } = this;
    const glow = ctx.createRadialGradient(x, y, 0, x, y, 12);
    glow.addColorStop(0, "#ffffff");
    glow.addColorStop(0.35, color);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(x, y, 12, 0, TAU); ctx.fill();
  }

  drawFloater(f, now) {
    const t = (now - f.born) / f.ttl;
    const { ctx } = this;
    ctx.globalAlpha = Math.max(0, 1 - t);
    ctx.font = "bold 13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillText(f.text, f.x + 1, f.y - 30 - t * 28 + 1);
    ctx.fillStyle = f.color;
    ctx.fillText(f.text, f.x, f.y - 30 - t * 28);
    ctx.globalAlpha = 1;
  }
}

// Converte um clique na tela para coordenadas do mundo (o canvas pode estar redimensionado pelo CSS)
export function screenToWorld(renderer, ev) {
  const rect = renderer.canvas.getBoundingClientRect();
  const sx = (ev.clientX - rect.left) * (renderer.canvas.width / rect.width);
  const sy = (ev.clientY - rect.top) * (renderer.canvas.height / rect.height);
  return { x: sx + (renderer.camX ?? 0), y: sy + (renderer.camY ?? 0) };
}
