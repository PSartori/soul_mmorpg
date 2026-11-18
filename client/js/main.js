// Ponto de entrada do cliente: login, estado recebido do servidor, previsão de movimento,
// HUD, barra de skills, chat e loop de desenho.
import { Connection, gameSocketUrl } from "./net.js";
import { SLOT_LABELS, setupInput } from "./input.js";
import { Physics } from "./physics.js";
import { Renderer, rankTitle, screenToWorld } from "./render.js";
import { InvitePanel, OrgWindow, PlayerMenu } from "./social.js";
import { InventoryUI, RARITY_COLORS } from "./inventory.js";
import { AdminUI } from "./admin.js";
import { DialogBox, ShopWindow, npcHeading } from "./npc.js";
import { QuestLog } from "./quests.js";
import { SkillsWindow, TrainerWindow, makeDraggable, makeDropTarget, skillMeta } from "./skills.js";
import { CraftWindow } from "./crafting.js";
import { PartyInvitePanel, PartyPanel, partyMenuItems } from "./party.js";

const $ = (id) => document.getElementById(id);

const state = {
  myId: null,
  ents: new Map(), // id -> dados do snapshot + posição desenhada (rx, ry)
  projs: [],
  drops: [], // itens no chão
  snapTime: 0,
  swings: [],
  rings: [], // áreas e buffs
  trails: [], // dashes
  floaters: [],
  you: null,
  defs: {
    skills: {}, effects: {}, enemies: {}, races: {}, classes: {}, items: {}, orgs: {}, roles: {}, npcs: {}, quests: {},
  },
  questMarks: {}, // id do NPC -> "!" (tem missão) ou "?" (missão pronta para entregar)
  partyIds: new Set(), // quem é do meu grupo (nomes destacados)
  statLabels: {},
};

const renderer = new Renderer($("canvas"));
let conn = null;

// ------------------------------------------------------------------ visão 2D / 3D (protótipo)
// O desenho 3D (render3d.js + biblioteca three) só é carregado quando a visão 3D é ligada.
// Se não carregar (sem internet, navegador sem WebGL), o jogo continua em 2D.
let view = "2d";
let renderer3d = null;
let loading3d = null;
let rotateKeys = (keys) => keys; // teclas da tela -> do mundo, conforme o giro da câmera 3D
let currentMap = null;
const VIEW_KEY = "rpg.view";

function savedView() {
  try {
    return localStorage.getItem(VIEW_KEY);
  } catch {
    return null;
  }
}

async function setView(next) {
  if (next === "3d" && !renderer3d) {
    try {
      loading3d ??= import("./render3d.js");
      const mod = await loading3d;
      renderer3d = new mod.Renderer3D($("canvas3d"), $("canvas"));
      renderer3d.defs = state.defs;
      if (currentMap) renderer3d.setMap(currentMap);
      rotateKeys = mod.rotateKeys;
    } catch (err) {
      console.warn("visão 3D indisponível:", err);
      addChatLine("Não foi possível carregar a visão 3D; o jogo continua em 2D.");
      loading3d = null;
      next = "2d";
    }
  }
  view = next;
  $("canvas3d").hidden = view !== "3d";
  $("canvas").classList.toggle("overlay", view === "3d");
  $("view-toggle").textContent = view === "3d" ? "Visão 2D (V)" : "Visão 3D (V)";
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    // sem armazenamento: só não lembra a escolha
  }
}

const active3d = () => view === "3d" && renderer3d;
let inGame = false;

