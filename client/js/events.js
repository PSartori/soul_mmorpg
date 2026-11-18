// Painel dos eventos em ondas (invasões) no lado da tela. O estado vem no snapshot:
//   you.ev = [{id, name, map, here, wave, waves, left, time, pause, dmg, min}] (lista vazia = nenhum)
// Quem começa e encerra é a equipe (janela de administração) ou a agenda do servidor.

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export class EventPanel {
  constructor({ root }) {
    this.root = root;
    this.root.hidden = true;
  }

  update(events) {
    this.root.hidden = !events?.length;
    if (!events?.length) return;
    this.root.replaceChildren(...events.map((ev) => this.card(ev)));
  }

  card(ev) {
    const card = el("div", "event-card");
    const head = el("div", "event-head");
    head.append(el("b", "", `⚔ ${ev.name}`), el("span", "event-time", clock(ev.time)));
    card.append(head);
    const wave = ev.waves > 1 ? `Onda ${ev.wave}/${ev.waves} · ` : "";
    const status = ev.pause > 0
      ? `${wave}próxima onda em ${ev.pause} s`
      : `${wave}${ev.left} ${ev.left === 1 ? "inimigo" : "inimigos"}`;
    card.append(el("div", "event-line", `${ev.map} · ${status}`));
    if (ev.dmg > 0) {
      const ok = ev.dmg >= ev.min;
      card.append(el("div", `event-line ${ok ? "ok" : ""}`,
        ok ? `Você está participando (${ev.dmg} de dano).` : `Seu dano: ${ev.dmg} (mínimo ${ev.min}).`));
    } else {
      card.append(el("div", "event-line hint", ev.here
        ? "Ataque os invasores para participar da recompensa."
        : `Vá até ${ev.map} para ajudar.`));
    }
    return card;
  }
}
