// Janela de inventário: espaços, equipamento, moedas e detalhes do item.
// Só mostra e envia pedidos; quem decide se pode usar/equipar é o servidor.

const SLOT_NAMES = { weapon: "Arma", armor: "Armadura", accessory: "Acessório" };
const TYPE_NAMES = { equipment: "Equipamento", consumable: "Consumível", material: "Material" };
const RARITY_NAMES = { common: "Comum", uncommon: "Incomum", rare: "Raro", epic: "Épico" };
const BONUS_NAMES = { max_hp: "Vida máx.", max_energy: "Energia máx.", speed: "Velocidade" };
const MOD_NAMES = {
  damage: "Dano", damage_taken: "Dano recebido", energy_regen: "Regeneração de energia", speed: "Velocidade",
};

export const RARITY_COLORS = {
  common: "#d8dbe2", uncommon: "#6fd36f", rare: "#5aa8ff", epic: "#c77dff", gold: "#ffd65a",
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function signed(n) {
  return n > 0 ? `+${n}` : `${n}`;
}

// Estado de um equipamento desgastado (100 = novo, 0 = quebrado), num espaço: marca no canto
export function markCondition(node, cond) {
  if (cond === undefined || cond >= 100) return;
  node.classList.add(cond === 0 ? "broken" : "worn");
  node.append(el("span", "cond", cond === 0 ? "quebrado" : `${cond}%`));
}

// Linhas de descrição de um item (bônus, uso, restrições...). cond: estado (%) deste exemplar
export function describe(def, defs, cond) {
  const lines = [];
  const kind = [RARITY_NAMES[def.rarity], TYPE_NAMES[def.type], SLOT_NAMES[def.slot]].filter(Boolean).join(" · ");
  lines.push(["meta", kind]);
  if (def.desc) lines.push(["", def.desc]);
  for (const [key, value] of Object.entries(def.bonus ?? {})) lines.push(["", `${signed(value)} ${BONUS_NAMES[key] ?? key}`]);
  for (const [key, value] of Object.entries(def.mods ?? {})) {
    lines.push(["", `${signed(Math.round((value - 1) * 100))}% ${MOD_NAMES[key] ?? key}`]);
  }
  if (def.heal) lines.push(["", `Recupera ${def.heal} de vida`]);
  if (def.energy) lines.push(["", `Recupera ${def.energy} de energia`]);
  for (const effectId of def.effects ?? []) lines.push(["", `Efeito: ${defs.effects[effectId]?.name ?? effectId}`]);
  const only = [
    ...(def.races ?? []).map((r) => defs.races[r]?.name ?? r),
    ...(def.classes ?? []).map((c) => defs.classes[c]?.name ?? c),
  ];
  if (only.length) lines.push(["meta", `Só para: ${only.join(", ")}`]);
  if (def.dur) {
    const state = cond ?? 100;
    lines.push([state === 0 ? "bad" : "meta", state === 0
      ? "Quebrado: não dá bônus. Conserte com um ferreiro."
      : `Durabilidade: ${state}%${state < 100 ? " (conserte com um ferreiro)" : ""}`]);
  }
  if (def.value) lines.push(["meta", `Valor: ${def.value} moedas`]);
  return lines;
}

export class InventoryUI {
  constructor({ getDefs, send }) {
    this.getDefs = getDefs;
    this.send = send;
    this.root = document.getElementById("inventory");
    this.grid = document.getElementById("inv-grid");
    this.equipment = document.getElementById("inv-equipment");
    this.info = document.getElementById("inv-info");
    this.goldEl = document.getElementById("inv-gold");
    this.inv = null;
    this.cooling = new Set();
    this.lastKey = "";
    document.getElementById("inv-close").addEventListener("click", () => this.toggle(false));
  }

  get isOpen() {
    return !this.root.hidden;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    if (this.isOpen) this.render();
  }

  // inv: { size, slots: [[id, qty, estado%?] | null], equipment: {slot: id}, cond: {slot: %}, gold }
  // cooldowns: { itemId: s }
  update(inv, cooldowns) {
    if (inv) this.inv = inv;
    this.cooling = new Set(Object.keys(cooldowns ?? {}));
    const key = JSON.stringify([this.inv, [...this.cooling]]);
    if (key === this.lastKey) return;
    this.lastKey = key;
    if (this.isOpen) this.render();
  }

  render() {
    if (!this.inv) return;
    const defs = this.getDefs();
    this.goldEl.textContent = `${this.inv.gold} moedas`;

    this.equipment.replaceChildren(
      ...Object.keys(SLOT_NAMES).map((slot) => {
        const itemId = this.inv.equipment[slot];
        const node = this.slotNode(itemId, 1, defs, SLOT_NAMES[slot], this.inv.cond?.[slot]);
        node.classList.add("equip");
        if (itemId) node.addEventListener("click", () => this.send({ t: "item", op: "unequip", slot }));
        return node;
      }),
    );

    this.grid.replaceChildren(
      ...this.inv.slots.map((entry, index) => {
        const [itemId, qty, cond] = entry ?? [null, 0];
        const node = this.slotNode(itemId, qty, defs, undefined, cond);
        if (itemId) {
          node.addEventListener("click", (ev) => {
            this.send({ t: "item", op: ev.shiftKey ? "drop" : "use", slot: index });
          });
        }
        return node;
      }),
    );
  }

  slotNode(itemId, qty, defs, label, cond) {
    const node = el("div", "islot");
    if (label) node.append(el("span", "label", label));
    const def = itemId ? defs.items[itemId] : null;
    if (!def) return node;
    node.classList.add("filled");
    if (this.cooling.has(itemId)) node.classList.add("cooling");
    node.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    node.append(el("span", "", def.name));
    if (qty > 1) node.append(el("span", "qty", String(qty)));
    markCondition(node, cond);
    node.addEventListener("mouseenter", () => this.showInfo(def, defs, cond));
    return node;
  }

  showInfo(def, defs, cond) {
    this.info.replaceChildren();
    const name = el("div", "name", def.name);
    this.info.style.setProperty("--rarity", RARITY_COLORS[def.rarity] ?? RARITY_COLORS.common);
    this.info.append(name, ...describe(def, defs, cond).map(([cls, text]) => el("div", cls, text)));
  }
}