// Previsão de movimento (só do próprio personagem)
let physics = null;
let tickTimer = null;
let entityHalf = 10;
let tickRate = 20;
let npcRange = 80; // distância para conversar com NPCs (vem do servidor)
let solidKinds = new Set(); // o que bloqueia o jogador: "player", "enemy", "npc" (vem do servidor)
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
        items: msg.items, orgs: msg.orgs, roles: msg.roles, npcs: msg.npcs, quests: msg.quests,
      };
      npcRange = msg.npc_range ?? npcRange;
      solidKinds = new Set(Object.entries(msg.solid ?? {}).filter(([, on]) => on).map(([kind]) => kind));
      kickText = null;
      state.ents.clear();
      renderer.setMap(msg.map);
      renderer.defs = state.defs;
      currentMap = msg.map;
      if (renderer3d) {
        renderer3d.defs = state.defs;
        renderer3d.setMap(msg.map);
      }
      entityHalf = msg.half;
      tickRate = msg.tick_rate;
      physics = new Physics(msg.map, entityHalf, tickRate);
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
    case "map":
      changeMap(msg);
      break;
    case "content":
      applyContent(msg);
      break;
    case "dialog":
      hideNpcWindows();
      dialogBox.show(msg);
      break;
    case "shop":
      // loja, treinador e oficina ocupam o mesmo canto do inventário, organização, diário e skills
      hideNpcWindows();
      closeCornerWindows();
      shopWindow.show(msg);
      break;
    case "trainer":
      hideNpcWindows();
      closeCornerWindows();
      trainerWindow.show(msg);
      break;
    case "craft":
      hideNpcWindows();
      closeCornerWindows();
      craftWindow.show(msg);
      break;
    case "npc_close":
      hideNpcWindows();
      break;
    case "org_data":
      orgWindow.update(msg);
      $("chat-channel").querySelector('option[value="org"]').hidden = !msg.mine;
      break;
    case "admin_data":
      adminUI.update(msg);
      break;
    case "admin_result":
      adminUI.showResult(msg);
      break;
    case "announce":
      showAnnouncement(msg.text, msg.by);
      break;
    case "kick":
      kickText = msg.text;
      break;
    case "error":
      $("login-error").textContent = msg.msg;
      break;
    case "snap":
      applySnapshot(msg);
      break;
    case "chat":
      addChatLine(msg.text, msg.from, msg.ch ? { name: msg.ch, color: msg.color } : null);
      break;
    case "sys":
      addChatLine(msg.text);
      break;
  }
}

// A equipe recarregou o conteúdo: definições novas (skills, itens, classes...) sem cair
function applyContent(msg) {
  state.defs = {
    skills: msg.skills, effects: msg.effects, enemies: msg.enemies, races: msg.races, classes: msg.classes,
    items: msg.items, orgs: msg.orgs, roles: msg.roles, npcs: msg.npcs, quests: msg.quests,
  };
  renderer.defs = state.defs;
  if (renderer3d) renderer3d.defs = state.defs;
  questLog.refresh();
  skillsWindow.lastKey = "";
  skillsWindow.update();
  if (trainerWindow.isOpen) trainerWindow.render();
  if (craftWindow.isOpen) craftWindow.render();
  lastHotbarKey = "";
  lastStatsKey = "";
  inventoryUI.lastKey = "";
  if (inventoryUI.isOpen) inventoryUI.render();
}

// Atravessou um portal: troca o mapa desenhado e a física, e recomeça a previsão
function changeMap(msg) {
  renderer.setMap(msg.map);
  currentMap = msg.map;
  renderer3d?.setMap(msg.map);
  physics = new Physics(msg.map, entityHalf, tickRate);
  pending = [];
  pred = { x: msg.x, y: msg.y, f: pred?.f ?? "down" };
  state.ents.clear();
  state.projs = [];
  state.drops = [];
  state.swings = [];
  state.rings = [];
  state.trails = [];
  state.floaters = [];
}

let kickText = null; // motivo de expulsão/ban, mostrado quando a conexão cair
let announceTimer = null;

function showAnnouncement(text, by) {
  const box = $("announce");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => (box.hidden = true), 7000);
  addChatLine(`📢 ${text} (${by})`);
}

function handleClose() {
  inGame = false;
  clearInterval(tickTimer);
  adminUI.toggle(false);
  orgWindow.toggle(false);
  hideNpcWindows();
  skillsWindow.toggle(false);
  invitePanel.update(null);
  partyInvitePanel.update(null);
  partyPanel.update(null);
  state.partyIds = new Set();
  $("overlay").classList.remove("hidden");
  $("login-error").textContent = kickText ?? "Conexão perdida. Entre de novo.";
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
  state.drops = msg.drops ?? [];
  state.snapTime = now;
  for (const fx of msg.fx) handleFx(fx, now);

  state.you = msg.you;
  reconcile(msg.you);
  inventoryUI.update(msg.you.inv, msg.you.icd);
  shopWindow.refresh();
  trainerWindow.refresh();
  craftWindow.refresh();
  skillsWindow.update(msg.you.known);
  if ("party" in msg.you) {  // só vem quando muda (null = sem grupo)
    partyPanel.update(msg.you.party);
    state.partyIds = partyPanel.memberIds;
  }
  if (msg.you.q) {
    questLog.update(msg.you.q);
    state.questMarks = msg.you.q.marks ?? {};
  }
  updateHud();
}

