// Desenho em 3D (protótipo): mundo em 3D visto por uma câmera inclinada, com os
// personagens como imagens 2D sempre viradas para a câmera ("billboards").
//
// O jogo continua 2D: o servidor só conhece x/y num plano. Aqui o plano vira o chão
// (x do mundo -> x, y do mundo -> z), paredes e árvores ganham altura, e nomes, barras
// de vida e números flutuantes são desenhados numa camada 2D por cima (o canvas normal).
//
// Unidade: 1 = um tile (32 px do mundo). A câmera gira em passos de 90°, porque o
// servidor só entende cima/baixo/esquerda/direita (ver rotateKeys).
import * as THREE from "three";
import { RARITY_COLORS } from "./inventory.js";
import { PARTY_NAME_COLOR, TILE_BASE, drawTile, levelColor, nameColor, rankTitle, tileNoise } from "./render.js";

const TAU = Math.PI * 2;
const DIR = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
const FACING_ANGLE = { right: 0, down: Math.PI / 2, left: Math.PI, up: -Math.PI / 2 };

const WALL_HEIGHT = 1.15;
const PITCH = (52 * Math.PI) / 180; // inclinação da câmera (0 = horizontal, 90° = de cima)
const ZOOM_MIN = 8;
const ZOOM_MAX = 26;

// ------------------------------------------------------------------ direções e câmera

// Teclas da tela -> teclas do mundo, com a câmera girada `steps` × 90°.
// Na tela, "cima" é para longe da câmera; no mundo, depende de para onde ela olha.
export function rotateKeys(keys, steps) {
  const s = ((steps % 4) + 4) % 4;
  if (!s) return keys;
  const sx = (keys.right ? 1 : 0) - (keys.left ? 1 : 0);
  const sy = (keys.down ? 1 : 0) - (keys.up ? 1 : 0);
  const th = (s * Math.PI) / 2;
  const wx = Math.round(sx * Math.cos(th) + sy * Math.sin(th));
  const wy = Math.round(-sx * Math.sin(th) + sy * Math.cos(th));
  return { up: wy < 0, down: wy > 0, left: wx < 0, right: wx > 0 };
}

// Para onde o personagem olha, visto pela câmera: front (para a câmera), back, left, right
function viewOf(facing, yaw) {
  const [wx, wy] = DIR[facing] ?? DIR.down;
  const sx = wx * Math.cos(yaw) - wy * Math.sin(yaw);
  const sy = wx * Math.sin(yaw) + wy * Math.cos(yaw);
  if (Math.abs(sy) >= Math.abs(sx)) return sy > 0 ? "front" : "back";
  return sx > 0 ? "right" : "left";
}

// ------------------------------------------------------------------ imagens dos personagens

const textureCache = new Map();

function canvasTexture(key, w, h, paint) {
  let tex = textureCache.get(key);
  if (tex) return tex;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  paint(c.getContext("2d"), w, h);
  tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  textureCache.set(key, tex);
  return tex;
}

function shade(hex, amount) {
  const c = new THREE.Color(hex);
  c.offsetHSL(0, 0, amount);
  return `#${c.getHexString()}`;
}

// Jogador: figura simples (cabeça, tronco, pernas) vista de frente, de costas ou de lado
function playerTexture(color, view) {
  return canvasTexture(`p:${color}:${view}`, 64, 96, (g) => {
    const dark = "rgba(0,0,0,0.65)";
    g.lineWidth = 3;
    g.strokeStyle = dark;
    // pernas
    g.fillStyle = shade(color, -0.25);
    g.fillRect(22, 70, 8, 22);
    g.fillRect(34, 70, 8, 22);
    g.strokeRect(22, 70, 8, 22);
    g.strokeRect(34, 70, 8, 22);
    // tronco
    g.fillStyle = color;
    g.beginPath();
    g.roundRect(16, 38, 32, 36, 9);
    g.fill(); g.stroke();
    // braços
    g.fillStyle = shade(color, -0.12);
    if (view === "left" || view === "right") {
      g.beginPath(); g.roundRect(26, 42, 12, 26, 5); g.fill(); g.stroke();
    } else {
      g.beginPath(); g.roundRect(8, 42, 10, 24, 5); g.fill(); g.stroke();
      g.beginPath(); g.roundRect(46, 42, 10, 24, 5); g.fill(); g.stroke();
    }
    // cabeça
    g.fillStyle = "#f0d2b0";
    g.beginPath(); g.arc(32, 24, 15, 0, TAU); g.fill(); g.stroke();
    // cabelo
    g.fillStyle = shade(color, -0.35);
    g.beginPath();
    if (view === "back") g.arc(32, 24, 15, 0, TAU);
    else g.arc(32, 20, 15, Math.PI, TAU);
    g.fill();
    // olhos
    g.fillStyle = "#222";
    if (view === "front") {
      g.fillRect(25, 25, 4, 5);
      g.fillRect(35, 25, 4, 5);
    } else if (view === "left") {
      g.fillRect(21, 25, 4, 5);
    } else if (view === "right") {
      g.fillRect(39, 25, 4, 5);
    }
  });
}

