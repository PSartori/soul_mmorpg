// Minimapa: o mapa inteiro em miniatura no canto da tela, com você, o grupo, outros
// jogadores, NPCs (com as marcas de missão ! e ?), inimigos por perto, portais e áreas PvP.
// Tecla N alterna: pequeno → grande → escondido. Clique no minimapa: pequeno ↔ grande.
// O jogo lembra a escolha (localStorage).
//
// De onde vem cada coisa:
//   - terreno, portais, áreas PvP e NPCs: mensagem do mapa (welcome / map), que não muda;
//   - jogadores e inimigos: o snapshot (só o que está perto, até VIEW_RADIUS);
//   - membros do grupo no mesmo mapa: you.mates ([id, x, y]), mesmo quando estão longe.
import { PARTY_NAME_COLOR, TILE_BASE } from "./render.js";

const MODES = ["small", "large", "off"];
const STORAGE_KEY = "rpg.minimap";
const SMALL = { w: 200, h: 150 }; // tamanho máximo do minimapa pequeno (px de tela)
const TILE_PX = 4; // px por tile na camada do terreno (depois é escalada)
const COLORS = {
  me: "#ffffff", // você: branco com contorno amarelo (os NPCs são dourados)
  player: "#8fb4ff",
  party: PARTY_NAME_COLOR,
  enemy: "#ff5a5a",
  npc: "#ffd27a",
  portal: "#7fd8ff",
};

function savedMode() {
  try {
    const mode = localStorage.getItem(STORAGE_KEY);
    return MODES.includes(mode) ? mode : "small";
  } catch {
    return "small";
  }
}

export class Minimap {
  constructor({ root, canvas, title, getDefs }) {
    this.root = root;
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.titleEl = title;
    this.getDefs = getDefs;
    this.map = null;
    this.layer = null; // terreno + PvP + portais, desenhado uma vez por mapa
    this.scale = 1; // px de tela por px do mundo
    this.lastDraw = 0;
    this.mode = savedMode();
    this.root.addEventListener("click", () => this.setMode(this.mode === "large" ? "small" : "large"));
    window.addEventListener("resize", () => this.resize());
    this.apply();
  }

  get isLarge() {
    return this.mode === "large";
  }

  // Tecla N: pequeno → grande → escondido → pequeno
  cycle() {
    this.setMode(MODES[(MODES.indexOf(this.mode) + 1) % MODES.length]);
  }

