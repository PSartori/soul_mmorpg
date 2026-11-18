// Troca entre jogadores: aviso de pedido (Aceitar/Recusar) e janela da troca.
// Quem decide tudo é o servidor (server/trade.py); aqui só se mostra e se pede.
//   {t: "trade", op: "invite", target} · "accept" · "decline"
//   {t: "trade", op: "set", item, qty}   põe/ajusta um item da bolsa (0 tira)
//   {t: "trade", op: "gold", amount}
//   {t: "trade", op: "confirm", v}       aceita a troca na versão v (a que está na tela)
//   {t: "trade", op: "cancel"}
// O estado chega no snapshot: you.trade = {with, mine, theirs, v, ok_me, ok_them} (null = acabou)
// e you.tinv = {from, left} (pedido esperando resposta).
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
  b.addEventListener("click", onClick);
  return b;
};

const numberInput = (value, max, label) => {
  const input = el("input");
  input.type = "number";
  input.min = "0";
  if (max !== undefined) input.max = String(max);
  input.value = String(value);
  input.setAttribute("aria-label", label);
  input.addEventListener("keydown", (ev) => {
    ev.stopPropagation(); // não move o personagem
    if (ev.key === "Enter") input.blur(); // Enter confirma (dispara o "change")
  });
  return input;
};

// ------------------------------------------------------------------ aviso de pedido

export class TradeInvitePanel {
  constructor({ root, send }) {
    this.root = root;
    this.send = send;
    this.text = el("p", "text");
    const actions = el("div", "actions");
    actions.append(
      button("Aceitar", () => this.send({ t: "trade", op: "accept" }), "accept"),
      button("Recusar", () => this.send({ t: "trade", op: "decline" })),
    );
    this.root.replaceChildren(this.text, actions);
    this.root.hidden = true;
  }

  update(invite) {
    this.root.hidden = !invite;
    if (invite) this.text.textContent = `${invite.from} quer trocar com você. (${invite.left}s)`;
  }
}

// Opção no menu ao clicar num jogador
export function tradeMenuItems(target, send, trading) {
  return trading ? [] : [["Propor troca", () => send({ t: "trade", op: "invite", target: target.n })]];
}

// ------------------------------------------------------------------ janela da troca