// Inimigo: bolha espinhosa na cor do tipo, com olhos vermelhos quando olha para a câmera
function enemyTexture(color, view) {
  return canvasTexture(`e:${color}:${view}`, 64, 64, (g) => {
    g.fillStyle = color;
    g.strokeStyle = "rgba(0,0,0,0.65)";
    g.lineWidth = 3;
    g.beginPath();
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * TAU - Math.PI / 2;
      const r = i % 2 ? 19 : 29;
      g.lineTo(32 + Math.cos(a) * r, 34 + Math.sin(a) * r);
    }
    g.closePath(); g.fill(); g.stroke();
    g.fillStyle = shade(color, 0.12);
    g.beginPath(); g.arc(26, 26, 7, 0, TAU); g.fill();
    if (view !== "back") {
      g.fillStyle = "#ff5a5a";
      const off = view === "left" ? -7 : view === "right" ? 7 : 0;
      g.fillRect(22 + off, 30, 7, 7);
      g.fillRect(35 + off, 30, 7, 7);
    }
  });
}

// NPC: figura de túnica longa na cor dele
function npcTexture(color) {
  return canvasTexture(`n:${color}`, 64, 96, (g) => {
    g.strokeStyle = "rgba(0,0,0,0.65)";
    g.lineWidth = 3;
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(32, 36); g.lineTo(52, 92); g.lineTo(12, 92); g.closePath();
    g.fill(); g.stroke();
    g.fillStyle = shade(color, -0.15);
    g.fillRect(20, 60, 24, 6);
    g.fillStyle = "#f0d2b0";
    g.beginPath(); g.arc(32, 26, 14, 0, TAU); g.fill(); g.stroke();
    g.fillStyle = shade(color, -0.3);
    g.beginPath(); g.arc(32, 22, 15, Math.PI * 1.05, TAU * 0.98); g.fill();
    g.fillStyle = "#222";
    g.fillRect(26, 27, 4, 4);
    g.fillRect(34, 27, 4, 4);
  });
}

function glowTexture(color) {
  return canvasTexture(`g:${color}`, 64, 64, (g) => {
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(0.3, color);
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
  });
}

function dropTexture(kind) {
  return canvasTexture(`d:${kind}`, 32, 32, (g) => {
    g.lineWidth = 2;
    if (kind === "gold") {
      g.fillStyle = "#ffd65a";
      g.strokeStyle = "#a07a10";
      g.beginPath(); g.arc(16, 16, 10, 0, TAU); g.fill(); g.stroke();
      return;
    }
    g.fillStyle = RARITY_COLORS[kind] ?? RARITY_COLORS.common;
    g.strokeStyle = "rgba(0,0,0,0.7)";
    g.beginPath();
    g.moveTo(16, 3); g.lineTo(27, 16); g.lineTo(16, 29); g.lineTo(5, 16);
    g.closePath(); g.fill(); g.stroke();
  });
}

// ------------------------------------------------------------------ peças reaproveitadas a cada quadro

// Conjunto de malhas iguais reaproveitadas a cada quadro (anéis, golpes, rastros):
// begin() no começo do quadro, get() para cada uma que vai aparecer, end() esconde o resto.
class MeshPool {
  constructor(parent, geometry, blending = THREE.NormalBlending) {
    this.parent = parent;
    this.geometry = geometry;
    this.blending = blending;
    this.meshes = [];
    this.used = 0;
  }

  begin() {
    this.used = 0;
  }

  get(color, opacity) {
    let mesh = this.meshes[this.used];
    if (!mesh) {
      const material = new THREE.MeshBasicMaterial({
        transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: this.blending,
      });
      mesh = new THREE.Mesh(this.geometry, material);
      mesh.rotation.x = -Math.PI / 2; // deitado no chão
      mesh.renderOrder = 2;
      this.parent.add(mesh);
      this.meshes.push(mesh);
    }
    this.used += 1;
    mesh.visible = true;
    mesh.material.color.set(color);
    mesh.material.opacity = opacity;
    return mesh;
  }

