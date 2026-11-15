// Ponto de entrada do cliente: login, estado recebido do servidor, previsão de movimento,
// HUD, barra de skills, chat e loop de desenho.
import { Connection, gameSocketUrl } from "./net.js";
import { SLOT_LABELS, setupInput } from "./input.js";
import { Physics } from "./physics.js";
import { Renderer } from "./render.js";

const $ = (id) => document.getElementById(id);

const state = {
  myId: null,
  ents: new Map(), // id -> dados do snapshot + posição desenhada (rx, ry)
  projs: [],
  snapTime: 0,
  swings: [],
  rings: [], // áreas e buffs
  trails: [], // dashes
  floaters: [],
  you: null,
  defs: { skills: {}, effects: {}, enemies: {}, races: {}, classes: {} },
  statLabels: {},
};

const renderer = new Renderer($("canvas"));
let conn = null;
let inGame = false;

// Previsão de movimento (só do próprio personagem)
let physics = null;
let tickTimer = null;
let seq = 0;
let pending = []; // comandos enviados que o servidor ainda não confirmou
let pred = null; // posição prevista {x, y, f}

// ------------------------------------------------------------------ rede

function connect() {
  conn = new Connection(gameSocketUrl(), { onMessage: handleMessage, onClose: handleClose });
}

function handleMessage(msg) {
  switch (msg.t) {
    case "welcome":
      state.myId = msg.id;
      state.statLabels = msg.stat_labels;
      state.defs = {
        skills: msg.skills, effects: msg.effects, enemies: msg.enemies, races: msg.races, classes: msg.classes,
      };
      state.ents.clear();
      renderer.setMap(msg.map);
      renderer.defs = state.defs;
      physics = new Physics(msg.map, msg.half, msg.tick_rate);
      // Ticks do cliente num timer próprio, independentes da taxa de quadros do desenho
      clearInterval(tickTimer);
      tickTimer = setInterval(clientTick, 1000 / msg.tick_rate);
      seq = 0;
      pending = [];
      pred = null;
      lastStatsKey = "";
      inGame = true;
      $("overlay").classList.add("hidden");
      document.activeElement?.blur();
      break;
    case "error":
      $("login-error").textContent = msg.msg;
      break;
    case "snap":
      applySnapshot(msg);
      break;
    case "chat":
      addChatLine(msg.text, msg.from);
      break;
    case "sys":
      addChatLine(msg.text);
      break;
  }
}

function handleClose() {
  inGame = false;
  clearInterval(tickTimer);
  $("overlay").classList.remove("hidden");
  $("login-error").textContent = "Conexão perdida. Entre de novo.";
  conn = null;
}

function applySnapshot(msg) {
  const now = performance.now();
  const seen = new Set();
  for (const e of msg.ents) {
    seen.add(e.id);
    const ent = state.ents.get(e.id);
    if (!ent) {
      state.ents.set(e.id, { ...e, rx: e.x, ry: e.y });
    } else {
      const teleported = Math.hypot(e.x - ent.rx, e.y - ent.ry) > 160; // ex.: renasceu
      Object.assign(ent, e);
      if (teleported) {
        ent.rx = e.x;
        ent.ry = e.y;
      }
    }
  }
  for (const id of state.ents.keys()) if (!seen.has(id)) state.ents.delete(id);

  state.projs = msg.projs;
  state.snapTime = now;
  for (const fx of msg.fx) handleFx(fx, now);

  state.you = msg.you;
  reconcile(msg.you);
  updateHud();
}

// Parte da posição confirmada pelo servidor e reaplica os comandos ainda não confirmados.
function reconcile(you) {
  while (pending.length && pending[0].seq <= you.ack) pending.shift();
  const me = state.ents.get(state.myId);
  const facing = pending.length && pred ? pred.f : me?.f ?? "down";
  pred = { x: you.x, y: you.y, f: facing };
  for (const cmd of pending) physics.step(pred, cmd.keys, you.spd);
}

function handleFx(fx, now) {
  const at = (id) => state.ents.get(id);
  switch (fx.k) {
    case "dmg": {
      const color = fx.id === state.myId ? "#ff6b6b" : "#ffffff";
      state.floaters.push({ x: fx.x, y: fx.y, text: `-${fx.v}`, color, born: now, ttl: 800 });
      break;
    }
    case "heal":
      state.floaters.push({ x: fx.x, y: fx.y, text: `+${fx.v}`, color: "#7bd65a", born: now, ttl: 800 });
      break;
    case "lvl":
      state.floaters.push({ x: fx.x, y: fx.y - 12, text: "Nível +1", color: "#ffd65a", born: now, ttl: 1400 });
      break;
    case "swing":
      state.swings.push({ id: fx.id, r: fx.r ?? 30, born: now, ttl: 160 });
      break;
    case "area": {
      const e = at(fx.id);
      if (e) state.rings.push({ x: e.x, y: e.y, r: fx.r, color: fx.c, born: now, ttl: 350 });
      break;
    }
    case "buff": {
      const e = at(fx.id);
      if (e) state.rings.push({ x: e.x, y: e.y, r: 24, color: fx.c, born: now, ttl: 450 });
      break;
    }
    case "dash":
      state.trails.push({ x0: fx.x0, y0: fx.y0, x1: fx.x1, y1: fx.y1, born: now, ttl: 250 });
      break;
  }
}