export class TradeWindow {
  constructor({ getDefs, getInventory, send }) {
    this.getDefs = getDefs;
    this.getInventory = getInventory;
    this.send = send;
    this.root = document.getElementById("trade");
    this.root.hidden = true;
    this.data = null;
    this.lastKey = "";
    this.stale = false; // chegou algo novo enquanto o jogador digitava
    // terminou de digitar uma quantidade: redesenha com o que chegou enquanto isso
    this.root.addEventListener("focusout", () => {
      if (this.stale) setTimeout(() => this.render(), 0);
    });
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // data: you.trade (null = a troca acabou)
  update(data) {
    this.data = data;
    this.root.hidden = !data;
    this.lastKey = "";
    this.render();
  }

  refresh() {
    if (this.isOpen && JSON.stringify(this.getInventory()) !== this.lastKey) this.render();
  }

  render() {
    const data = this.data;
    if (!data) return;
    const focused = document.activeElement;
    if (this.root.contains(focused) && focused.tagName === "INPUT") { // não apaga o que está digitando
      this.stale = true;
      return;
    }
    this.stale = false;
    const defs = this.getDefs();
    const inv = this.getInventory();
    this.lastKey = JSON.stringify(inv);

    const header = el("header");
    header.append(el("h3", "", `Troca com ${data.with}`), button("✕", () => {
      if (window.confirm("Cancelar a troca?")) this.send({ t: "trade", op: "cancel" });
    }, "close"));

    const sides = el("div", "trade-sides");
    sides.append(this.mySide(data, defs, inv), this.theirSide(data, defs));

    this.info = el("div", "shop-info", "Passe o mouse sobre um item.");
    this.root.replaceChildren(header, sides, el("h4", "bank-label", "Sua bolsa (clique para oferecer)"),
      this.bag(data, defs, inv), this.footer(data), this.info);
  }

  sideBox(title, accepted) {
    const box = el("section", `trade-side${accepted ? " ok" : ""}`);
    const head = el("h4");
    head.append(document.createTextNode(title));
    if (accepted) head.append(el("span", "trade-ok", " ✓ aceitou"));
    box.append(head);
    return box;
  }

  itemName(def, qty) {
    const name = el("span", "item", qty > 1 ? `${def.name} ×${qty}` : def.name);
    name.style.color = RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common;
    return name;
  }

  mySide(data, defs, inv) {
    const box = this.sideBox("Você oferece", data.ok_me);
    const items = Object.entries(data.mine.items);
    if (!items.length) box.append(el("p", "hint", "Nada ainda."));
    for (const [itemId, qty] of items) {
      const def = defs.items[itemId];
      if (!def) continue;
      const row = el("div", "shop-row");
      row.addEventListener("mouseenter", () => this.showInfo(def, defs));
      row.append(this.itemName(def, 1));
      const have = countIn(inv, itemId);
      if (have > 1) {
        const input = numberInput(qty, have, `Quantidade de ${def.name}`);
        input.addEventListener("change", () => {
          const n = Math.max(0, Math.min(have, Math.floor(Number(input.value) || 0)));
          this.send({ t: "trade", op: "set", item: itemId, qty: n });
        });
        row.append(input);
      }
      row.append(button("×", () => this.send({ t: "trade", op: "set", item: itemId, qty: 0 }), "small"));
      box.append(row);
    }
    const goldRow = el("div", "shop-row");
    const gold = numberInput(data.mine.gold, inv?.gold ?? 0, "Moedas oferecidas");
    gold.addEventListener("change", () => {
      const n = Math.max(0, Math.floor(Number(gold.value) || 0));
      this.send({ t: "trade", op: "gold", amount: n });
    });
    goldRow.append(el("span", "item gold", "Moedas"), gold, el("span", "hint", `de ${inv?.gold ?? 0}`));
    box.append(goldRow);
    return box;
  }

  theirSide(data, defs) {
    const box = this.sideBox(`${data.with} oferece`, data.ok_them);
    const items = Object.entries(data.theirs.items);
    if (!items.length && !data.theirs.gold) box.append(el("p", "hint", "Nada ainda."));
    for (const [itemId, qty] of items) {
      const def = defs.items[itemId];
      if (!def) continue;
      const row = el("div", "shop-row");
      row.addEventListener("mouseenter", () => this.showInfo(def, defs));
      row.append(this.itemName(def, qty));
      box.append(row);
    }
    if (data.theirs.gold) box.append(el("div", "shop-row trade-gold", `${data.theirs.gold} moedas`));
    return box;
  }

  bag(data, defs, inv) {
    const grid = el("div", "bank-grid");
    const offered = data.mine.items;
    (inv?.slots ?? []).forEach((entry) => {
      const node = el("div", "islot");
      const [itemId] = entry ?? [null];
      const def = itemId ? defs.items[itemId] : null;
      if (def) {
        const have = countIn(inv, itemId);
        node.classList.add("filled");
        if (itemId in offered) node.classList.add("offered");
        node.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
        node.append(el("span", "", def.name));
        if (entry[1] > 1) node.append(el("span", "qty", String(entry[1])));
        node.title = itemId in offered ? "Já está na troca" : "Oferecer na troca (tudo o que você tem)";
        node.addEventListener("mouseenter", () => this.showInfo(def, defs));
        node.addEventListener("click", () => {
          if (!(itemId in offered)) this.send({ t: "trade", op: "set", item: itemId, qty: have });
        });
      }
      grid.append(node);
    });
    return grid;
  }

  footer(data) {
    const box = el("div", "trade-footer");
    const status = data.ok_me
      ? `Você aceitou. Esperando ${data.with}…`
      : data.ok_them ? `${data.with} aceitou. Confira e aceite também.` : "Os dois precisam aceitar.";
    const accept = button(data.ok_me ? "Aceito ✓" : "Aceitar troca",
      () => this.send({ t: "trade", op: "confirm", v: data.v }), "accept");
    accept.disabled = data.ok_me;
    box.append(el("p", "trade-status", status), accept);
    const wrap = el("div");
    wrap.append(box, el("p", "hint", "Qualquer mudança nas ofertas desfaz os aceites dos dois."));
    return wrap;
  }

  showInfo(def, defs) {
    this.info.replaceChildren();
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(el("div", "name", def.name), ...describe(def, defs).map(([cls, text]) => el("div", cls, text)));
  }
}

function countIn(inv, itemId) {
  return (inv?.slots ?? []).reduce((sum, entry) => sum + (entry && entry[0] === itemId ? entry[1] : 0), 0);
}
