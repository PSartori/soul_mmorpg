// Janelas de serviços abertas por NPCs: conserto de equipamento e viagem rápida.
// Só mostram e enviam pedidos; preços e regras são do servidor (server/upkeep.py, server/travel.py).
//   conserto ("repair: true" em data/npcs.yaml):
//     servidor -> {t: "repair", npc, name, title, color, items: [{where: "eq"|"bag", slot, id, cond, price}]}
//     {t: "npc", op: "repair", where, slot}  ·  {t: "npc", op: "repair_all"}
//   viagem ("travel: true"):
//     servidor -> {t: "travel", npc, name, title, color, stops: [{id, npc, title, map, price, known}]}
//     {t: "npc", op: "travel", to: id}
import { RARITY_COLORS, describe } from "./inventory.js";

const SLOT_NAMES = { weapon: "Arma", armor: "Armadura", accessory: "Acessório" };

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
    onClick(ev);
    b.blur();
  });
  return b;
};

class ServiceWindow {
  constructor({ getDefs, getGold, send, heading }, id) {
    this.getDefs = getDefs;
    this.getGold = getGold;
    this.send = send;
    this.heading = heading;
    this.root = document.getElementById(id);
    this.root.hidden = true;
    this.data = null;
    this.lastGold = null;
  }

  get isOpen() {
    return !this.root.hidden;
  }

  show(msg) {
    this.data = msg;
    this.root.hidden = false;
    this.render();
  }

  hide() {
    this.root.hidden = true;
    this.data = null;
  }

  // As moedas mudaram: os botões que dá ou não para pagar mudam de cor
  refresh() {
    if (this.isOpen && this.getGold() !== this.lastGold) this.render();
  }

  header(title, closeLabel) {
    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", closeLabel);
    const box = el("div", "shop-title");
    box.append(el("h3", "", title), this.heading(this.data));
    const header = el("header");
    header.append(box, el("span", "shop-gold", `${this.getGold()} moedas`), close);
    return header;
  }
}

export class RepairWindow extends ServiceWindow {
  constructor(opts) {
    super(opts, "repair");
  }

  render() {
    const msg = this.data;
    if (!msg) return;
    const defs = this.getDefs();
    const gold = this.getGold();
    this.lastGold = gold;
    const box = el("section", "shop-box");
    box.append(el("h4", "", "Equipamento desgastado"));
    if (!msg.items.length) box.append(el("p", "hint", "Está tudo em bom estado."));
    for (const entry of msg.items) {
      const def = defs.items[entry.id];
      if (!def) continue;
      const row = el("div", "shop-row");
      const name = el("span", "item", def.name);
      name.style.color = RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common;
      const where = entry.where === "eq" ? `${SLOT_NAMES[entry.slot] ?? entry.slot} (equipado)` : "na bolsa";
      const state = el("span", entry.cond === 0 ? "price bad" : "price", entry.cond === 0 ? "quebrado" : `${entry.cond}%`);
      const fix = button(`Consertar · ${entry.price}`,
        () => this.send({ t: "npc", op: "repair", where: entry.where, slot: entry.slot }), "small");
      if (gold < entry.price) fix.classList.add("poor");
      row.append(name, el("span", "hint", where), state, fix);
      row.addEventListener("mouseenter", () => this.showInfo(def, defs, entry.cond));
      box.append(row);
    }
    const parts = [this.header("Conserto", "Fechar conserto"), box];
    if (msg.items.length > 1) {
      const total = msg.items.reduce((sum, e) => sum + e.price, 0);
      const all = button(`Consertar tudo · ${total}`, () => this.send({ t: "npc", op: "repair_all" }), "small");
      if (gold < total) all.classList.add("poor");
      const footer = el("div", "service-footer");
      footer.append(all);
      parts.push(footer);
    }
    parts.push(el("p", "hint", "Quebrado não dá bônus. O preço depende do valor do item e de quanto ele se desgastou."));
    this.info = el("div", "shop-info", "Passe o mouse sobre um item.");
    parts.push(this.info);
    this.root.replaceChildren(...parts);
  }

  showInfo(def, defs, cond) {
    this.info.replaceChildren();
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(el("div", "name", def.name),
      ...describe(def, defs, cond).map(([cls, text]) => el("div", cls, text)));
  }
}

export class TravelWindow extends ServiceWindow {
  constructor(opts) {
    super(opts, "travel");
  }

  render() {
    const msg = this.data;
    if (!msg) return;
    const gold = this.getGold();
    this.lastGold = gold;
    const box = el("section", "shop-box");
    box.append(el("h4", "", "Destinos"));
    if (!msg.stops.length) box.append(el("p", "hint", "Não há para onde levar daqui."));
    for (const stop of msg.stops) {
      const row = el("div", `shop-row${stop.known ? "" : " locked"}`);
      const place = el("span", "item", stop.map);
      const who = el("span", "hint", stop.title ? `${stop.npc} · ${stop.title}` : stop.npc);
      row.append(place, who);
      if (stop.known) {
        const go = button(`Viajar · ${stop.price}`, () => this.send({ t: "npc", op: "travel", to: stop.id }), "small");
        if (gold < stop.price) go.classList.add("poor");
        row.append(go);
      } else {
        row.append(el("span", "hint", "ainda não visitado"));
      }
      box.append(row);
    }
    this.root.replaceChildren(
      this.header("Viagem", "Fechar viagem"),
      box,
      el("p", "hint", "Só para lugares onde você já esteve. Quanto mais longe, mais caro. Em combate, não dá."),
    );
  }
}
