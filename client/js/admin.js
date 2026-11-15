// Janela de administração (só aparece para quem tem papel na equipe).
// Mostra só as ações que o papel permite; o servidor confere tudo de novo.

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const MUTE_OPTIONS = [["5 min", 300], ["30 min", 1800], ["1 h", 3600], ["1 dia", 86400]];
const BAN_OPTIONS = [["1 dia", 86400], ["7 dias", 604800], ["Para sempre", null]];

export class AdminUI {
  constructor({ getDefs, send }) {
    this.getDefs = getDefs;
    this.send = send;
    this.root = document.getElementById("admin");
    this.body = document.getElementById("admin-body");
    this.result = document.getElementById("admin-result");
    this.data = null;
    this.selected = null;
    this.timer = null;
    document.getElementById("admin-close").addEventListener("click", () => this.toggle(false));
  }

  get isOpen() {
    return !this.root.hidden;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    clearInterval(this.timer);
    if (this.isOpen) {
      this.send({ t: "admin", op: "refresh" });
      // aberta, a janela se atualiza sozinha (quem entrou, saiu ou foi expulso)
      this.timer = setInterval(() => this.send({ t: "admin", op: "refresh" }), 5000);
    }
  }

  op(op, extra = {}) {
    this.send({ t: "admin", op, ...extra });
  }

  showResult(msg) {
    this.result.textContent = msg.text;
    this.result.className = msg.ok ? "ok" : "error";
  }

  update(data) {
    this.data = data;
    if (this.selected && !data.players.some((p) => p.name === this.selected)) this.selected = null;
    // não redesenha no meio de uma digitação (motivo, anúncio, quantidade)
    if (this.root.contains(document.activeElement) && ["INPUT", "SELECT"].includes(document.activeElement.tagName)) return;
    this.render();
  }

  can(permission) {
    const role = this.getDefs().roles?.[this.data?.role];
    return !!role && role.permissions.includes(permission);
  }

  render() {
    if (!this.data) return;
    const defs = this.getDefs();
    const role = defs.roles[this.data.role];
    document.getElementById("admin-title").textContent = `Administração · ${role?.name ?? ""}`;
    const sections = [this.playersSection(defs), this.actionsSection(defs)];
    if (this.can("reload")) sections.push(this.reloadSection());
    if (this.can("announce")) sections.push(this.announceSection());
    if (this.data.bans) sections.push(this.bansSection());
    if (this.data.log) sections.push(this.logSection());
    this.body.replaceChildren(...sections);
  }

  playersSection(defs) {
    const box = el("section", "adm-box");
    box.append(el("h4", "", `Online (${this.data.players.length})`));
    const list = el("div", "adm-players");
    for (const p of this.data.players) {
      const row = el("button", "adm-player" + (p.name === this.selected ? " selected" : ""));
      row.type = "button";
      const tag = p.role ? defs.roles[p.role] : null;
      const name = el("span", "name", (tag ? `[${tag.tag}] ` : "") + p.name + (p.muted ? " 🔇" : ""));
      if (tag) name.style.color = tag.color;
      row.append(name, el("span", "meta", [p.map, [p.race, p.class].filter(Boolean).join(" "), p.org].filter(Boolean).join(" · ")));
      row.addEventListener("click", () => {
        this.selected = p.name;
        this.render();
      });
      list.append(row);
    }
    box.append(list);
    return box;
  }

