// Telas de conta: entrar ou criar conta, escolher ou criar personagem, e a janela
// "Conta" dentro do jogo (trocar senha, trocar de personagem). Só mostra e envia
// pedidos; quem decide é o servidor (server/accounts.py).
//   {t: "account", op: "login" | "register", name, password}
//   {t: "char", op: "create", name, race} / {t: "char", op: "enter", name}
//   {t: "account", op: "logout"} / {t: "account", op: "password", old, new} / {t: "char", op: "leave"}

const $ = (id) => document.getElementById(id);

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

export class AccountScreens {
  constructor({ send }) {
    this.send = send; // (mensagem) => abre a conexão se preciso e envia
    this.registering = false;
    this.data = null; // última resposta {t: "account", name, chars, max, races}

    $("login").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this.error("");
      const name = $("login-name").value.trim();
      const password = $("login-password").value;
      if (this.registering && password !== $("login-password2").value) {
        this.error("As duas senhas não são iguais.");
        return;
      }
      this.send({ t: "account", op: this.registering ? "register" : "login", name, password });
    });
    $("login-mode").addEventListener("click", () => this.setRegistering(!this.registering));
    $("char-new").addEventListener("submit", (ev) => {
      ev.preventDefault();
      this.error("");
      const race = document.querySelector('input[name="race"]:checked')?.value ?? null;
      this.send({ t: "char", op: "create", name: $("char-name").value.trim(), race });
    });
    $("chars-logout").addEventListener("click", () => this.send({ t: "account", op: "logout" }));
  }

  setRegistering(on) {
    this.registering = on;
    $("login-password2").hidden = !on;
    $("login-password2").required = on;
    $("login-submit").textContent = on ? "Criar conta" : "Entrar";
    $("login-mode").textContent = on ? "Já tem conta? Entrar" : "Não tem conta? Criar uma";
    $("login-password").autocomplete = on ? "new-password" : "current-password";
    $("login-hint").textContent = on
      ? "Uma conta pode ter vários personagens. Guarde a senha: se esquecer, só a equipe consegue gerar outra."
      : "Entre com a sua conta para escolher o personagem.";
    this.error("");
  }

  // Mensagem de erro na tela que estiver aberta
  error(text) {
    $("login-error").textContent = $("chars").hidden ? text : "";
    $("chars-error").textContent = $("chars").hidden ? "" : text;
  }

  showLogin(text = "") {
    $("overlay").classList.remove("hidden");
    $("login").hidden = false;
    $("chars").hidden = true;
    $("login-password").value = "";
    $("login-password2").value = "";
    this.error(text);
  }

  // Resposta {t: "account"}: name null = saiu da conta; senão mostra os personagens
  update(msg) {
    if (!msg.name) {
      this.data = null;
      this.showLogin();
      return;
    }
    this.data = msg;
    $("overlay").classList.remove("hidden");
    $("login").hidden = true;
    $("chars").hidden = false;
    $("chars-error").textContent = "";
    $("chars-account").textContent = `Conta ${msg.name} · ${msg.chars.length} de ${msg.max} personagens`;

    const list = $("chars-list");
    list.replaceChildren();
    if (!msg.chars.length) list.append(el("p", "hint", "Nenhum personagem ainda: crie o primeiro abaixo."));
    for (const c of msg.chars) {
      const card = el("div", "char-card");
      const info = el("div", "info");
      info.append(el("b", "", c.name),
        el("span", "meta", [[c.race, c.class].filter(Boolean).join(" · "), `Nv ${c.lv}`, c.map].filter(Boolean).join(" · ")));
      const play = el("button", "", c.online ? "Já está no jogo" : "Jogar");
      play.type = "button";
      play.disabled = c.online;
      play.addEventListener("click", () => {
        this.error("");
        this.send({ t: "char", op: "enter", name: c.name });
      });
      card.append(info, play);
      list.append(card);
    }

    const full = msg.chars.length >= msg.max;
    $("char-new").hidden = full;
    $("chars-full").hidden = !full;
    $("char-name").value = "";
    $("race-options").replaceChildren(
      ...msg.races.map((race, i) => {
        const label = document.createElement("label");
        label.innerHTML = '<input type="radio" name="race"><span class="name"></span><span class="desc"></span>';
        const radio = label.querySelector("input");
        radio.value = race.id;
        radio.checked = i === 0;
        label.querySelector(".name").textContent = race.name;
        label.querySelector(".desc").textContent = race.description;
        return label;
      }),
    );
  }

  hide() {
    $("overlay").classList.add("hidden");
  }
}

// Janela "Conta" no jogo: trocar a senha e voltar para a lista de personagens
export class AccountWindow {
  constructor({ send }) {
    this.send = send;
    this.root = $("account");
    this.root.hidden = true;
    $("account-close").addEventListener("click", () => this.toggle(false));
    $("password-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const old = $("pw-old").value;
      const fresh = $("pw-new").value;
      if (fresh !== $("pw-new2").value) {
        this.result(false, "As duas senhas novas não são iguais.");
        return;
      }
      this.result(true, "Conferindo…");
      this.send({ t: "account", op: "password", old, new: fresh });
    });
    $("char-leave").addEventListener("click", () => {
      if (window.confirm("Sair deste personagem e voltar para a lista?")) this.send({ t: "char", op: "leave" });
    });
    for (const id of ["pw-old", "pw-new", "pw-new2"]) {
      $(id).addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
    }
  }

  get isOpen() {
    return !this.root.hidden;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    if (this.isOpen) this.result(true, "");
  }

  // Resposta {t: "account_result", ok, text}
  result(ok, text) {
    const box = $("account-result");
    box.textContent = text;
    box.className = ok ? "" : "error";
    if (ok && text === "Senha trocada.") {
      for (const id of ["pw-old", "pw-new", "pw-new2"]) $(id).value = "";
    }
  }
}
