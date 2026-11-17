// Missões: diário (tecla J), acompanhamento no canto da tela e o bloco de objetivos e
// recompensas que aparece quando um NPC oferece uma missão. O progresso vem do servidor
// (you.q no snapshot, só quando muda); as definições, de defs.quests.

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// Linhas dos objetivos: "Derrotar Espectro 2/3", com ✓ nos cumpridos
export function objectiveList(def, progress = null) {
  const list = el("ul", "objectives");
  def.objectives.forEach((obj, i) => {
    const value = progress ? progress[i] ?? 0 : null;
    const done = progress && value >= obj.count;
    const count = obj.count > 1 ? ` ${progress ? value : 0}/${obj.count}` : "";
    const li = el("li", done ? "done" : "", `${done ? "✓ " : ""}${obj.label}${count}`);
    list.append(li);
  });
  return list;
}

// Bloco da oferta (dentro da conversa com o NPC)
export function questOfferBlock(def) {
  const box = el("div", "quest-offer");
  box.append(el("div", "quest-name", def.name), el("div", "label", "Objetivos"), objectiveList(def));
  if (def.rewards.length) box.append(el("div", "label", "Recompensa"), el("div", "rewards", def.rewards.join(" · ")));
  if (def.turn_in && def.turn_in !== def.giver) box.append(el("div", "hint", `Entregar a ${def.turn_in}.`));
  if (def.repeatable) box.append(el("div", "hint", "Pode ser repetida."));
  return box;
}

export class QuestLog {
  constructor({ getDefs, send }) {
    this.getDefs = getDefs;
    this.send = send;
    this.root = document.getElementById("quests");
    this.body = document.getElementById("quests-body");
    this.tracker = document.getElementById("tracker");
    this.data = { active: [], done: [], marks: {} };
    document.getElementById("quests-close").addEventListener("click", () => this.toggle(false));
  }

  get isOpen() {
    return !this.root.hidden;
  }

  get marks() {
    return this.data.marks ?? {};
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    if (this.isOpen) this.render();
  }

  update(data) {
    if (!data) return;
    this.data = data;
    this.renderTracker();
    if (this.isOpen) this.render();
  }

  // Definições novas (recarga de conteúdo)
  refresh() {
    this.renderTracker();
    if (this.isOpen) this.render();
  }

  render() {
    const defs = this.getDefs();
    const nodes = [];
    if (!this.data.active.length) {
      nodes.push(el("p", "hint", "Nenhuma missão em andamento. NPCs com ! acima da cabeça têm missões para você."));
    }
    for (const { id, p, ready } of this.data.active) {
      const def = defs.quests?.[id];
      if (!def) continue;
      const card = el("section", "quest-card");
      const head = el("div", "head");
      head.append(el("b", "", def.name));
      if (ready) head.append(el("span", "ready", def.turn_in ? "pronta" : "concluindo…"));
      card.append(head, el("p", "desc", def.description), objectiveList(def, p));
      if (def.rewards.length) card.append(el("p", "rewards", `Recompensa: ${def.rewards.join(" · ")}`));
      if (def.turn_in) card.append(el("p", "hint", `${ready ? "Entregue" : "Entregar"} a ${def.turn_in}.`));
      const abandon = el("button", "small danger", "Abandonar");
      abandon.type = "button";
      abandon.addEventListener("click", () => {
        if (window.confirm(`Abandonar "${def.name}"? O progresso é perdido.`)) this.send({ t: "quest", op: "abandon", id });
        abandon.blur();
      });
      card.append(abandon);
      nodes.push(card);
    }
    const done = this.data.done.map((id) => defs.quests?.[id]?.name).filter(Boolean);
    if (done.length) {
      const box = el("section", "quest-card done");
      box.append(el("b", "", `Concluídas (${done.length})`), el("p", "hint", done.join(", ")));
      nodes.push(box);
    }
    this.body.replaceChildren(...nodes);
  }

  // Canto da tela: objetivos das missões em andamento
  renderTracker() {
    const defs = this.getDefs();
    const cards = this.data.active
      .map(({ id, p, ready }) => {
        const def = defs.quests?.[id];
        if (!def) return null;
        const card = el("div", "track");
        const name = el("div", "name", def.name);
        if (ready) name.append(el("span", "ready", def.turn_in ? ` · entregar a ${def.turn_in}` : ""));
        card.append(name, objectiveList(def, p));
        return card;
      })
      .filter(Boolean);
    this.tracker.replaceChildren(...cards);
    this.tracker.hidden = !cards.length;
  }
}
