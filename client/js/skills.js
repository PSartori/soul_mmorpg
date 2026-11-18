// Skills: janela das skills conhecidas e da barra (tecla K) e janela do treinador.
// Só mostra e envia pedidos; quem decide o que pode ir para a barra, o que falta para
// aprender e se a compra vale é o servidor (server/training.py).
//   barra: {t: "skills", op: "bar", slot, id}   (id null = esvazia o espaço)
//   aprender: {t: "npc", op: "learn", skill}
import { SLOT_LABELS } from "./input.js";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const button = (text, onClick, className = "") => {
  const b = el("button", className, text);
  b.type = "button";
  b.addEventListener("click", (ev) => {
    ev.stopPropagation();
    onClick(ev);
    b.blur();
  });
  return b;
};

const TYPE_LABELS = {
  melee: "Corpo a corpo",
  projectile: "Projétil",
  area: "Em área",
  dash: "Deslocamento",
  self: "Em si mesmo",
};

// "Projétil · 10 energia · recarga 0.8 s"
export function skillMeta(def) {
  const parts = [TYPE_LABELS[def.type] ?? def.type];
  parts.push(def.cost > 0 ? `${def.cost} energia` : "sem custo");
  parts.push(`recarga ${def.cd} s`);
  return parts.join(" · ");
}

// "Só para: Desperto, Espectral" (ou vazio, se todos podem usar)
export function skillRestriction(def, defs) {
  const names = def.classes?.length
    ? def.classes.map((c) => defs.classes[c]?.name ?? c)
    : (def.races ?? []).map((r) => defs.races[r]?.name ?? r);
  return names.length ? `Só para: ${names.join(", ")}` : "";
}