// ------------------------------------------------------------------ tick do cliente (20/s)

function clientTick() {
  if (!inGame || !pred || !state.you || state.you.s === "dead") return;
  const keys = input.sample();
  if (!keys.up && !keys.down && !keys.left && !keys.right) return;
  seq += 1;
  conn.send({ t: "move", seq, ...keys });
  pending.push({ seq, keys });
  physics.step(pred, keys, state.you.spd);
}

function useSlot(slot) {
  const you = state.you;
  const skillId = you?.skills[slot];
  if (!skillId || !pred) return;
  if (cooldownLeft(skillId, performance.now()) > 0) return;
  conn?.send({ t: "skill", id: skillId, f: pred.f });
}

function cooldownLeft(skillId, now) {
  const at = state.you?.cd[skillId] ?? 0;
  return Math.max(0, at - (now - state.snapTime) / 1000);
}

// ------------------------------------------------------------------ HUD e chat

function setBar(id, value, max, label) {
  const bar = $(id);
  bar.querySelector(".fill").style.width = `${max > 0 ? (100 * value) / max : 0}%`;
  bar.querySelector(".label").textContent = `${label} ${value} / ${max}`;
}

let lastStatsKey = "";
let lastHotbarKey = "";
const ZONE_LABELS = { safe: "Zona segura", pve: "Zona PvE", pvp: "Zona PvP" };
const STATE_LABELS ={ idle: "", walk: "", meditate: " · meditando", rest: " · descansando", dead: " · derrotado" };

function updateHud() {
  const you = state.you;
  const me = state.ents.get(state.myId);
  if (!you || !me) return;
  $("me-name").textContent = me.n + (STATE_LABELS[you.s] ?? "");
  const raceName = state.defs.races[you.race]?.name ?? "";
  const className = state.defs.classes[you.cls]?.name ?? "";
  $("me-class").textContent = [raceName, className].filter(Boolean).join(" · ");

  const zone = you.zone ?? { t: "pve", n: "" };
  const zoneEl = $("me-zone");
  zoneEl.className = zone.t;
  zoneEl.textContent = `${ZONE_LABELS[zone.t] ?? zone.t}${zone.n ? ` — ${zone.n}` : ""}`;

  const duelEl = $("me-duel");
  duelEl.hidden = !you.duel;
  if (you.duel) {
    const { with: rival, starts_in: startsIn, left } = you.duel;
    const clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    duelEl.textContent = startsIn > 0
      ? `Duelo contra ${rival} começa em ${Math.ceil(startsIn)}…`
      : `Duelo contra ${rival} · ${clock}`;
  }
  setBar("bar-hp", you.hp, you.mhp, "Vida");
  setBar("bar-energy", you.en, you.men, "Energia");
  setBar("bar-fatigue", you.fat, you.mfat, "Cansaço");

  $("effects").replaceChildren(
    ...you.ef.map(({ id, t }) => {
      const def = state.defs.effects[id] ?? { name: id, color: "#fff" };
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.style.borderColor = def.color;
      chip.textContent = `${def.name} ${t.toFixed(1)}s`;
      return chip;
    }),
  );

  // só refaz a lista de atributos quando algo mudou (chegam 20 snapshots por segundo)
  const statsKey = JSON.stringify(you.stats);
  if (statsKey !== lastStatsKey) {
    lastStatsKey = statsKey;
    $("stats").replaceChildren(...Object.entries(you.stats).map(([key, s]) => statRow(key, s)));
  }

  const hotbarKey = you.skills.join(",");
  if (hotbarKey !== lastHotbarKey) {
    lastHotbarKey = hotbarKey;
    buildHotbar(you.skills);
  }
}

function statRow(key, s) {
  const li = document.createElement("li");
  const name = document.createElement("span");
  name.textContent = state.statLabels[key] ?? key;
  const lv = document.createElement("span");
  lv.className = "lv";
  lv.textContent = `Nv ${s.lv}`;
  const xp = document.createElement("div");
  xp.className = "xp";
  xp.title = `${s.xp} / ${s.next} XP`;
  const fill = document.createElement("span");
  fill.style.width = `${s.next > 0 ? Math.min(100, (100 * s.xp) / s.next) : 100}%`;
  xp.append(fill);
  li.append(name, lv, xp);
  return li;
}