  actionsSection(defs) {
    const box = el("section", "adm-box");
    if (!this.selected) {
      box.append(el("p", "hint", "Escolha um jogador na lista para agir."));
      return box;
    }
    const target = this.selected;
    box.append(el("h4", "", `Ações sobre ${target}`));
    const reason = el("input");
    reason.placeholder = "Motivo (opcional, aparece para a pessoa)";
    reason.maxLength = 200;
    box.append(reason);
    const row = (label, buttons) => {
      const line = el("div", "adm-row");
      line.append(el("span", "label", label), ...buttons);
      box.append(line);
    };
    const button = (text, onClick, danger = false) => {
      const b = el("button", danger ? "danger" : "", text);
      b.type = "button";
      b.addEventListener("click", onClick);
      return b;
    };
    const confirmThen = (question, fn) => () => {
      if (window.confirm(question)) fn();
    };

    if (this.can("kick")) {
      row("Expulsar", [button("Expulsar agora", confirmThen(`Expulsar ${target}?`,
        () => this.op("kick", { target, reason: reason.value })), true)]);
    }
    if (this.can("mute")) {
      row("Silenciar", [
        ...MUTE_OPTIONS.map(([text, seconds]) => button(text, () => this.op("mute", { target, seconds, reason: reason.value }))),
        button("Tirar", () => this.op("unmute", { target })),
      ]);
    }
    if (this.can("ban")) {
      row("Banir", BAN_OPTIONS.map(([text, seconds]) => button(text, confirmThen(
        `Banir ${target} (${text.toLowerCase()})? A pessoa será desconectada.`,
        () => this.op("ban", { target, seconds, reason: reason.value }),
      ), true)));
    }
    if (this.can("teleport")) {
      row("Teleporte", [button("Ir até", () => this.op("goto", { target })), button("Trazer", () => this.op("bring", { target }))]);
    }
    if (this.can("give")) {
      const item = el("select");
      for (const id of this.data.items) {
        const opt = el("option", "", defs.items[id]?.name ?? id);
        opt.value = id;
        item.append(opt);
      }
      const qty = el("input");
      qty.type = "number";
      qty.min = "1";
      qty.value = "1";
      qty.className = "small";
      row("Dar item", [item, qty, button("Dar", () => this.op("give_item", { target, item: item.value, qty: Number(qty.value) }))]);
      const gold = el("input");
      gold.type = "number";
      gold.min = "1";
      gold.value = "10";
      gold.className = "small";
      row("Dar moedas", [gold, button("Dar", () => this.op("give_gold", { target, amount: Number(gold.value) }))]);
    }
    if (this.can("set_role")) {
      const select = el("select");
      for (const id of ["", ...this.data.roles]) {
        const opt = el("option", "", id ? defs.roles[id].name : "Sem papel");
        opt.value = id;
        select.append(opt);
      }
      row("Papel", [select, button("Definir", () => this.op("set_role", { target, role: select.value || null }))]);
    }
    return box;
  }

  reloadSection() {
    const box = el("section", "adm-box");
    box.append(el("h4", "", "Conteúdo"));
    box.append(el("p", "hint", "Lê de novo os YAML e mapas de data/ sem reiniciar. Se houver erro, nada muda."));
    const b = el("button", "", "Recarregar conteúdo");
    b.type = "button";
    b.addEventListener("click", () => {
      this.result.textContent = "Recarregando…";
      this.result.className = "";
      this.op("reload");
    });
    box.append(b);
    return box;
  }

  announceSection() {
    const box = el("section", "adm-box");
    box.append(el("h4", "", "Anúncio para todos"));
    const input = el("input");
    input.maxLength = 200;
    input.placeholder = "Ex.: invasão começa em 5 minutos!";
    const send = el("button", "", "Anunciar");
    send.type = "button";
    send.addEventListener("click", () => {
      if (input.value.trim()) this.op("announce", { text: input.value.trim() });
      input.value = "";
    });
    const line = el("div", "adm-row");
    line.append(input, send);
    box.append(line);
    return box;
  }

  bansSection() {
    const box = el("section", "adm-box");
    box.append(el("h4", "", `Banidos (${this.data.bans.length})`));
    if (!this.data.bans.length) box.append(el("p", "hint", "Ninguém banido."));
    for (const ban of this.data.bans) {
      const line = el("div", "adm-row");
      const b = el("button", "", "Desbanir");
      b.type = "button";
      b.addEventListener("click", () => this.op("unban", { target: ban.name }));
      line.append(el("span", "label wide", `${ban.name} — ${ban.until}, por ${ban.by}${ban.reason ? ` (${ban.reason})` : ""}`), b);
      box.append(line);
    }
    return box;
  }

  logSection() {
    const box = el("section", "adm-box");
    box.append(el("h4", "", "Registro da equipe"));
    const list = el("div", "adm-log");
    for (const entry of this.data.log) {
      const what = [entry.actor, entry.action, entry.target].filter(Boolean).join(" ");
      list.append(el("div", "", `${entry.at} — ${what}${entry.details ? ` (${entry.details})` : ""}`));
    }
    if (!this.data.log.length) list.append(el("div", "hint", "Nada ainda."));
    box.append(list);
    return box;
  }
}
