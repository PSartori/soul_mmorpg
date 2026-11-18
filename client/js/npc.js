// Conversa com NPCs e janela de loja. Só mostra e envia pedidos ({t: "npc", op: ...});
// quem decide o que aparece, o preço e se a compra/venda vale é o servidor.
import { RARITY_COLORS, describe } from "./inventory.js";
import { questOfferBlock } from "./quests.js";

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

// Nome do NPC (na cor dele) e título: "Hilda · Mercadora"
export function npcHeading(msg) {
  const head = el("div", "npc-name");
  const name = el("b", "", msg.name);
  name.style.color = msg.color;
  head.append(name);
  if (msg.title) head.append(el("span", "npc-title", ` · ${msg.title}`));
  return head;
}

// ------------------------------------------------------------------ fala do NPC

export class DialogBox {
  constructor({ getDefs, send }) {
    this.getDefs = getDefs;
    this.send = send;
    this.root = document.getElementById("dialog");
    this.root.hidden = true;
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // msg: {npc, name, title, color, text, options: [{i, text, kind?}], quest?}
  show(msg) {
    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", "Encerrar conversa");
    const header = el("header");
    header.append(npcHeading(msg), close);

    const options = el("div", "options");
    const choices = msg.options.length ? msg.options : [{ i: 0, text: "Encerrar" }];
    choices.forEach((opt, n) => {
      // opções de missão ("offer" / "turn_in") ganham destaque
      const b = button(`${n + 1}. ${opt.text}`, () => this.send({ t: "npc", op: "choose", i: opt.i }), opt.kind ?? "");
      options.append(b);
    });
    const quest = msg.quest ? this.getDefs().quests?.[msg.quest] : null;
    this.root.replaceChildren(header, el("p", "text", msg.text), ...(quest ? [questOfferBlock(quest)] : []), options);
    this.root.hidden = false;
  }

  hide() {
    this.root.hidden = true;
  }
}

// ------------------------------------------------------------------ loja

export class ShopWindow {
  constructor({ getDefs, getInventory, send }) {
    this.getDefs = getDefs;
    this.getInventory = getInventory;
    this.send = send;
    this.root = document.getElementById("shop");
    this.root.hidden = true;
    this.data = null;
    this.lastKey = "";
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // msg: {npc, name, title, color, shop, sells: [{id, price}], buys: {itemId: preço}}
  show(msg) {
    this.data = msg;
    this.lastKey = "";
    this.root.hidden = false;
    this.render();
  }

  hide() {
    this.root.hidden = true;
    this.data = null;
  }

  // O inventário mudou (comprou, vendeu, pegou algo do chão...): refaz a lista de venda
  refresh() {
    if (!this.isOpen) return;
    const inv = this.getInventory();
    const key = JSON.stringify(inv);
    if (key === this.lastKey) return;
    this.render();
  }

  render() {
    const defs = this.getDefs();
    const inv = this.getInventory();
    const msg = this.data;
    if (!msg) return;
    const focused = document.activeElement;
    if (this.root.contains(focused) && focused.tagName === "INPUT") return; // não apaga o que está digitando
    this.lastKey = JSON.stringify(inv);

    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", "Fechar loja");
    const header = el("header");
    const title = el("div", "shop-title");
    title.append(el("h3", "", msg.shop), npcHeading(msg));
    header.append(title, el("span", "shop-gold", `${inv?.gold ?? 0} moedas`), close);

    this.info = el("div", "shop-info", "Passe o mouse sobre um item.");
    this.root.replaceChildren(header, this.buySection(msg, defs, inv), this.sellSection(msg, defs, inv), this.info);
  }

  buySection(msg, defs, inv) {
    const box = el("section", "shop-box");
    box.append(el("h4", "", "Comprar"));
    if (!msg.sells.length) box.append(el("p", "hint", "Esta loja não vende nada."));
    for (const { id, price } of msg.sells) {
      const def = defs.items[id];
      if (!def) continue;
      const row = this.itemRow(def, defs);
      const qty = el("input");
      qty.type = "number";
      qty.min = "1";
      qty.max = String(def.stack > 1 ? 99 : 1);
      qty.value = "1";
      qty.disabled = def.stack <= 1;
      qty.setAttribute("aria-label", `Quantidade de ${def.name}`);
      qty.addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
      const affordable = (inv?.gold ?? 0) >= price;
      const buy = button(`Comprar · ${price}`, () => {
        const n = Math.max(1, Math.min(999, Math.floor(Number(qty.value) || 1)));
        this.send({ t: "npc", op: "buy", item: id, qty: n });
        qty.blur();
      }, "small");
      if (!affordable) buy.classList.add("poor");
      row.append(qty, buy);
      box.append(row);
    }
    return box;
  }

  sellSection(msg, defs, inv) {
    const box = el("section", "shop-box");
    box.append(el("h4", "", "Vender"));
    const rows = [];
    (inv?.slots ?? []).forEach((entry, slot) => {
      if (!entry) return;
      const [id, qty] = entry;
      const price = msg.buys[id];
      const def = defs.items[id];
      if (!price || !def) return;
      const row = this.itemRow(def, defs, qty);
      row.append(el("span", "price", `${price} cada`));
      row.append(button("Vender 1", () => this.send({ t: "npc", op: "sell", slot, qty: 1 }), "small"));
      if (qty > 1) {
        row.append(button(`Todos · ${price * qty}`, () => this.send({ t: "npc", op: "sell", slot, qty }), "small"));
      }
      rows.push(row);
    });
    if (!Object.keys(msg.buys).length) box.append(el("p", "hint", "Esta loja não compra nada."));
    else if (!rows.length) box.append(el("p", "hint", "Você não tem nada que esta loja compre."));
    box.append(...rows);
    return box;
  }

  itemRow(def, defs, qty = 0) {
    const row = el("div", "shop-row");
    const name = el("span", "item", qty > 1 ? `${def.name} ×${qty}` : def.name);
    name.style.color = RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common;
    row.append(name);
    row.addEventListener("mouseenter", () => this.showInfo(def, defs));
    return row;
  }

  showInfo(def, defs) {
    this.info.replaceChildren();
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(el("div", "name", def.name), ...describe(def, defs).map(([cls, text]) => el("div", cls, text)));
  }
}