// Corpos que bloqueiam o movimento previsto: posições do último snapshot (metade do corpo em
// e.h quando é maior que o normal, como chefes e elites). Quem se move pode já estar um pouco em outro lugar no servidor; aí a
// correção chega no snapshot seguinte e é suavizada no desenho.
function solids() {
  const list = [];
  for (const e of state.ents.values()) {
    if (e.id !== state.myId && e.s !== "dead" && solidKinds.has(e.k)) list.push([e.x, e.y, e.h ?? entityHalf]);
  }
  return list;
}

// Parte da posição confirmada pelo servidor e reaplica os comandos ainda não confirmados.
function reconcile(you) {
  while (pending.length && pending[0].seq <= you.ack) pending.shift();
  const me = state.ents.get(state.myId);
  const facing = pending.length && pred ? pred.f : me?.f ?? "down";
  pred = { x: you.x, y: you.y, f: facing };
  const bodies = pending.length ? solids() : [];
  for (const cmd of pending) physics.step(pred, cmd.keys, you.spd, bodies);
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
    case "pick":
      if (fx.id === state.myId) {
        const color = RARITY_COLORS[fx.r] ?? RARITY_COLORS.common;
        state.floaters.push({ x: fx.x, y: fx.y - 6, text: fx.t, color, born: now, ttl: 1500 });
      }
      break;
    case "lvl":
      state.floaters.push({ x: fx.x, y: fx.y - 12, text: "Nível +1", color: "#ffd65a", born: now, ttl: 1400 });
      break;
    case "learn":
      if (fx.id === state.myId) {
        state.floaters.push({ x: fx.x, y: fx.y - 18, text: `Nova skill: ${fx.t}`, color: "#b8e0ff", born: now, ttl: 2000 });
      }
      break;
    case "quest":
      if (fx.id === state.myId) {
        state.floaters.push({ x: fx.x, y: fx.y - 18, text: "Missão concluída!", color: "#ffe9b0", born: now, ttl: 2000 });
      }
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
  // na visão 3D a câmera pode estar girada: "cima" é para longe da câmera
  const keys = active3d() ? rotateKeys(input.sample(), renderer3d.yawSteps) : input.sample();
  if (!keys.up && !keys.down && !keys.left && !keys.right) return;
  seq += 1;
  conn.send({ t: "move", seq, ...keys });
  pending.push({ seq, keys });
  physics.step(pred, keys, state.you.spd, solids());
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
  state.myLevel = playerLevel(you);
  $("me-class").textContent = [raceName, className, `Nv ${state.myLevel}`].filter(Boolean).join(" · ");
  updateBossBar();
  const orgEl = $("me-org");
  orgEl.hidden = !me.o;
  if (me.o) {
    const org = state.defs.orgs[me.o[0]];
    orgEl.style.setProperty("--org", org?.color ?? "#fff");
    orgEl.textContent = `${org?.name ?? me.o[0]} · ${rankTitle(state.defs, me.o)}`;
  }

  const zone = you.zone ?? { t: "pve", n: "" };
  const zoneEl = $("me-zone");
  zoneEl.className = zone.t;
  zoneEl.textContent = `${zone.m ? `${zone.m} · ` : ""}${ZONE_LABELS[zone.t] ?? zone.t}${zone.n ? ` — ${zone.n}` : ""}`;

  const duelEl = $("me-duel");
  duelEl.hidden = !you.duel;
  if (you.duel) {
    const { with: rival, starts_in: startsIn, left } = you.duel;
    const clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    $("me-duel-text").textContent = startsIn > 0
      ? `Duelo contra ${rival} começa em ${Math.ceil(startsIn)}…`
      : `Duelo contra ${rival} · ${clock}`;
  }
  invitePanel.update(you.invite);
  partyInvitePanel.update(you.pinv);
  // canal "Organização" no chat só para quem é de uma
  const orgOption = $("chat-channel").querySelector('option[value="org"]');
  orgOption.hidden = !me.o;
  if (!me.o && $("chat-channel").value === "org") $("chat-channel").value = "";
  $("chat-channel").querySelector('option[value="party"]').hidden = !partyPanel.inParty;
  if (!partyPanel.inParty && $("chat-channel").value === "party") $("chat-channel").value = "";
  setBar("bar-hp", you.hp, you.mhp, "Vida");
  setBar("bar-energy", you.en, you.men, "Energia");
  setBar("bar-fatigue", you.fat, you.mfat, "Cansaço");
  $("gold").textContent = `${you.gold} moedas`;
  $("admin-open").hidden = !you.staff;
  if (!you.staff && adminUI.isOpen) adminUI.toggle(false);

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

  const hotbarKey = JSON.stringify(you.skills);
  if (hotbarKey !== lastHotbarKey) {
    lastHotbarKey = hotbarKey;
    buildHotbar(you.skills);
  }
}