// Arrastar uma skill (da lista ou da barra) leva o id dela
const DRAG_TYPE = "application/x-skill";
export function makeDraggable(node, skillId) {
  node.draggable = true;
  node.addEventListener("dragstart", (ev) => {
    ev.dataTransfer.setData(DRAG_TYPE, skillId);
    ev.dataTransfer.effectAllowed = "move";
  });
}
export function makeDropTarget(node, onDrop) {
  node.addEventListener("dragover", (ev) => {
    if (!ev.dataTransfer.types.includes(DRAG_TYPE)) return;
    ev.preventDefault();
    node.classList.add("drop");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drop"));
  node.addEventListener("drop", (ev) => {
    node.classList.remove("drop");
    const skillId = ev.dataTransfer.getData(DRAG_TYPE);
    if (!skillId) return;
    ev.preventDefault();
    onDrop(skillId);
  });
}

// ------------------------------------------------------------------ janela de skills (K)

export class SkillsWindow {
  constructor({ getDefs, getYou, send }) {
    this.getDefs = getDefs;
    this.getYou = getYou;
    this.send = send;
    this.root = document.getElementById("skills");
    this.root.hidden = true;
    this.known = []; // [{id, ok}] (you.known, só chega quando muda)
    this.selected = null; // skill escolhida com clique, esperando um espaço da barra
    this.lastKey = "";
  }

  get isOpen() {
    return !this.root.hidden;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    this.selected = null;
    this.lastKey = "";
    if (this.isOpen) this.render();
  }

  update(known) {
    if (known) this.known = known;
    if (!this.isOpen) return;
    const key = JSON.stringify([this.known, this.getYou()?.skills, this.selected]);
    if (key !== this.lastKey) this.render();
  }

  // Põe a skill no espaço (o servidor troca de lugar se ela já estiver na barra)
  place(slot, skillId) {
    this.send({ t: "skills", op: "bar", slot, id: skillId });
    this.selected = null;
    this.lastKey = "";
  }

  render() {
    const defs = this.getDefs();
    const bar = this.getYou()?.skills ?? [];
    this.lastKey = JSON.stringify([this.known, bar, this.selected]);

    const close = button("✕", () => this.toggle(false), "close");
    close.setAttribute("aria-label", "Fechar");
    const header = el("header");
    header.append(el("h3", "", "Skills"), close);

    // --- barra
    const barBox = el("section", "skill-box");
    barBox.append(el("h4", "", "Barra de atalhos"));
    const slots = el("div", "skill-bar");
    for (let slot = 0; slot < bar.length; slot++) {
      const skillId = bar[slot];
      const def = skillId ? defs.skills[skillId] : null;
      const cell = el("div", `bar-cell${def ? "" : " empty"}${this.selected ? " target" : ""}`);
      cell.style.setProperty("--skill-color", def?.color ?? "transparent");
      cell.append(el("span", "key", SLOT_LABELS[slot] ?? String(slot + 1)), el("span", "name", def?.name ?? "vazio"));
      if (def) {
        makeDraggable(cell, skillId);
        const clear = button("×", () => this.place(slot, null), "clear");
        clear.title = "Tirar da barra";
        clear.setAttribute("aria-label", `Tirar ${def.name} da barra`);
        cell.append(clear);
      }
      cell.addEventListener("click", () => {
        if (this.selected) this.place(slot, this.selected);
      });
      makeDropTarget(cell, (id) => this.place(slot, id));
      slots.append(cell);
    }
    barBox.append(slots);
    barBox.append(el("p", "hint", this.selected
      ? `Agora clique num espaço da barra para pôr ${defs.skills[this.selected]?.name ?? ""}.`
      : "Arraste uma skill para a barra, ou clique nela e depois num espaço. Em combate a barra fica travada."));

    // --- skills conhecidas
    const listBox = el("section", "skill-box");
    listBox.append(el("h4", "", `Skills conhecidas (${this.known.length})`));
    for (const { id, ok } of this.known) {
      const def = defs.skills[id];
      if (!def) continue;
      const onBar = bar.includes(id);
      const row = el("div", `skill-row${ok ? "" : " locked"}${this.selected === id ? " selected" : ""}`);
      row.style.setProperty("--skill-color", def.color);
      const info = el("div", "info");
      const title = el("div", "title");
      title.append(el("b", "", def.name));
      if (onBar) title.append(el("span", "tag", `tecla ${SLOT_LABELS[bar.indexOf(id)]?.split(" ")[0]}`));
      if (!ok) title.append(el("span", "tag locked", "bloqueada"));
      info.append(title, el("div", "meta", skillMeta(def)));
      if (def.desc) info.append(el("div", "desc", def.desc));
      const restriction = skillRestriction(def, defs);
      if (restriction) info.append(el("div", ok ? "meta" : "why", restriction));
      row.append(el("span", "swatch"), info);
      if (ok) {
        makeDraggable(row, id);
        row.title = "Clique para escolher, depois clique num espaço da barra";
        row.addEventListener("click", () => {
          this.selected = this.selected === id ? null : id;
          this.render();
        });
        if (!onBar && bar.includes(null)) {
          row.append(button("Pôr na barra", () => this.place(bar.indexOf(null), id), "small"));
        }
      }
      listBox.append(row);
    }
    if (!this.known.length) listBox.append(el("p", "hint", "Você ainda não conhece nenhuma skill."));
    listBox.append(el("p", "hint", "Aprenda mais com os treinadores (converse com eles: E)."));

    this.root.replaceChildren(header, barBox, listBox);
  }
}

// ------------------------------------------------------------------ treinador

export class TrainerWindow {
  constructor({ getDefs, getGold, send, heading }) {
    this.getDefs = getDefs;
    this.getGold = getGold;
    this.send = send;
    this.heading = heading; // nome do NPC + título (o mesmo da conversa e da loja)
    this.root = document.getElementById("trainer");
    this.root.hidden = true;
    this.data = null;
    this.lastGold = null;
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // msg: {npc, name, title, color, trainer, teaches: [{id, price, known, missing: [texto]}]}
  show(msg) {
    this.data = msg;
    this.root.hidden = false;
    this.render();
  }

  hide() {
    this.root.hidden = true;
    this.data = null;
  }

  // As moedas mudaram: os botões de aprender podem ter mudado
  refresh() {
    if (this.isOpen && this.getGold() !== this.lastGold) this.render();
  }

  render() {
    const msg = this.data;
    if (!msg) return;
    const defs = this.getDefs();
    const gold = this.getGold();
    this.lastGold = gold;

    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", "Fechar treinador");
    const header = el("header");
    const title = el("div", "shop-title");
    title.append(el("h3", "", msg.trainer), this.heading(msg));
    header.append(title, el("span", "shop-gold", `${gold} moedas`), close);

    const box = el("section", "skill-box");
    for (const entry of msg.teaches) {
      const def = defs.skills[entry.id];
      if (!def) continue;
      const ready = !entry.known && !entry.missing.length;
      const row = el("div", `skill-row${entry.known ? " known" : ready ? "" : " locked"}`);
      row.style.setProperty("--skill-color", def.color);
      const info = el("div", "info");
      const name = el("div", "title");
      name.append(el("b", "", def.name));
      if (entry.known) name.append(el("span", "tag", "já conhece"));
      info.append(name, el("div", "meta", skillMeta(def)));
      if (def.desc) info.append(el("div", "desc", def.desc));
      if (!entry.known && entry.missing.length) {
        const list = el("ul", "missing");
        for (const text of entry.missing) list.append(el("li", "", text));
        info.append(el("div", "why", "Falta:"), list);
      }
      row.append(el("span", "swatch"), info);
      if (!entry.known) {
        const price = entry.price > 0 ? `${entry.price} moedas` : "grátis";
        const learn = button(`Aprender · ${price}`, () => this.send({ t: "npc", op: "learn", skill: entry.id }), "small");
        learn.disabled = !ready || gold < entry.price;
        if (ready && gold < entry.price) learn.title = `Faltam ${entry.price - gold} moedas`;
        row.append(learn);
      }
      box.append(row);
    }
    const hint = el("p", "hint", "Skills aprendidas vão para a janela de skills (K), onde você escolhe a barra.");
    this.root.replaceChildren(header, box, hint);
  }
}
