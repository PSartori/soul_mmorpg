// Interface social: menu ao clicar num jogador, aviso de desafio de duelo e janela
// da organização. Substitui os comandos de chat (que continuam como atalho).
// O cliente só mostra opções prováveis; quem decide se pode é o servidor.

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

const PROMOTION_LABELS = { default: "inicial", appointment: "por nomeação", challenge: "por disputa" };

function rankTitle(org, rankId, seat) {
  const rank = org?.ranks?.[rankId];
  if (!rank) return rankId;
  return rank.numbered && seat ? `${rank.name} ${seat}` : rank.name;
}

// ------------------------------------------------------------------ menu do jogador

export class PlayerMenu {
  constructor({ container, getDefs, getMe, getYou, send, openAdmin }) {
    this.container = container;
    this.getDefs = getDefs;
    this.getMe = getMe;
    this.getYou = getYou;
    this.send = send;
    this.openAdmin = openAdmin;
    this.root = el("div", "player-menu");
    this.root.hidden = true;
    container.append(this.root);
    window.addEventListener("pointerdown", (ev) => {
      if (!this.root.hidden && !this.root.contains(ev.target)) this.close();
    });
  }

  close() {
    this.root.hidden = true;
  }

  // x, y: posição do clique dentro do container (px de tela)
  open(target, x, y) {
    const items = this.itemsFor(target);
    if (!items.length) return;
    this.root.replaceChildren(el("div", "title", target.n));
    for (const [label, action, danger] of items) {
      this.root.append(button(label, () => {
        this.close();
        action();
      }, danger ? "danger" : ""));
    }
    this.root.hidden = false;
    const box = this.container.getBoundingClientRect();
    const menu = this.root.getBoundingClientRect();
    this.root.style.left = `${Math.min(x, box.width - menu.width - 4)}px`;
    this.root.style.top = `${Math.min(y, box.height - menu.height - 4)}px`;
  }

  itemsFor(target) {
    const defs = this.getDefs();
    const me = this.getMe();
    const you = this.getYou();
    const items = [];
    const name = target.n;
    if (!me || !you) return items;

    if (!you.duel && !target.du) {
      items.push(["Desafiar para duelo", () => this.send({ t: "duel", op: "challenge", target: name })]);
    }

    const [myOrg, myRankId] = me.o ?? [];
    const [theirOrg, theirRankId, theirSeat] = target.o ?? [];
    if (myOrg && myOrg === theirOrg) {
      const org = defs.orgs[myOrg];
      const order = Object.keys(org.ranks);
      const mine = order.indexOf(myRankId);
      const theirs = order.indexOf(theirRankId);
      const theirRank = org.ranks[theirRankId];
      if (theirs < mine && theirRank?.promotion === "challenge") {
        items.push([`Disputar o cargo de ${rankTitle(org, theirRankId, theirSeat)}`,
          () => this.send({ t: "org", op: "dispute", target: name })]);
      }
      if (org.ranks[myRankId]?.leader && theirs > mine) {
        for (const rankId of order.slice(mine + 1)) {
          if (org.ranks[rankId].promotion === "appointment" && rankId !== theirRankId) {
            items.push([`Promover a ${org.ranks[rankId].name}`,
              () => this.send({ t: "org", op: "promote", target: name, rank: rankId })]);
          }
        }
        if (theirRankId !== org.default_rank) {
          items.push(["Rebaixar ao cargo inicial", () => this.send({ t: "org", op: "demote", target: name })]);
        }
        items.push(["Expulsar da organização", () => {
          if (window.confirm(`Expulsar ${name} de ${org.name}?`)) this.send({ t: "org", op: "expel", target: name });
        }, true]);
      }
    }

    if (you.staff) items.push(["Abrir na administração", () => this.openAdmin(name)]);
    return items;
  }
}

// ------------------------------------------------------------------ aviso de desafio

export class InvitePanel {
  constructor({ root, send }) {
    this.root = root;
    this.send = send;
    this.text = el("p", "text");
    const actions = el("div", "actions");
    actions.append(
      button("Aceitar", () => this.send({ t: "duel", op: "accept" }), "accept"),
      button("Recusar", () => this.send({ t: "duel", op: "decline" })),
    );
    this.root.replaceChildren(this.text, actions);
    this.root.hidden = true;
  }

  update(invite) {
    this.root.hidden = !invite;
    if (!invite) return;
    const what = invite.prize ? `pelo seu cargo de ${invite.prize}` : "para um duelo";
    this.text.textContent = `${invite.from} desafiou você ${what}. (${invite.left}s)`;
  }
}

// ------------------------------------------------------------------ janela da organização