// Nível do personagem: a média dos níveis dos atributos (é com ele que o nível dos inimigos se compara)
function playerLevel(you) {
  const levels = Object.values(you.stats ?? {}).map((s) => s.lv);
  return levels.length ? Math.floor(levels.reduce((a, b) => a + b, 0) / levels.length) : 1;
}

// Barra de vida do chefe (topo da tela): só quando ele está perto ou já em combate (ferido)
const BOSS_BAR_RANGE = 420;
function updateBossBar() {
  const bar = $("bossbar");
  const me = state.ents.get(state.myId);
  let boss = null;
  let bestDist = Infinity;
  for (const e of state.ents.values()) {
    if (!e.boss || e.s === "dead" || !me) continue;
    const d = Math.hypot(e.x - me.x, e.y - me.y);
    if ((d <= BOSS_BAR_RANGE || e.hp < e.mhp) && d < bestDist) {
      boss = e;
      bestDist = d;
    }
  }
  bar.hidden = !boss;
  if (!boss) return;
  const phases = state.defs.enemies[boss.t]?.phases ?? 0;
  bar.querySelector(".name").textContent = `${boss.n} · Nv ${boss.lv}` + (phases ? ` · fase ${boss.ph + 1}/${phases + 1}` : "");
  bar.querySelector(".fill").style.width = `${Math.max(0, Math.min(100, (100 * boss.hp) / boss.mhp))}%`;
  bar.querySelector(".hp").textContent = `${boss.hp} / ${boss.mhp}`;
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

// Barra de atalhos: todos os espaços, inclusive os vazios. Dá para arrastar skills da
// janela de skills (K) para cá, ou de um espaço para outro (trocam de lugar).
function buildHotbar(skills) {
  $("hotbar").replaceChildren(
    ...skills.map((skillId, slot) => {
      const def = skillId ? state.defs.skills[skillId] : null;
      const btn = document.createElement("button");
      btn.className = def ? "slot" : "slot empty";
      btn.type = "button";
      btn.innerHTML = '<span class="cd"></span><span class="key"></span><span class="name"></span>';
      btn.querySelector(".key").textContent = SLOT_LABELS[slot];
      if (def) {
        btn.dataset.skill = skillId;
        btn.title = `${def.name} — ${skillMeta(def)}`;
        btn.querySelector(".name").textContent = def.name;
        btn.style.setProperty("--skill-color", def.color);
        makeDraggable(btn, skillId);
      } else {
        btn.title = "Espaço vazio: abra as skills (K) para escolher";
      }
      btn.addEventListener("click", () => {
        if (def) useSlot(slot);
        else toggleSkills(true);
        btn.blur();
      });
      makeDropTarget(btn, (id) => conn?.send({ t: "skills", op: "bar", slot, id }));
      return btn;
    }),
  );
}

function updateHotbarCooldowns(now) {
  const you = state.you;
  if (!you) return;
  for (const btn of $("hotbar").children) {
    const def = state.defs.skills[btn.dataset.skill];
    if (!def) continue; // espaço vazio
    const left = cooldownLeft(btn.dataset.skill, now);
    btn.querySelector(".cd").style.height = `${def.cd > 0 ? (100 * left) / def.cd : 0}%`;
    btn.classList.toggle("no-energy", you.en < def.cost);
  }
}

function addChatLine(text, from = null, channel = null) {
  const log = $("chat-log");
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 30;
  const line = document.createElement("div");
  if (channel) {
    const tag = document.createElement("span");
    tag.className = "channel";
    tag.style.setProperty("--org", channel.color);
    tag.textContent = `[${channel.name}] `;
    line.append(tag);
  }
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
    if (text && conn) conn.send({ t: "chat", text, ch: $("chat-channel").value || undefined });
    chatInput.value = "";
    chatInput.blur();
  } else if (ev.key === "Escape") {
    chatInput.blur();
  }
});