function buildHotbar(skills) {
  $("hotbar").replaceChildren(
    ...skills.map((skillId, slot) => {
      const def = state.defs.skills[skillId];
      const btn = document.createElement("button");
      btn.className = "slot";
      btn.type = "button";
      btn.dataset.skill = skillId;
      btn.title = `${def.name} — custo ${def.cost} energia, recarga ${def.cd}s`;
      btn.innerHTML = '<span class="cd"></span><span class="key"></span><span class="name"></span>';
      btn.querySelector(".key").textContent = SLOT_LABELS[slot];
      btn.querySelector(".name").textContent = def.name;
      btn.style.setProperty("--skill-color", def.color);
      btn.addEventListener("click", () => {
        useSlot(slot);
        btn.blur();
      });
      return btn;
    }),
  );
}

function updateHotbarCooldowns(now) {
  const you = state.you;
  if (!you) return;
  for (const btn of $("hotbar").children) {
    const def = state.defs.skills[btn.dataset.skill];
    const left = cooldownLeft(btn.dataset.skill, now);
    btn.querySelector(".cd").style.height = `${def.cd > 0 ? (100 * left) / def.cd : 0}%`;
    btn.classList.toggle("no-energy", you.en < def.cost);
  }
}

function addChatLine(text, from = null) {
  const log = $("chat-log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  const line = document.createElement("div");
  if (from) {
    const who = document.createElement("span");
    who.className = "from";
    who.textContent = `${from}: `;
    line.append(who, document.createTextNode(text));
  } else {
    line.className = "sys";
    line.textContent = text;
  }
  log.append(line);
  while (log.childElementCount > 150) log.firstElementChild.remove();
  if (atBottom) log.scrollTop = log.scrollHeight;
}

// ------------------------------------------------------------------ entrada

// Raças disponíveis na criação de personagem (o servidor só usa se o nome for novo)
async function loadRaces() {
  try {
    const races = await (await fetch("/api/races")).json();
    $("race-options").replaceChildren(
      ...races.map((race, i) => {
        const label = document.createElement("label");
        label.innerHTML = '<input type="radio" name="race"><span class="name"></span><span class="desc"></span>';
        const radio = label.querySelector("input");
        radio.value = race.id;
        radio.checked = i === 0;
        label.querySelector(".name").textContent = race.name;
        label.querySelector(".desc").textContent = race.description;
        return label;
      }),
    );
  } catch {
    $("login-error").textContent = "Não foi possível carregar as raças. O servidor está no ar?";
  }
}
loadRaces();

$("login").addEventListener("submit", (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  if (!conn || conn.closed) connect();
  const race = document.querySelector('input[name="race"]:checked')?.value ?? null;
  conn.send({ t: "login", name: $("login-name").value.trim(), password: $("login-password").value, race });
});

const chatInput = $("chat-input");
chatInput.addEventListener("keydown", (ev) => {
  ev.stopPropagation(); // não deixa as teclas do chat moverem o personagem
  if (ev.key === "Enter") {
    const text = chatInput.value.trim();
    if (text && conn) conn.send({ t: "chat", text });
    chatInput.value = "";
    chatInput.blur();
  } else if (ev.key === "Escape") {
    chatInput.blur();
  }
});

const input = setupInput({
  enabled: () => inGame,
  onSlot: useSlot,
  onAction: (action) => conn?.send({ t: action }),
  onEnter: () => chatInput.focus(),
});

// Para depurar pelo console do navegador: __game.state, __game.pred, __game.pending
window.__game = {
  state,
  get pred() { return pred; },
  get pending() { return pending; },
};

// ------------------------------------------------------------------ loop de desenho

let last = performance.now();
let lastHotbarUpdate = 0;

function frame(now) {
  const dt = Math.min(250, now - last) / 1000;
  last = now;

  // Outros: aproxima a posição desenhada da última recebida (suaviza os 20 ticks/s).
  // Eu: aproxima da posição prevista, que já responde na hora.
  const kOthers = 1 - Math.exp(-dt * 18);
  const kMe = 1 - Math.exp(-dt * 30);
  for (const e of state.ents.values()) {
    if (e.id === state.myId && pred && e.s !== "dead") {
      e.f = pred.f;
      if (Math.hypot(pred.x - e.rx, pred.y - e.ry) > 64) {
        e.rx = pred.x; // correção grande (dash, renascer): pula direto
        e.ry = pred.y;
      } else {
        e.rx += (pred.x - e.rx) * kMe;
        e.ry += (pred.y - e.ry) * kMe;
      }
    } else {
      e.rx += (e.x - e.rx) * kOthers;
      e.ry += (e.y - e.ry) * kOthers;
    }
  }

  const alive = (fx) => now - fx.born < fx.ttl;
  state.swings = state.swings.filter(alive);
  state.rings = state.rings.filter(alive);
  state.trails = state.trails.filter(alive);
  state.floaters = state.floaters.filter(alive);

  if (now - lastHotbarUpdate > 80) {
    lastHotbarUpdate = now;
    updateHotbarCooldowns(now);
  }

  renderer.draw(state, now);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
