// Grupo: painel no lado (membros com vida e nível), aviso de convite com botões e as
// opções de grupo no menu do jogador. Só mostra e envia pedidos ({t: "party", op: ...});
// quem decide é o servidor (server/parties.py). O estado chega em you.party (só quando
// muda; null = sem grupo) e o convite em you.pinv.

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

export class PartyPanel {
  constructor({ root, send, getMyId, getMapId }) {
    this.root = root;
    this.send = send;
    this.getMyId = getMyId;
    this.getMapId = getMapId;
    this.data = null; // {leader, members: [{id, n, hp, mhp, lv, map, dead}]}
    this.root.hidden = true;
  }

  get inParty() {
    return !!this.data;
  }

  get isLeader() {
    return this.data?.leader === this.getMyId();
  }

  // ids dos membros (para destacar os nomes no desenho)
  get memberIds() {
    return new Set(this.data?.members.map((m) => m.id) ?? []);
  }

  has(id) {
    return !!this.data?.members.some((m) => m.id === id);
  }

  update(data) {
    this.data = data;
    this.render();
  }

  render() {
    const data = this.data;
    this.root.hidden = !data;
    if (!data) return;
    const myId = this.getMyId();
    const leader = data.leader === myId;

    const header = el("header");
    header.append(el("h3", "", `Grupo (${data.members.length})`));
    header.append(button("Sair", () => {
      if (window.confirm("Sair do grupo?")) this.send({ t: "party", op: "leave" });
    }, "small"));

    const list = el("ul", "party-list");
    for (const m of data.members) {
      const li = el("li", m.dead ? "dead" : "");
      const top = el("div", "row");
      const name = el("span", "name", `${m.id === data.leader ? "★ " : ""}${m.n}`);
      if (m.id === myId) name.classList.add("me");
      top.append(name, el("span", "lv", `Nv ${m.lv}`));
      if (leader && m.id !== myId) {
        const lead = button("Líder", () => this.send({ t: "party", op: "lead", target: m.n }), "tiny");
        lead.title = `Passar a liderança para ${m.n}`;
        const kick = button("✕", () => {
          if (window.confirm(`Tirar ${m.n} do grupo?`)) this.send({ t: "party", op: "kick", target: m.n });
        }, "tiny danger");
        kick.title = `Tirar ${m.n} do grupo`;
        top.append(lead, kick);
      }
      const bar = el("div", "hp");
      const fill = el("span");
      fill.style.width = `${m.mhp > 0 ? Math.max(0, Math.min(100, (100 * m.hp) / m.mhp)) : 0}%`;
      bar.append(fill);
      li.append(top, bar);
      const where = m.dead ? "derrotado" : m.map !== this.getMapId() ? "em outro mapa" : "";
      if (where) li.append(el("div", "where", where));
      list.append(li);
    }
    this.root.replaceChildren(header, list);
  }
}

// Aviso de convite para o grupo (embaixo do aviso de duelo, se os dois aparecerem)
export class PartyInvitePanel {
  constructor({ root, send }) {
    this.root = root;
    this.send = send;
    this.text = el("p", "text");
    const actions = el("div", "actions");
    actions.append(
      button("Aceitar", () => this.send({ t: "party", op: "accept" }), "accept"),
      button("Recusar", () => this.send({ t: "party", op: "decline" })),
    );
    this.root.replaceChildren(this.text, actions);
    this.root.hidden = true;
  }

  update(invite) {
    this.root.hidden = !invite;
    if (invite) this.text.textContent = `${invite.from} convidou você para o grupo. (${invite.left}s)`;
  }
}

// Opções de grupo no menu ao clicar num jogador: [rótulo, ação, perigoso?]
export function partyMenuItems(panel, target, send) {
  const items = [];
  const name = target.n;
  if (panel.has(target.id)) {
    if (panel.isLeader) {
      items.push(["Passar a liderança do grupo", () => send({ t: "party", op: "lead", target: name })]);
      items.push(["Tirar do grupo", () => {
        if (window.confirm(`Tirar ${name} do grupo?`)) send({ t: "party", op: "kick", target: name });
      }, true]);
    }
  } else if (!panel.inParty || panel.isLeader) {
    items.push(["Convidar para o grupo", () => send({ t: "party", op: "invite", target: name })]);
  }
  return items;
}
