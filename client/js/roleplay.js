// Roleplay: balões de fala (canal Local e ações), janela de perfil (P) e opções do
// menu do jogador (ver perfil, mensagem privada, ignorar). Quem decide é o servidor
// (server/social.py); aqui só se mostra e se pede.
//   {t: "social", op: "profile", name?}       -> {t: "profile", name, race, cls, lv, o, st, text, me, ignored, can_clear}
//   {t: "social", op: "set_profile", text}
//   {t: "social", op: "ignore" | "unignore", name}
import { rankTitle } from "./render.js";

const $ = (id) => document.getElementById(id);
const PROFILE_MAX = 600;
export const PM_COLOR = "#ff9ad5";
export const LOCAL_COLOR = "#a8d8a0";

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

// ------------------------------------------------------------------ balões de fala

// Quanto tempo o balão fica (ms): mais tempo para textos longos
export function bubbleTtl(text) {
  return Math.min(10000, 4000 + text.length * 60);
}

// Quebra o texto em linhas de no máximo `width` px (até `maxLines`; o resto vira "…")
function wrap(ctx, text, width, maxLines) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width <= width || !line) {
      line = next;
    } else {
      lines.push(line);
      line = word;
    }
  }
  if (line) lines.push(line);
  if (lines.length > maxLines) {
    lines.length = maxLines;
    lines[maxLines - 1] = `${lines[maxLines - 1].replace(/\s*\S*$/, "")}…`;
  }
  // palavra sozinha maior que a largura: corta
  return lines.map((l) => {
    if (ctx.measureText(l).width <= width) return l;
    while (l.length > 1 && ctx.measureText(`${l}…`).width > width) l = l.slice(0, -1);
    return `${l}…`;
  });
}

// Desenha o balão com a ponta em (x, y): logo acima do nome do personagem.
// bubble = {text, emote, born, ttl}. minTop: o balão não sobe além disto (fica dentro da tela).
export function drawBubble(ctx, x, y, bubble, now, minTop = -Infinity) {
  const age = now - bubble.born;
  const alpha = Math.max(0, Math.min(1, (bubble.ttl - age) / 500)); // some nos últimos 0,5 s
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = bubble.emote ? "italic 12px system-ui, sans-serif" : "12px system-ui, sans-serif";
  const text = bubble.emote ? `* ${bubble.text} *` : bubble.text;
  const lines = wrap(ctx, text, 180, 4);
  const lineH = 15;
  const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14;
  const h = lines.length * lineH + 8;
  const left = x - w / 2;
  const top = Math.max(minTop, y - h - 6);
  const tip = top === y - h - 6; // empurrado para baixo (borda da tela): sem a ponta
  ctx.fillStyle = bubble.emote ? "rgba(46, 34, 70, 0.92)" : "rgba(250, 250, 245, 0.95)";
  ctx.strokeStyle = bubble.emote ? "rgba(200, 170, 255, 0.7)" : "rgba(0, 0, 0, 0.5)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(left, top, w, h, 6);
  if (tip) {
    ctx.moveTo(x - 5, top + h);
    ctx.lineTo(x, top + h + 6);
    ctx.lineTo(x + 5, top + h);
  }
  ctx.fill();
  ctx.stroke();
  if (tip) ctx.fillRect(x - 4, top + h - 1, 8, 2); // cobre a borda entre o balão e a ponta
  ctx.fillStyle = bubble.emote ? "#e6d4ff" : "#1b1d24";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  lines.forEach((l, i) => ctx.fillText(l, x, top + 5 + i * lineH));
  ctx.restore();
}

// ------------------------------------------------------------------ menu do jogador

// Opções do topo do menu (perfil e mensagem privada) e do fim (ignorar)
export function roleplayMenuItems({ target, send, whisper, ignored }) {
  const name = target.n;
  const isIgnored = ignored.has(name.toLowerCase());
  return {
    first: [
      ["Ver perfil", () => send({ t: "social", op: "profile", name })],
      ["Mensagem privada", () => whisper(name)],
    ],
    last: [
      isIgnored
        ? ["Deixar de ignorar", () => send({ t: "social", op: "unignore", name })]
        : ["Ignorar", () => send({ t: "social", op: "ignore", name }), true],
    ],
  };
}

// ------------------------------------------------------------------ janela de perfil

export class ProfileWindow {
  constructor({ getDefs, send, whisper, openInCorner }) {
    this.getDefs = getDefs;
    this.send = send;
    this.whisper = whisper;
    this.openInCorner = openInCorner; // abre no canto (fechando as outras janelas de lá)
    this.root = $("profile");
    this.body = $("profile-body");
    this.title = $("profile-title");
    this.root.hidden = true;
    this.data = null;
    this.ignored = [];
    $("profile-close").addEventListener("click", () => this.toggle(false));
  }

