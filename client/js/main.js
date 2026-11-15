// Ponto de entrada do cliente: login, estado recebido do servidor, HUD, chat e loop de desenho.
import { Connection, gameSocketUrl } from "./net.js";
import { setupInput } from "./input.js";
import { Renderer } from "./render.js";

const $ = (id) => document.getElementById(id);

const state = {
  myId: null,
  ents: new Map(), // id -> dados do snapshot + posição desenhada (rx, ry)
  projs: [],
  snapTime: 0,
  swings: [],
  floaters: [],
  you: null,
  statLabels: {},
};

const renderer = new Renderer($("canvas"));
let conn = null;
let inGame = false;

// ------------------------------------------------------------------ rede

function connect() {
  conn = new Connection(gameSocketUrl(), { onMessage: handleMessage, onClose: handleClose });
}

function handleMessage(msg) {
  switch (msg.t) {
    case "welcome":
      state.myId = msg.id;
      state.statLabels = msg.stat_labels;
      renderer.setMap(msg.map);
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
      const teleported = Math.hypot(e.x - ent.rx, e.y - ent.ry) > 96; // ex.: renasceu
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

  for (const fx of msg.fx) {
    if (fx.k === "dmg") {
      const color = fx.id === state.myId ? "#ff6b6b" : "#ffffff";
      state.floaters.push({ x: fx.x, y: fx.y, text: `-${fx.v}`, color, born: now, ttl: 800 });
    } else if (fx.k === "lvl") {
      state.floaters.push({ x: fx.x, y: fx.y - 12, text: "Nível +1", color: "#ffd65a", born: now, ttl: 1400 });
    } else if (fx.k === "swing") {
      state.swings.push({ id: fx.id, born: now, ttl: 160 });
    }
  }

  state.you = msg.you;
  updateHud();
}

// ------------------------------------------------------------------ HUD e chat

function setBar(id, value, max, label) {
  const bar = $(id);
  bar.querySelector(".fill").style.width = `${max > 0 ? (100 * value) / max : 0}%`;
  bar.querySelector(".label").textContent = `${label} ${value} / ${max}`;
}

let lastStatsKey = "";
const STATE_LABELS ={ idle: "", walk: "", meditate: " · meditando", rest: " · descansando", dead: " · derrotado" };

function updateHud() {
  const you = state.you;
  const me = state.ents.get(state.myId);
  if (!you || !me) return;
  $("me-name").textContent = me.n + (STATE_LABELS[you.s] ?? "");
  setBar("bar-hp", you.hp, you.mhp, "Vida");
  setBar("bar-energy", you.en, you.men, "Energia");
  setBar("bar-fatigue", you.fat, you.mfat, "Cansaço");

  // só refaz a lista de atributos quando algo mudou (chegam 20 snapshots por segundo)
  const statsKey = JSON.stringify(you.stats);
  if (statsKey === lastStatsKey) return;
  lastStatsKey = statsKey;
  $("stats").replaceChildren(
    ...Object.entries(you.stats).map(([key, s]) => {
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
    }),
  );
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

$("login").addEventListener("submit", (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  if (!conn || conn.closed) connect();
  conn.send({ t: "login", name: $("login-name").value.trim(), password: $("login-password").value });
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

setupInput({
  enabled: () => inGame,
  onMove: (held) => conn?.send({ t: "input", ...held }),
  onAction: (action) => conn?.send({ t: action }),
  onEnter: () => chatInput.focus(),
});

// ------------------------------------------------------------------ loop de desenho

let last = performance.now();
function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;

  // aproxima a posição desenhada da última posição recebida (suaviza os 20 ticks/s)
  const k = 1 - Math.exp(-dt * 18);
  for (const e of state.ents.values()) {
    e.rx += (e.x - e.rx) * k;
    e.ry += (e.y - e.ry) * k;
  }
  state.swings = state.swings.filter((s) => now - s.born < s.ttl);
  state.floaters = state.floaters.filter((f) => now - f.born < f.ttl);

  renderer.draw(state, now);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