  setMode(mode) {
    this.mode = mode;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // sem armazenamento: só não lembra a escolha
    }
    this.apply();
  }

  apply() {
    this.root.hidden = this.mode === "off" || !this.map;
    this.root.classList.toggle("large", this.isLarge);
    this.root.title = this.isLarge ? "Clique para diminuir (N esconde)" : "Clique para ampliar (N alterna)";
    this.resize();
  }

  // O mapa mudou (entrou no jogo, atravessou um portal, a equipe recarregou o conteúdo)
  setMap(map) {
    this.map = map;
    this.titleEl.textContent = map.title ?? map.id;
    const cols = map.rows[0].length;
    const rows = map.rows.length;
    const layer = document.createElement("canvas");
    layer.width = cols * TILE_PX;
    layer.height = rows * TILE_PX;
    const g = layer.getContext("2d");
    map.rows.forEach((row, ty) => {
      [...row].forEach((ch, tx) => {
        g.fillStyle = TILE_BASE[ch] ?? TILE_BASE["."];
        g.fillRect(tx * TILE_PX, ty * TILE_PX, TILE_PX, TILE_PX);
        if (ch === "T") { // árvore: copa escura no meio do tile
          g.fillStyle = "#2c4a22";
          g.fillRect(tx * TILE_PX + 1, ty * TILE_PX + 1, TILE_PX - 2, TILE_PX - 2);
        } else if (ch === "#") { // parede um pouco mais clara, para destacar o contorno
          g.fillStyle = "#555a68";
          g.fillRect(tx * TILE_PX, ty * TILE_PX, TILE_PX, 1);
        }
      });
    });
    const rect = ([x0, y0, x1, y1]) => [x0 * TILE_PX, y0 * TILE_PX, (x1 - x0 + 1) * TILE_PX, (y1 - y0 + 1) * TILE_PX];
    for (const zone of map.zones ?? []) {
      if (zone.type !== "pvp") continue;
      const [x, y, w, h] = rect(zone.area);
      g.fillStyle = "rgba(220, 60, 60, 0.28)";
      g.fillRect(x, y, w, h);
      g.strokeStyle = "rgba(255, 110, 110, 0.9)";
      g.lineWidth = 1;
      g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    }
    for (const portal of map.portals ?? []) {
      const [x, y, w, h] = rect(portal.area);
      g.fillStyle = COLORS.portal;
      g.fillRect(x, y, w, h);
    }
    this.layer = layer;
    this.apply();
  }

  // Tamanho na tela: o pequeno cabe em SMALL; o grande, em 80% da área do jogo
  resize() {
    if (!this.map || this.root.hidden) return;
    const ts = this.map.tile_size;
    const worldW = this.map.rows[0].length * ts;
    const worldH = this.map.rows.length * ts;
    const box = this.root.parentElement.getBoundingClientRect();
    // pequeno: até SMALL, sem passar de 25% da área do jogo (telas pequenas)
    let maxW = Math.max(100, Math.min(SMALL.w, box.width * 0.25));
    let maxH = Math.max(75, Math.min(SMALL.h, box.height * 0.25));
    if (this.isLarge) {
      maxW = Math.max(SMALL.w, box.width * 0.8);
      maxH = Math.max(SMALL.h, box.height * 0.8 - 24);
    }
    this.scale = Math.min(maxW / worldW, maxH / worldH);
    const cssW = Math.round(worldW * this.scale);
    const cssH = Math.round(worldH * this.scale);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.style.width = `${cssW}px`;
    this.canvas.style.height = `${cssH}px`;
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    this.dpr = dpr;
    this.lastDraw = 0;
  }

  // Chamado a cada quadro; redesenha no máximo 10 vezes por segundo
  draw(state, now, pred) {
    if (this.root.hidden || !this.layer || now - this.lastDraw < 100) return;
    this.lastDraw = now;
    const { ctx, scale } = this;
    const large = this.isLarge;
    const defs = this.getDefs();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.drawImage(this.layer, 0, 0, this.layer.width, this.layer.height,
      0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);

    const at = (x, y) => [x * scale, y * scale];
    const dot = (x, y, r, fill, stroke = "rgba(0,0,0,0.8)") => {
      const [sx, sy] = at(x, y);
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = stroke;
      ctx.stroke();
    };
    const cssW = this.canvas.width / this.dpr;
    const label = (text, x, y, color, dy) => {
      if (!text) return;
      const [sx, sy] = at(x, y);
      ctx.font = "bold 11px system-ui, sans-serif";
      ctx.textAlign = "center";
      // não deixa o texto sair pelas bordas do minimapa
      const half = ctx.measureText(text).width / 2;
      const tx = Math.min(Math.max(sx, half + 2), cssW - half - 2);
      const ty = Math.max(sy + dy, 11);
      ctx.fillStyle = "rgba(0,0,0,0.85)";
      ctx.fillText(text, tx + 1, ty + 1);
      ctx.fillStyle = color;
      ctx.fillText(text, tx, ty);
    };
    const k = large ? 1.6 : 1; // marcadores maiores no mapa grande

    // portais: no mapa grande, o nome do destino
    if (large) {
      const ts = this.map.tile_size;
      for (const portal of this.map.portals ?? []) {
        const [x0, y0, x1] = portal.area;
        label(`→ ${portal.title ?? portal.to}`, ((x0 + x1 + 1) / 2) * ts, y0 * ts, COLORS.portal, -4);
      }
    }

    // inimigos que estão por perto (vêm no snapshot)
    for (const e of state.ents.values()) {
      if (e.k !== "enemy" || e.s === "dead") continue;
      dot(e.rx, e.ry, (e.boss ? 4 : 2) * k, COLORS.enemy, e.el ? "#ffd84a" : "rgba(0,0,0,0.8)");
    }

    // NPCs (todos os do mapa) e as marcas de missão
    for (const npc of this.map.npcs ?? []) {
      dot(npc.x, npc.y, 2.5 * k, COLORS.npc);
      const mark = state.questMarks?.[npc.id];
      if (mark) label(mark, npc.x, npc.y, mark === "?" ? "#7ee07e" : "#ffd84a", -4 * k);
      if (large) label(defs.npcs?.[npc.t]?.name ?? "", npc.x, npc.y, "#ffe9b0", 14);
    }

    // outros jogadores; os do grupo em verde, mesmo longe (you.mates)
    const drawn = new Set();
    for (const e of state.ents.values()) {
      if (e.k !== "player" || e.id === state.myId || e.s === "dead") continue;
      const inParty = state.partyIds?.has(e.id);
      dot(e.rx, e.ry, 2.5 * k, inParty ? COLORS.party : COLORS.player);
      if (large && inParty) label(e.n, e.rx, e.ry, COLORS.party, -5 * k);
      drawn.add(e.id);
    }
    for (const [id, x, y] of state.mates ?? []) {
      if (!drawn.has(id)) dot(x, y, 2.5 * k, COLORS.party);
    }

    // você: ponto maior, com um traço para onde olha
    const me = state.ents.get(state.myId);
    if (me || pred) {
      const x = pred?.x ?? me.rx;
      const y = pred?.y ?? me.ry;
      const [sx, sy] = at(x, y);
      const dir = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[pred?.f ?? me?.f] ?? [0, 1];
      ctx.strokeStyle = "#ffd84a";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(sx + dir[0] * 8 * k, sy + dir[1] * 8 * k);
      ctx.stroke();
      dot(x, y, 3.5 * k, COLORS.me, "#000");
      ctx.beginPath(); // contorno amarelo, para achar você de relance
      ctx.arc(sx, sy, 3.5 * k + 1.5, 0, Math.PI * 2);
      ctx.strokeStyle = "#ffd84a";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }
}
