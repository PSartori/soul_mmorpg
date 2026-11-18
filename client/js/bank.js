// Janela do banco, aberta por um NPC (opção "bank: true" em data/npcs.yaml).
// O banco é da conta: todos os personagens dela veem as mesmas coisas guardadas.
//   {t: "npc", op: "deposit" | "withdraw", slot, qty}   move itens (o que couber)
//   {t: "npc", op: "deposit_gold" | "withdraw_gold", amount}
//   {t: "npc", op: "expand", what: "bag" | "bank"}   compra mais espaços (paga com a bolsa)
//   servidor -> {t: "bank", npc, name, title, color, size, slots: [[id, qty, estado%?] | null], gold,
//                fee (taxa ao guardar moedas), upgrades: {bag|bank: {size, price (null = no máximo), add}}}
import { RARITY_COLORS, describe, markCondition } from "./inventory.js";

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const button = (text, onClick, className = "") => {
  const b = el("button", className, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
};

export class BankWindow {
  constructor({ getDefs, getInventory, send, heading }) {
    this.getDefs = getDefs;
    this.getInventory = getInventory;
    this.send = send;
    this.heading = heading; // nome e título do NPC
    this.root = document.getElementById("bank");
    this.root.hidden = true;
    this.data = null;
    this.lastKey = "";
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

  // A bolsa mudou (pegou algo, usou uma poção...): redesenha
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

    const close = button("✕", () => this.send({ t: "npc", op: "close" }), "close");
    close.setAttribute("aria-label", "Fechar banco");
    const title = el("div", "shop-title");
    title.append(el("h3", "", "Banco"), this.heading(msg));
    const header = el("header");
    header.append(title, close);

    const used = msg.slots.filter(Boolean).length;
    this.info = el("div", "shop-info", "Passe o mouse sobre um item.");
    this.root.replaceChildren(
      header,
      this.goldBox(msg, inv),
      el("h4", "bank-label", `Baú (${used}/${msg.size})`),
      this.grid(msg.slots, defs, "withdraw"),
      el("h4", "bank-label", "Sua bolsa"),
      this.grid(inv?.slots ?? [], defs, "deposit"),
      el("p", "hint", "Clique: move a pilha inteira · Shift + clique: só 1. Equipados ficam com você."),
      this.upgradesBox(msg, inv),
      this.info,
    );
  }

  // Comprar mais espaços na bolsa e no baú
  upgradesBox(msg, inv) {
    const box = el("section", "shop-box bank-upgrades");
    box.append(el("h4", "", "Mais espaço"));
    const gold = inv?.gold ?? 0;
    for (const [what, label] of [["bag", "Bolsa"], ["bank", "Baú (para a conta toda)"]]) {
      const up = msg.upgrades?.[what];
      if (!up) continue;
      const row = el("div", "shop-row");
      row.append(el("span", "item", `${label}: ${up.size} espaços`));
      if (up.price === null) {
        row.append(el("span", "hint", "no máximo"));
      } else {
        const buy = button(`+${up.add} · ${up.price}`, () => {
          if (window.confirm(`Comprar mais ${up.add} espaços (${label.toLowerCase()}) por ${up.price} moedas?`)) {
            this.send({ t: "npc", op: "expand", what });
          }
        }, "small");
        if (gold < up.price) buy.classList.add("poor");
        row.append(buy);
      }
      box.append(row);
    }
    box.append(el("p", "hint", "Cada ampliação custa mais que a anterior. Paga com as moedas da bolsa."));
    return box;
  }

  goldBox(msg, inv) {
    const box = el("div", "bank-gold");
    const amount = el("input");
    amount.type = "number";
    amount.min = "1";
    amount.placeholder = "tudo";
    amount.setAttribute("aria-label", "Quantia de moedas");
    amount.addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
    // vazio = tudo o que há do lado de onde sai
    const move = (op, available) => {
      const typed = Math.floor(Number(amount.value));
      const value = typed > 0 ? typed : available;
      amount.value = "";
      amount.blur();
      if (value > 0) this.send({ t: "npc", op, amount: value });
    };
    const text = el("div", "bank-gold-text");
    const fee = msg.fee ? ` · guardar custa ${Math.round(msg.fee * 100)}%` : "";
    text.append(el("span", "", `Guardado: ${msg.gold} moedas`),
      el("span", "hint", `Na bolsa: ${inv?.gold ?? 0}${fee}`));
    box.append(text, amount,
      button("Guardar", () => move("deposit_gold", inv?.gold ?? 0), "small"),
      button("Sacar", () => move("withdraw_gold", msg.gold), "small"));
    return box;
  }

  // op: "deposit" (bolsa -> banco) ou "withdraw" (banco -> bolsa)
  grid(slots, defs, op) {
    const grid = el("div", "bank-grid");
    slots.forEach((entry, slot) => {
      const node = el("div", "islot");
      const [itemId, qty, cond] = entry ?? [null, 0];
      const def = itemId ? defs.items[itemId] : null;
      if (def) {
        node.classList.add("filled");
        node.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
        node.append(el("span", "", def.name));
        if (qty > 1) node.append(el("span", "qty", String(qty)));
        markCondition(node, cond);
        node.title = op === "deposit" ? "Guardar no banco" : "Pegar do banco";
        node.addEventListener("mouseenter", () => this.showInfo(def, defs, cond));
        node.addEventListener("click", (ev) => this.send({ t: "npc", op, slot, qty: ev.shiftKey ? 1 : qty }));
      }
      grid.append(node);
    });
    return grid;
  }

  showInfo(def, defs, cond) {
    this.info.replaceChildren();
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(el("div", "name", def.name),
      ...describe(def, defs, cond).map(([cls, text]) => el("div", cls, text)));
  }
}