  end() {
    for (let i = this.used; i < this.meshes.length; i++) this.meshes[i].visible = false;
  }
}

// ------------------------------------------------------------------ desenho

export class Renderer3D {
  constructor(glCanvas, overlayCanvas) {
    this.glCanvas = glCanvas;
    this.canvas = overlayCanvas; // nomes, barras e textos (o mesmo canvas do desenho 2D)
    this.ctx = overlayCanvas.getContext("2d");
    this.defs = { skills: {}, effects: {}, enemies: {} };
    this.ts = 32;

    const gl = new THREE.WebGLRenderer({ canvas: glCanvas, antialias: true });
    gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    gl.setSize(overlayCanvas.width, overlayCanvas.height, false);
    gl.shadowMap.enabled = true;
    gl.shadowMap.type = THREE.PCFSoftShadowMap;
    this.gl = gl;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color("#0b0c10");
    this.scene.fog = new THREE.Fog("#0b0c10", 22, 48);
    this.camera = new THREE.PerspectiveCamera(30, overlayCanvas.width / overlayCanvas.height, 0.5, 200);

    this.scene.add(new THREE.HemisphereLight("#cfe3ff", "#3a3020", 1.1));
    const sun = new THREE.DirectionalLight("#fff3d8", 1.6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const sc = sun.shadow.camera;
    sc.left = -18; sc.right = 18; sc.top = 18; sc.bottom = -18; sc.near = 1; sc.far = 60;
    sun.shadow.bias = -0.0015;
    this.scene.add(sun, sun.target);
    this.sun = sun;

    this.mapGroup = new THREE.Group();
    this.dynamic = new THREE.Group();
    this.scene.add(this.mapGroup, this.dynamic);

    const unitRing = new THREE.RingGeometry(0.86, 1, 40);
    const unitDisc = new THREE.CircleGeometry(1, 24);
    const swing = new THREE.RingGeometry(0.55, 1, 16, 1, -0.9, 1.8);
    const strip = new THREE.PlaneGeometry(1, 1);
    this.rings = new MeshPool(this.dynamic, unitRing);
    this.glows = new MeshPool(this.dynamic, unitDisc, THREE.AdditiveBlending);
    this.shadows = new MeshPool(this.dynamic, unitDisc);
    this.swings = new MeshPool(this.dynamic, swing, THREE.AdditiveBlending);
    this.strips = new MeshPool(this.dynamic, strip, THREE.AdditiveBlending);

    this.sprites = new Map(); // id da entidade -> THREE.Sprite
    this.dropSprites = new Map();
    this.projSprites = [];

    this.yawSteps = 0; // giro da câmera em passos de 90° (o que as teclas usam)
    this.yaw = 0; // giro desenhado (vai até yawSteps suavemente)
    this.zoom = 17; // distância da câmera até o personagem, em tiles
    this.target = new THREE.Vector3();
    this.hasTarget = false;
  }

  rotate(dir) {
    this.yawSteps = (this.yawSteps + dir + 4) % 4;
  }

  zoomBy(delta) {
    this.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, this.zoom * (delta > 0 ? 1.1 : 1 / 1.1)));
  }

  // ---------------------------------------------------------------- mapa

  setMap(map) {
    this.map = map;
    this.ts = map.tile_size;
    for (const child of [...this.mapGroup.children]) {
      this.mapGroup.remove(child);
      child.geometry?.dispose();
      for (const m of [child.material].flat()) {
        m?.map?.dispose();
        m?.dispose();
      }
    }
    this.hasTarget = false;
    const rows = map.rows;
    const w = rows[0].length;
    const h = rows.length;
    const ts = this.ts;

    // Chão: uma textura com todos os tiles (paredes e árvores viram 3D por cima)
    const floor = document.createElement("canvas");
    floor.width = w * ts;
    floor.height = h * ts;
    const g = floor.getContext("2d");
    rows.forEach((row, ty) => {
      [...row].forEach((ch, tx) => {
        const base = ch === "T" ? "." : ch === "#" ? "#" : ch;
        if (base === "#") {
          g.fillStyle = TILE_BASE["#"];
          g.fillRect(tx * ts, ty * ts, ts, ts);
        } else {
          drawTile(g, base, tx * ts, ty * ts, ts, tileNoise(tx, ty));
        }
      });
    });
    for (const zone of map.zones ?? []) {
      if (zone.type !== "pvp") continue;
      const [x0, y0, x1, y1] = zone.area;
      const x = x0 * ts, y = y0 * ts, zw = (x1 - x0 + 1) * ts, zh = (y1 - y0 + 1) * ts;
      g.fillStyle = "rgba(200, 50, 50, 0.16)";
      g.fillRect(x, y, zw, zh);
      g.strokeStyle = "rgba(255, 110, 110, 0.8)";
      g.lineWidth = 3;
      g.setLineDash([10, 8]);
      g.strokeRect(x + 2, y + 2, zw - 4, zh - 4);
      g.setLineDash([]);
    }
    const tex = new THREE.CanvasTexture(floor);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshLambertMaterial({ map: tex }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(w / 2, 0, h / 2);
    ground.receiveShadow = true;
    this.mapGroup.add(ground);

    // Paredes e árvores, em lote (uma malha para todas as do mesmo tipo)
    const walls = [];
    const trees = [];
    rows.forEach((row, ty) => [...row].forEach((ch, tx) => {
      if (ch === "#") walls.push([tx, ty]);
      else if (ch === "T") trees.push([tx, ty]);
    }));
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);
    const color = new THREE.Color();

    if (walls.length) {
      const mesh = new THREE.InstancedMesh(
        new THREE.BoxGeometry(1, WALL_HEIGHT, 1),
        new THREE.MeshLambertMaterial({ color: "#ffffff" }),
        walls.length,
      );
      walls.forEach(([tx, ty], i) => {
        m.makeTranslation(tx + 0.5, WALL_HEIGHT / 2, ty + 0.5);
        mesh.setMatrixAt(i, m);
        mesh.setColorAt(i, color.set("#5b606e").offsetHSL(0, 0, (tileNoise(tx, ty) - 0.5) * 0.08));
      });
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.mapGroup.add(mesh);
    }
    if (trees.length) {
      const trunks = new THREE.InstancedMesh(
        new THREE.CylinderGeometry(0.07, 0.1, 0.55, 6),
        new THREE.MeshLambertMaterial({ color: "#5a3d24" }),
        trees.length,
      );
      const crowns = new THREE.InstancedMesh(
        new THREE.IcosahedronGeometry(0.46, 0),
        new THREE.MeshStandardMaterial({ color: "#ffffff", flatShading: true, roughness: 0.95 }),
        trees.length,
      );
      trees.forEach(([tx, ty], i) => {
        const r = tileNoise(tx, ty);
        const k = 0.85 + r * 0.35;
        m.makeTranslation(tx + 0.5, 0.27, ty + 0.5);
        trunks.setMatrixAt(i, m);
        q.setFromAxisAngle(up, r * TAU);
        m.compose(new THREE.Vector3(tx + 0.5, 0.6 + 0.3 * k, ty + 0.5), q, new THREE.Vector3(k, k * 1.15, k));
        crowns.setMatrixAt(i, m);
        crowns.setColorAt(i, color.set("#2f5a26").offsetHSL(0, 0, (r - 0.5) * 0.1));
      });
      for (const mesh of [trunks, crowns]) {
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        this.mapGroup.add(mesh);
      }
    }

    // Portais: coluna de luz que pulsa
    this.portalMeshes = (map.portals ?? []).map((portal) => {
      const [x0, y0, x1, y1] = portal.area;
      const pw = x1 - x0 + 1;
      const ph = y1 - y0 + 1;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(pw, 1.6, ph),
        new THREE.MeshBasicMaterial({
          color: "#9fdcff", transparent: true, opacity: 0.25, depthWrite: false, blending: THREE.AdditiveBlending,
        }),
      );
      mesh.position.set(x0 + pw / 2, 0.8, y0 + ph / 2);
      mesh.userData = { label: `→ ${portal.title ?? portal.to}`, x: x0 + pw / 2, z: y0 + ph / 2 };
      this.mapGroup.add(mesh);
      return mesh;
    });
    this.zoneLabels = (map.zones ?? []).filter((z) => z.type === "pvp").map((z) => ({
      text: `PvP · ${z.name}`, x: z.area[0] + 0.3, z: z.area[1] + 0.5,
    }));
  }

  // ---------------------------------------------------------------- quadro

  // Mantém o canvas 3D exatamente embaixo do canvas 2D (que define o tamanho na página)
  syncSize() {
    const c = this.canvas;
    const st = this.glCanvas.style;
    const box = `${c.offsetLeft}|${c.offsetTop}|${c.offsetWidth}|${c.offsetHeight}`;
    if (box === this.lastBox) return;
    this.lastBox = box;
    st.left = `${c.offsetLeft}px`;
    st.top = `${c.offsetTop}px`;
    st.width = `${c.offsetWidth}px`;
    st.height = `${c.offsetHeight}px`;
  }

  updateCamera(me, dt) {
    if (me) {
      const goal = new THREE.Vector3(me.rx / this.ts, 0, me.ry / this.ts);
      if (!this.hasTarget) this.target.copy(goal);
      else this.target.lerp(goal, 1 - Math.exp(-dt * 12));
      this.hasTarget = true;
    }
    // giro suave até o passo escolhido, pelo caminho mais curto
    const goalYaw = (this.yawSteps * Math.PI) / 2;
    let diff = ((goalYaw - this.yaw) % TAU + TAU + Math.PI) % TAU - Math.PI;
    this.yaw += diff * (1 - Math.exp(-dt * 10));
    if (Math.abs(diff) < 1e-3) this.yaw = goalYaw;

    const d = this.zoom;
    const horiz = Math.cos(PITCH) * d;
    const cam = this.camera;
    cam.position.set(
      this.target.x + Math.sin(this.yaw) * horiz,
      Math.sin(PITCH) * d,
      this.target.z + Math.cos(this.yaw) * horiz,
    );
    cam.lookAt(this.target.x, 0.5, this.target.z);
    this.scene.fog.near = d + 6;
    this.scene.fog.far = d + 30;
    // a luz (e a sombra) acompanham o personagem
    this.sun.position.set(this.target.x - 8, 18, this.target.z + 5);
    this.sun.target.position.copy(this.target);
  }

  draw(state, now) {
    const { ctx, canvas } = this;
    const dt = this.lastNow ? Math.min(0.1, (now - this.lastNow) / 1000) : 0;
    this.lastNow = now;
    this.syncSize();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this.map) return;

    const me = state.ents.get(state.myId);
    this.updateCamera(me, dt);
    this.questMarks = state.questMarks ?? {};
    this.myLevel = state.myLevel ?? 1;
    this.partyIds = state.partyIds;
    const ts = this.ts;

    for (const pool of [this.rings, this.glows, this.shadows, this.swings, this.strips]) pool.begin();

    // portais pulsando
    const pulse = 0.5 + 0.5 * Math.sin(now / 350);
    for (const mesh of this.portalMeshes ?? []) mesh.material.opacity = 0.18 + 0.17 * pulse;

    // personagens
    const seen = new Set();
    for (const e of state.ents.values()) {
      seen.add(e.id);
      this.drawEntity(e, e.id === state.myId, now);
    }
    for (const [id, sprite] of this.sprites) {
      if (!seen.has(id)) {
        this.dynamic.remove(sprite);
        sprite.material.dispose();
        this.sprites.delete(id);
      }
    }

    // itens no chão
    const dropSeen = new Set();
    for (const d of state.drops) {
      dropSeen.add(d.id);
      const kind = d.i === "gold" ? "gold" : this.defs.items?.[d.i]?.rarity ?? "common";
      let sprite = this.dropSprites.get(d.id);
      if (!sprite) {
        sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: dropTexture(kind), alphaTest: 0.3 }));
        sprite.center.set(0.5, 0);
        sprite.scale.set(0.38, 0.38, 1);
        this.dynamic.add(sprite);
        this.dropSprites.set(d.id, sprite);
      }
      sprite.position.set(d.x / ts, 0.08 + 0.06 * Math.sin(now / 300 + d.id), d.y / ts);
      this.shadows.get("#000000", 0.3).position.set(d.x / ts, 0.012, d.y / ts);
      this.shadows.meshes[this.shadows.used - 1].scale.set(0.16, 0.16, 1);
      if (kind !== "gold") {
        const glow = this.glows.get(RARITY_COLORS[kind] ?? "#ffffff", 0.35);
        glow.position.set(d.x / ts, 0.015, d.y / ts);
        glow.scale.set(0.38, 0.38, 1);
      }
    }
    for (const [id, sprite] of this.dropSprites) {
      if (!dropSeen.has(id)) {
        this.dynamic.remove(sprite);
        sprite.material.dispose();
        this.dropSprites.delete(id);
      }
    }

    // projéteis: brilho que segue a velocidade entre os snapshots
    const since = Math.min(now - state.snapTime, 100) / 1000;
    state.projs.forEach((p, i) => {
      let sprite = this.projSprites[i];
      if (!sprite) {
        sprite = new THREE.Sprite(new THREE.SpriteMaterial({
          transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        }));
        sprite.scale.set(0.8, 0.8, 1);
        this.dynamic.add(sprite);
        this.projSprites.push(sprite);
      }
      const color = this.defs.skills[p.s]?.color ?? "#8cc8ff";
      const tex = glowTexture(color);
      if (sprite.material.map !== tex) {
        sprite.material.map = tex;
        sprite.material.needsUpdate = true;
      }
      sprite.visible = true;
      sprite.position.set((p.x + p.vx * since) / ts, 0.5, (p.y + p.vy * since) / ts);
      const glow = this.glows.get(color, 0.25);
      glow.position.set((p.x + p.vx * since) / ts, 0.02, (p.y + p.vy * since) / ts);
      glow.scale.set(0.35, 0.35, 1);
    });
    for (let i = state.projs.length; i < this.projSprites.length; i++) this.projSprites[i].visible = false;

    // golpes, áreas, buffs e rastros de dash, no chão
    for (const s of state.swings) {
      const e = state.ents.get(s.id);
      if (!e) continue;
      const t = (now - s.born) / s.ttl;
      const mesh = this.swings.get("#ffffff", 0.85 * (1 - t));
      const r = (s.r * 0.8) / ts;
      mesh.position.set(e.rx / ts, 0.35, e.ry / ts);
      mesh.scale.set(r, r, 1);
      // o arco está centrado no eixo x; no chão, o ângulo do mundo vira -ângulo em torno de y
      mesh.rotation.set(-Math.PI / 2, 0, -(FACING_ANGLE[e.f] ?? 0) - t * 0.4);
    }
    for (const r of state.rings) {
      const t = (now - r.born) / r.ttl;
      const radius = (r.r * (0.4 + 0.6 * t)) / ts;
      const mesh = this.rings.get(r.color, Math.max(0, 1 - t));
      mesh.position.set(r.x / ts, 0.03, r.y / ts);
      mesh.scale.set(radius, radius, 1);
    }
    for (const tr of state.trails) {
      const t = (now - tr.born) / tr.ttl;
      const dx = (tr.x1 - tr.x0) / ts;
      const dz = (tr.y1 - tr.y0) / ts;
      const mesh = this.strips.get("#dcebff", 0.5 * (1 - t));
      mesh.position.set((tr.x0 + tr.x1) / 2 / ts, 0.04, (tr.y0 + tr.y1) / 2 / ts);
      mesh.scale.set(Math.hypot(dx, dz) + 0.4, 0.44, 1);
      mesh.rotation.set(-Math.PI / 2, 0, -Math.atan2(dz, dx));
    }

    for (const pool of [this.rings, this.glows, this.shadows, this.swings, this.strips]) pool.end();
    this.gl.render(this.scene, this.camera);

    // ---- camada 2D por cima: nomes, barras, marcas de missão, números
    for (const label of this.zoneLabels ?? []) this.groundText(label.text, label.x, label.z, "#ffb0b0", 12);
    for (const mesh of this.portalMeshes ?? []) {
      const { label, x, z } = mesh.userData;
      const p = this.toScreen(x, 1.9, z);
      if (p) this.text(label, p.x, p.y, "#a8e4ff", "bold 12px system-ui, sans-serif");
    }
    const ents = [...state.ents.values()].sort((a, b) => this.depth(b) - this.depth(a));
    for (const e of ents) this.drawLabels(e, e.id === state.myId, now);
    for (const f of state.floaters) {
      const p = this.toScreen(f.x / ts, 1.3, f.y / ts);
      if (!p) continue;
      const t = (now - f.born) / f.ttl;
      ctx.globalAlpha = Math.max(0, 1 - t);
      this.text(f.text, p.x, p.y - t * 28, f.color, "bold 13px system-ui, sans-serif");
      ctx.globalAlpha = 1;
    }
    if (me && me.s === "dead") {
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      this.text("Você foi derrotado…", canvas.width / 2, canvas.height / 2, "#ffffff", "bold 24px system-ui, sans-serif");
    }
  }

  // ---------------------------------------------------------------- personagens

  bodyScale(e) {
    return (e.h ?? 10) / 10; // chefes e elites são maiores
  }

  // Altura da imagem (em tiles) e da cabeça, para os textos ficarem logo acima
  spriteHeight(e) {
    const k = this.bodyScale(e);
    return e.k === "enemy" ? 0.95 * k : 1.15 * k;
  }

  drawEntity(e, isMe, now) {
    const ts = this.ts;
    const x = e.rx / ts;
    const z = e.ry / ts;
    const k = this.bodyScale(e);
    const view = e.k === "npc" ? "front" : viewOf(e.f, this.yaw);
    let tex;
    if (e.k === "npc") tex = npcTexture(this.defs.npcs?.[e.t]?.color ?? "#e0c070");
    else if (e.k === "enemy") tex = enemyTexture(this.defs.enemies[e.t]?.color ?? "#6d2f7d", view);
    else tex = playerTexture(nameColor(e.n), view);

    let sprite = this.sprites.get(e.id);
    if (!sprite) {
      sprite = new THREE.Sprite(new THREE.SpriteMaterial({ alphaTest: 0.4 }));
      sprite.center.set(0.5, 0); // a imagem fica em pé sobre o ponto do personagem
      this.dynamic.add(sprite);
      this.sprites.set(e.id, sprite);
    }
    if (sprite.material.map !== tex) {
      sprite.material.map = tex;
      sprite.material.needsUpdate = true;
    }
    const dead = e.s === "dead";
    sprite.visible = !(dead && e.k !== "player");
    sprite.material.transparent = dead;
    sprite.material.opacity = dead ? 0.3 : 1;
    const h = this.spriteHeight(e);
    const w = e.k === "enemy" ? h : h * (2 / 3);
    const bob = e.k === "enemy" && !dead ? Math.abs(Math.sin(now / 260 + e.id)) * 0.05 : 0;
    sprite.scale.set(w, dead ? h * 0.4 : h, 1);
    sprite.position.set(x, bob, z);
    if (dead) return;

    // sombra no chão
    const shadow = this.shadows.get("#000000", 0.35);
    shadow.position.set(x, 0.01, z);
    shadow.scale.set(0.3 * k, 0.3 * k, 1);
    if (e.k === "npc") return;

    // anéis no chão: meditação, duelo, elite/chefe e um por efeito de status
    if (e.s === "meditate") this.ring(x, z, 0.55 + Math.sin(now / 180) * 0.08, "#82c8ff", 0.85);
    if (e.du) this.ring(x, z, 0.65, "#ff5050", 0.9);
    if (e.el || e.boss) this.ring(x, z, 0.52 * k, e.boss ? "#ff5a5a" : "#ffd65a", 0.9);
    (e.ef ?? []).forEach((effectId, i) => {
      this.ring(x, z, 0.46 + i * 0.12, this.defs.effects[effectId]?.color ?? "#ffffff", 0.75);
    });
    if (isMe) this.ring(x, z, 0.36, "#ffffff", 0.35);
  }

  ring(x, z, radius, color, opacity) {
    const mesh = this.rings.get(color, opacity);
    mesh.position.set(x, 0.02, z);
    mesh.scale.set(radius, radius, 1);
  }

  depth(e) {
    return this.camera.position.distanceToSquared(new THREE.Vector3(e.rx / this.ts, 0, e.ry / this.ts));
  }

  drawLabels(e, isMe, now) {
    const { ctx } = this;
    const ts = this.ts;
    const x = e.rx / ts;
    const z = e.ry / ts;
    if (e.s === "dead") return;
    const head = this.headScreen(e, 0.06);
    if (!head) return;
    const hx = head.x;
    const hy = head.y;

    if (e.k === "npc") {
      const def = this.defs.npcs?.[e.t] ?? { name: e.n, title: "" };
      this.text(def.name, hx, hy - 4, "#ffe9b0", "bold 11px system-ui, sans-serif");
      if (def.title) this.text(`‹${def.title}›`, hx, hy - 16, "#d8c8a0", "10px system-ui, sans-serif");
      const mark = this.questMarks?.[e.id];
      if (mark) {
        const my = hy - (def.title ? 30 : 20) + Math.sin(now / 250) * 2;
        ctx.font = "bold 18px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(0,0,0,0.8)";
        ctx.strokeText(mark, hx, my);
        ctx.fillStyle = mark === "?" ? "#7ee07e" : "#ffd84a";
        ctx.fillText(mark, hx, my);
      }
      return;
    }

    const k = this.bodyScale(e);
    const bw = 28 * Math.max(1, k * 0.8);
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(hx - bw / 2, hy, bw, 4);
    ctx.fillStyle = e.k === "enemy" ? "#d9534f" : "#5cb85c";
    ctx.fillRect(hx - bw / 2, hy, bw * Math.max(0, Math.min(1, e.hp / e.mhp)), 4);

    ctx.font = e.boss ? "bold 12px system-ui, sans-serif" : "11px system-ui, sans-serif";
    const role = e.st ? this.defs.roles?.[e.st] : null;
    const tag = role ? `[${role.tag}] ` : "";
    const label = e.k === "enemy" ? `${e.el ? "★ " : ""}${e.n}${e.lv ? ` · Nv ${e.lv}` : ""}` : e.n;
    const left = hx - ctx.measureText(tag + label).width / 2;
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillText(tag + label, left + 1, hy - 4);
    if (role) {
      ctx.fillStyle = role.color;
      ctx.fillText(tag, left, hy - 5);
    }
    ctx.fillStyle = isMe ? "#ffe08a" : e.k === "enemy" ? levelColor(e.lv ?? 1, this.myLevel)
      : this.partyIds?.has(e.id) ? PARTY_NAME_COLOR : "#ffffff";
    ctx.fillText(label, left + ctx.measureText(tag).width, hy - 5);

    const title = rankTitle(this.defs, e.o);
    if (title) this.text(title, hx, hy - 18, this.defs.orgs?.[e.o[0]]?.color ?? "#d8dbe2", "10px system-ui, sans-serif");
    if (e.s === "rest") {
      this.text("z", hx + 14, hy + 10 - ((now / 400) % 1) * 8, "#cfd8ff", "bold 12px system-ui, sans-serif");
    }
  }

  // ---------------------------------------------------------------- tela

  // Topo da imagem do personagem na tela. A imagem fica de frente para a câmera e se
  // inclina junto com ela, então o topo está na direção "para cima" da câmera, não na vertical.
  headScreen(e, extra = 0) {
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
    const h = this.spriteHeight(e) + extra;
    return this.toScreen(e.rx / this.ts + up.x * h, up.y * h, e.ry / this.ts + up.z * h);
  }

  // Ponto do mundo 3D -> pixel do canvas (null se estiver atrás da câmera)
  toScreen(x, y, z) {
    const v = new THREE.Vector3(x, y, z).project(this.camera);
    if (v.z > 1) return null;
    return { x: ((v.x + 1) / 2) * this.canvas.width, y: ((1 - v.y) / 2) * this.canvas.height };
  }

  text(text, x, y, color, font) {
    const { ctx } = this;
    ctx.font = font;
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(0,0,0,0.8)";
    ctx.fillText(text, x + 1, y + 1);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  groundText(text, x, z, color, size) {
    const p = this.toScreen(x, 0, z);
    if (!p) return;
    this.ctx.textAlign = "left";
    this.ctx.font = `bold ${size}px system-ui, sans-serif`;
    this.ctx.fillStyle = "rgba(0,0,0,0.6)";
    this.ctx.fillText(text, p.x + 1, p.y + 1);
    this.ctx.fillStyle = color;
    this.ctx.fillText(text, p.x, p.y);
  }

  // Qual entidade está sob o mouse: compara com o retângulo da imagem dela na tela
  pickEntity(ev, entities) {
    const rect = this.canvas.getBoundingClientRect();
    const mx = (ev.clientX - rect.left) * (this.canvas.width / rect.width);
    const my = (ev.clientY - rect.top) * (this.canvas.height / rect.height);
    let best = null;
    let bestDist = Infinity;
    for (const e of entities) {
      const x = e.rx / this.ts;
      const z = e.ry / this.ts;
      const foot = this.toScreen(x, 0, z);
      const head = this.headScreen(e);
      if (!foot || !head) continue;
      const h = Math.max(12, foot.y - head.y);
      const halfW = Math.max(10, h * 0.4);
      const cx = foot.x;
      const cy = (foot.y + head.y) / 2;
      if (Math.abs(mx - cx) > halfW || my < head.y - 4 || my > foot.y + 4) continue;
      const d = Math.hypot(mx - cx, my - cy);
      if (d < bestDist) {
        bestDist = d;
        best = e;
      }
    }
    return best;
  }
}