const inventoryUI = new InventoryUI({ getDefs: () => state.defs, send: (msg) => conn?.send(msg) });
const adminUI = new AdminUI({ getDefs: () => state.defs, send: (msg) => conn?.send(msg) });
$("admin-open").addEventListener("click", (ev) => {
  adminUI.toggle();
  ev.currentTarget.blur();
});

const dialogBox = new DialogBox({ getDefs: () => state.defs, send: (msg) => conn?.send(msg) });
const questLog = new QuestLog({ getDefs: () => state.defs, send: (msg) => conn?.send(msg) });
const shopWindow = new ShopWindow({
  getDefs: () => state.defs,
  getInventory: () => inventoryUI.inv,
  send: (msg) => conn?.send(msg),
});
const trainerWindow = new TrainerWindow({
  getDefs: () => state.defs,
  getGold: () => state.you?.gold ?? 0,
  send: (msg) => conn?.send(msg),
  heading: npcHeading,
});
const skillsWindow = new SkillsWindow({
  getDefs: () => state.defs,
  getYou: () => state.you,
  send: (msg) => conn?.send(msg),
});
const craftWindow = new CraftWindow({
  getDefs: () => state.defs,
  getInventory: () => inventoryUI.inv,
  send: (msg) => conn?.send(msg),
  heading: npcHeading,
});
// Janelas abertas por NPCs: conversa, loja, treinador e oficina (uma de cada vez)
const npcWindows = () => [dialogBox, shopWindow, trainerWindow, craftWindow];
function hideNpcWindows() {
  for (const win of npcWindows()) win.hide();
}
function closeNpc() {
  if (npcWindows().some((win) => win.isOpen)) conn?.send({ t: "npc", op: "close" });
  hideNpcWindows();
}
// Tecla E: conversa com o NPC mais próximo, se houver um ao alcance
function talkToNearest() {
  if (!pred) return;
  let best = null;
  let bestDist = npcRange;
  for (const e of state.ents.values()) {
    if (e.k !== "npc") continue;
    const d = Math.hypot(e.x - pred.x, e.y - pred.y);
    if (d <= bestDist) {
      best = e;
      bestDist = d;
    }
  }
  if (best) conn?.send({ t: "npc", op: "talk", id: best.id });
  else addChatLine("Não há ninguém por perto para conversar.");
}
$("inv-open").addEventListener("click", (ev) => {
  toggleInventory();
  ev.currentTarget.blur();
});