export class OrgWindow {
  constructor({ getDefs, send }) {
    this.getDefs = getDefs;
    this.send = send;
    this.root = document.getElementById("org");
    this.body = document.getElementById("org-body");
    this.title = document.getElementById("org-title");
    this.data = null;
    this.timer = null;
    document.getElementById("org-close").addEventListener("click", () => this.toggle(false));
  }

  get isOpen() {
    return !this.root.hidden;
  }

  get isMember() {
    return !!this.data?.mine;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    clearInterval(this.timer);
    if (this.isOpen) {
      this.op("info");
      this.timer = setInterval(() => this.op("info"), 5000); // quem entrou, saiu, subiu...
    }
  }

  op(op, extra = {}) {
    this.send({ t: "org", op, ...extra });
  }

  update(data) {
    this.data = data;
    if (this.root.contains(document.activeElement) && document.activeElement.tagName === "SELECT") return;
    this.render();
  }

  render() {
    if (!this.data) return;
    const defs = this.getDefs();
    this.body.replaceChildren(...(this.data.mine ? this.memberView(defs) : this.joinView(defs)));
  }

  joinView(defs) {
    this.title.textContent = "Organizações";
    const intro = el("p", "hint", `Você não faz parte de nenhuma. Seu nível total: ${this.data.total_level}.`);
    const cards = this.data.orgs.map((info) => {
      const org = defs.orgs[info.id];
      const card = el("section", "org-card");
      const name = el("h4", "", org.name);
      name.style.color = org.color;
      const races = org.races.length ? org.races.map((r) => defs.races[r]?.name ?? r).join(", ") : "qualquer raça";
      card.append(name, el("p", "", org.description), el("p", "hint", `${races} · nível total mínimo ${org.min}`));
      card.append(info.can_join
        ? button("Entrar", () => this.op("join", { org: info.id }))
        : el("p", "reason", `Não dá para entrar: ${info.reason}.`));
      return card;
    });
    return [intro, ...cards];
  }

  memberView(defs) {
    const mine = this.data.mine;
    const org = defs.orgs[mine.org];
    this.title.textContent = `${org.name} · ${mine.title}`;
    this.title.style.color = org.color;

    const ranksBox = el("section", "org-card");
    ranksBox.append(el("h4", "", "Cargos"));
    for (const info of mine.ranks) {
      const rank = org.ranks[info.id];
      const row = el("div", "org-rank");
      const seats = rank.seats ? ` [${info.holders.length}/${rank.seats}]` : "";
      const head = el("div", "head");
      head.append(el("b", "", rank.name + seats),
        el("span", "hint", ` ${PROMOTION_LABELS[rank.promotion]}${rank.min ? ` · nível ${rank.min}` : ""}`));
      if (info.can_claim) head.append(button("Assumir vaga", () => this.op("claim", { rank: info.id }), "small"));
      const holders = info.holders.map((h) => (rank.numbered && h.seat ? `${h.seat}. ` : "") + h.name + (h.online ? " •" : ""));
      row.append(head, el("div", "holders", holders.join(", ") || "vago"));
      ranksBox.append(row);
    }

    const membersBox = el("section", "org-card");
    membersBox.append(el("h4", "", `Membros (${mine.members.length})`));
    if (mine.dispute_wait > 0) membersBox.append(el("p", "hint", `Você perdeu uma disputa: espere ${mine.dispute_wait}s.`));
    for (const member of mine.members) {
      const row = el("div", "org-member");
      const name = el("span", "name", `${member.name}${member.me ? " (você)" : ""}`);
      if (member.online) name.classList.add("online");
      row.append(name, el("span", "hint", member.title));
      const actions = el("span", "actions");
      if (member.can_dispute) {
        actions.append(button("Disputar cargo", () => this.op("dispute", { target: member.name }), "small"));
      }
      if (member.promote_to.length) {
        const select = el("select");
        for (const rankId of member.promote_to) {
          const opt = el("option", "", org.ranks[rankId].name);
          opt.value = rankId;
          select.append(opt);
        }
        actions.append(select, button("Promover", () => this.op("promote", { target: member.name, rank: select.value }), "small"));
      }
      if (member.can_demote) actions.append(button("Rebaixar", () => this.op("demote", { target: member.name }), "small"));
      if (member.can_expel) {
        actions.append(button("Expulsar", () => {
          if (window.confirm(`Expulsar ${member.name}?`)) this.op("expel", { target: member.name });
        }, "small danger"));
      }
      row.append(actions);
      membersBox.append(row);
    }

    const leave = button("Sair da organização", () => {
      if (window.confirm(`Sair de ${org.name}? Você perde o cargo.`)) this.op("leave");
    }, "danger");
    return [ranksBox, membersBox, leave];
  }
}