  get isOpen() {
    return !this.root.hidden;
  }

  // Só mostra ou esconde (as janelas do canto chamam isto para fechar umas às outras)
  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
  }

  // Tecla P / botão do painel: pede o próprio perfil (a janela abre com a resposta) ou fecha
  toggleOwn() {
    if (this.isOpen && this.data?.me) this.toggle(false);
    else this.send({ t: "social", op: "profile" });
  }

  // Resposta do servidor (perfil de alguém ou o próprio): abre a janela
  update(data) {
    // não apaga o que está sendo digitado quando o próprio perfil chega de novo
    const typing = this.isOpen && this.data?.me && data.me && document.activeElement === $("profile-text");
    this.data = data;
    if (!this.isOpen) this.openInCorner(this);
    if (!typing) this.render();
  }

  // Lista de ignorados (vem no snapshot quando muda)
  setIgnored(list) {
    this.ignored = list;
    if (this.data && !this.data.me) {
      this.data.ignored = list.some((n) => n.toLowerCase() === this.data.name.toLowerCase());
    }
    if (this.isOpen && document.activeElement !== $("profile-text")) this.render();
  }

  infoLines(data) {
    const defs = this.getDefs();
    const lines = [];
    const race = defs.races[data.race]?.name;
    const cls = defs.classes[data.cls]?.name;
    lines.push(el("p", "profile-meta", [race, cls, `Nv ${data.lv}`].filter(Boolean).join(" · ")));
    if (data.o) {
      const org = defs.orgs[data.o[0]];
      const line = el("p", "profile-org", `${org?.name ?? data.o[0]} · ${rankTitle(defs, data.o)}`);
      line.style.color = org?.color ?? "";
      lines.push(line);
    }
    if (data.st) {
      const role = defs.roles?.[data.st];
      const line = el("p", "profile-org", role?.name ?? data.st);
      line.style.color = role?.color ?? "";
      lines.push(line);
    }
    return lines;
  }

  render() {
    const data = this.data;
    if (!data) return;
    this.title.textContent = data.me ? "Seu perfil" : data.name;
    this.body.replaceChildren(...(data.me ? this.ownView(data) : this.otherView(data)));
  }

  ownView(data) {
    const name = el("h4", "profile-name", data.name);
    const label = el("label", "profile-label", "Descrição: aparência, história, jeito de ser");
    label.htmlFor = "profile-text";
    const text = el("textarea");
    text.id = "profile-text";
    text.maxLength = PROFILE_MAX;
    text.rows = 7;
    text.value = data.text;
    text.placeholder = "Ex.: Alta, cabelo grisalho preso numa trança. Fala pouco e sempre carrega um caderno.";
    text.addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
    const count = el("span", "hint");
    const updateCount = () => (count.textContent = `${text.value.length}/${PROFILE_MAX}`);
    text.addEventListener("input", updateCount);
    updateCount();
    const save = button("Salvar", () => this.send({ t: "social", op: "set_profile", text: text.value }));
    const row = el("div", "profile-row");
    row.append(count, save);

    const ignoredBox = el("section", "profile-ignored");
    ignoredBox.append(el("h4", "", `Ignorados (${this.ignored.length})`));
    if (!this.ignored.length) {
      ignoredBox.append(el("p", "hint", "Ninguém. Para ignorar alguém, clique no jogador e escolha \"Ignorar\"."));
    }
    for (const who of this.ignored) {
      const line = el("div", "profile-ignored-row");
      line.append(el("span", "", who),
        button("Deixar de ignorar", () => this.send({ t: "social", op: "unignore", name: who }), "small"));
      ignoredBox.append(line);
    }
    return [name, ...this.infoLines(data), label, text, row, ignoredBox];
  }

  otherView(data) {
    const nodes = this.infoLines(data);
    nodes.push(data.text ? el("p", "profile-desc", data.text) : el("p", "hint", "Sem descrição."));
    const actions = el("div", "profile-actions");
    actions.append(button("Mensagem privada", () => this.whisper(data.name)));
    actions.append(data.ignored
      ? button("Deixar de ignorar", () => this.send({ t: "social", op: "unignore", name: data.name }))
      : button("Ignorar", () => this.send({ t: "social", op: "ignore", name: data.name }), "danger"));
    if (data.can_clear) {
      actions.append(button("Apagar descrição (equipe)", () => {
        if (window.confirm(`Apagar a descrição do perfil de ${data.name}?`)) {
          this.send({ t: "admin", op: "clear_profile", target: data.name });
        }
      }, "danger"));
    }
    nodes.push(actions);
    return nodes;
  }
}