const orgWindow = new OrgWindow({ getDefs: () => state.defs, send: (msg) => conn?.send(msg) });
// Inventário, organização, diário, skills, loja e treinador ocupam o mesmo canto: abrir um fecha os outros
const cornerWindows = () => [inventoryUI, orgWindow, questLog, skillsWindow];
function closeCornerWindows() {
  for (const win of cornerWindows()) win.toggle(false);
}
function openInCorner(win, force) {
  win.toggle(force);
  if (!win.isOpen) return;
  for (const other of cornerWindows()) if (other !== win) other.toggle(false);
  if (shopWindow.isOpen || trainerWindow.isOpen || craftWindow.isOpen) closeNpc();
}
const toggleOrg = () => openInCorner(orgWindow);
const toggleInventory = () => openInCorner(inventoryUI);
const toggleQuests = () => openInCorner(questLog);
const toggleSkills = (force) => openInCorner(skillsWindow, force);
$("skills-open").addEventListener("click", (ev) => {
  toggleSkills();
  ev.currentTarget.blur();
});
$("quests-open").addEventListener("click", (ev) => {
  toggleQuests();
  ev.currentTarget.blur();
});
$("org-open").addEventListener("click", (ev) => {
  toggleOrg();
  ev.currentTarget.blur();
});
const invitePanel = new InvitePanel({ root: $("invite"), send: (msg) => conn?.send(msg) });
const partyInvitePanel = new PartyInvitePanel({ root: $("party-invite"), send: (msg) => conn?.send(msg) });
const partyPanel = new PartyPanel({
  root: $("party"),
  send: (msg) => conn?.send(msg),
  getMyId: () => state.myId,
  getMapId: () => currentMap?.id,
});
$("duel-forfeit").addEventListener("click", (ev) => {
  if (window.confirm("Desistir do duelo? Conta como derrota.")) conn?.send({ t: "duel", op: "forfeit" });
  ev.currentTarget.blur();
});
const playerMenu = new PlayerMenu({
  container: $("viewport"),
  getDefs: () => state.defs,
  getMe: () => state.ents.get(state.myId),
  getYou: () => state.you,
  send: (msg) => conn?.send(msg),
  openAdmin: (name) => adminUI.openFor(name),
  extraItems: (target) => partyMenuItems(partyPanel, target, (msg) => conn?.send(msg)),
});

// O que está sob o mouse: outro jogador ou um NPC (tolerância de 22 px)
function clickableAt(ev) {
  if (active3d()) {
    const candidates = [...state.ents.values()].filter(
      (e) => e.id !== state.myId && e.s !== "dead" && (e.k === "player" || e.k === "npc"),
    );
    return renderer3d.pickEntity(ev, candidates);
  }
  const { x, y } = screenToWorld(renderer, ev);
  let target = null;
  let best = 22;
  for (const e of state.ents.values()) {
    if (e.id === state.myId || e.s === "dead" || (e.k !== "player" && e.k !== "npc")) continue;
    const d = Math.hypot(e.rx - x, e.ry - y);
    if (d < best) {
      best = d;
      target = e;
    }
  }
  return target;
}

// Clique num NPC: conversa. Clique num jogador: menu com o que dá para fazer com ele.
$("canvas").addEventListener("click", (ev) => {
  if (!inGame) return;
  const target = clickableAt(ev);
  if (!target) return;
  if (target.k === "npc") {
    conn?.send({ t: "npc", op: "talk", id: target.id });
    return;
  }
  const box = $("viewport").getBoundingClientRect();
  playerMenu.open(target, ev.clientX - box.left, ev.clientY - box.top);
});
// Rodinha do mouse: zoom da câmera 3D
$("canvas").addEventListener("wheel", (ev) => {
  if (!active3d()) return;
  ev.preventDefault();
  renderer3d.zoomBy(ev.deltaY);
}, { passive: false });
$("view-toggle").addEventListener("click", (ev) => {
  setView(view === "3d" ? "2d" : "3d");
  ev.currentTarget.blur();
});
if (savedView() === "3d") setView("3d");
$("canvas").addEventListener("mousemove", (ev) => {
  $("canvas").style.cursor = inGame && clickableAt(ev) ? "pointer" : "";
});

const input = setupInput({
  enabled: () => inGame,
  onSlot: useSlot,
  onAction: (action) => conn?.send({ t: action }),
  onLocal: (name) => {
    if (name === "inventory") toggleInventory();
    else if (name === "org") toggleOrg();
    else if (name === "admin" && state.you?.staff) adminUI.toggle();
    else if (name === "talk") talkToNearest();
    else if (name === "quests") toggleQuests();
    else if (name === "skills") toggleSkills();
    else if (name === "view") setView(view === "3d" ? "2d" : "3d");
    else if (name === "rotate-left" || name === "rotate-right") renderer3d?.rotate(name === "rotate-left" ? -1 : 1);
    else if (name === "escape") {
      closeCornerWindows();
      adminUI.toggle(false);
      playerMenu.close();
      closeNpc();
    }
  },
  onEnter: () => chatInput.focus(),
});

// Para depurar pelo console do navegador: __game.state, __game.pred, __game.pending
window.__game = {
  state,
  get pred() { return pred; },
  get pending() { return pending; },
  get renderer3d() { return renderer3d; },
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

  (active3d() ? renderer3d : renderer).draw(state, now);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
