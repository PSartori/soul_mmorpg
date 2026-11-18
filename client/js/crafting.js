// Oficina: criação de itens a partir de materiais. Só mostra e envia pedidos
// ({t: "npc", op: "craft", i, times}); quem confere materiais, moedas, requisitos e
// espaço é o servidor (server/crafting.py). Os materiais que o jogador tem vêm do
// inventário, por isso a janela se refaz quando ele muda.
import { RARITY_COLORS, describe } from "./inventory.js";

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

// Quantas unidades de cada item há na bolsa
function countItems(inv) {
  const counts = {};
  for (const entry of inv?.slots ?? []) {
    if (entry) counts[entry[0]] = (counts[entry[0]] ?? 0) + entry[1];
  }
  return counts;
}

export class CraftWindow {
  constructor({ getDefs, getInventory, send, heading }) {
    this.getDefs = getDefs;
    this.getInventory = getInventory;
    this.send = send;
    this.heading = heading; // nome do NPC + título (o mesmo da conversa e da loja)
    this.root = document.getElementById("craft");
    this.root.hidden = true;
    this.data = null;
    this.lastKey = "";
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // msg: {npc, name, title, color, station, recipes: [{i, make, qty, needs: [[item, qtd]], price, missing}]}
  show(msg) {
    this.data = msg;
    this.root.hidden = false;
    this.lastKey = "";
    this.render();
  }

  hide() {
    this.root.hidden = true;
    this.data = null;
  }

  // O inventário mudou (criou, pegou do chão...): refaz contagens e botões
  refresh() {
    if (!this.isOpen) return;
    if (JSON.stringify(this.getInventory()) !== this.lastKey) this.render();
  }

  render() {
    const msg = this.data;
    if (!msg) return;
    const focused = document.activeElement;
    if (this.root.contains(focused) && focused.tagName === "INPUT") return; // não apaga o que está digitando
    const defs = this.getDefs();
    const inv = this.getInventory();
    this.lastKey = JSON.stringify(inv);
    const have = countItems(inv);
    const gold = inv?.gold ?? 0;

    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", "Fechar oficina");
    const header = el("header");
    const title = el("div", "shop-title");
    title.append(el("h3", "", msg.station), this.heading(msg));
    header.append(title, el("span", "shop-gold", `${gold} moedas`), close);

    this.info = el("div", "shop-info", "Passe o mouse sobre um item para ver os detalhes.");
    const box = el("section", "craft-box");
    if (!msg.recipes.length) box.append(el("p", "hint", "Nada para criar aqui, por enquanto."));
    for (const recipe of msg.recipes) {
      const out = defs.items[recipe.make];
      if (!out) continue;
      // quantas vezes dá para criar com o que tem (materiais e moedas)
      let possible = Math.min(
        ...recipe.needs.map(([id, qty]) => Math.floor((have[id] ?? 0) / qty)),
        recipe.price > 0 ? Math.floor(gold / recipe.price) : Infinity,
      );
      if (recipe.missing.length) possible = 0;

      const row = el("div", `craft-row${possible > 0 ? "" : " unable"}`);
      const name = el("div", "craft-out", recipe.qty > 1 ? `${out.name} ×${recipe.qty}` : out.name);
      name.style.color = RARITY_COLORS[out.rarity] ?? RARITY_COLORS.common;
      name.addEventListener("mouseenter", () => this.showInfo(out, defs));
      row.append(name);

      const needs = el("ul", "craft-needs");
      for (const [id, qty] of recipe.needs) {
        const def = defs.items[id];
        const count = have[id] ?? 0;
        const li = el("li", count >= qty ? "ok" : "short", `${def?.name ?? id} ${count}/${qty}`);
        if (def) li.addEventListener("mouseenter", () => this.showInfo(def, defs));
        needs.append(li);
      }
      if (recipe.price > 0) needs.append(el("li", gold >= recipe.price ? "ok gold" : "short", `${recipe.price} moedas`));
      row.append(needs);

      if (recipe.missing.length) {
        const list = el("ul", "missing");
        for (const text of recipe.missing) list.append(el("li", "", text));
        row.append(el("div", "why", "Falta:"), list);
      }

      const actions = el("div", "craft-actions");
      const qty = el("input");
      qty.type = "number";
      qty.min = "1";
      qty.max = String(Math.max(1, possible));
      qty.value = "1";
      qty.disabled = possible <= 1;
      qty.setAttribute("aria-label", `Quantas vezes criar ${out.name}`);
      qty.addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
      const make = button("Criar", () => {
        const times = Math.max(1, Math.min(possible, Math.floor(Number(qty.value) || 1)));
        this.send({ t: "npc", op: "craft", i: recipe.i, times });
        qty.blur();
      }, "small");
      make.disabled = possible <= 0;
      actions.append(qty, make);
      if (possible > 1) {
        actions.append(button(`Tudo (${possible})`, () => this.send({ t: "npc", op: "craft", i: recipe.i, times: possible }),
          "small"));
      }
      row.append(actions);
      box.append(row);
    }
    this.root.replaceChildren(header, box, this.info);
  }

  showInfo(def, defs) {
    this.info.replaceChildren();
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(el("div", "name", def.name), ...describe(def, defs).map(([cls, text]) => el("div", cls, text)));
  }
}
