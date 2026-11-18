// Relatar bug: janela com um campo de texto. O servidor junta personagem, mapa,
// posição, zona e hora; daqui vão a visão (2D/3D), o navegador, o tamanho da tela e os
// últimos erros de JavaScript desta página (guardados desde que ela abriu).
//   {t: "bug", text, client: {view, agent, screen, errors}}  ->  {t: "bug_result", ok, text}

const $ = (id) => document.getElementById(id);
const MAX_ERRORS = 5;
const recentErrors = [];

function remember(text) {
  recentErrors.push(`${new Date().toLocaleTimeString("pt-BR")} ${text}`.slice(0, 300));
  if (recentErrors.length > MAX_ERRORS) recentErrors.shift();
}

window.addEventListener("error", (ev) => {
  const where = ev.filename ? ` (${ev.filename.split("/").pop()}:${ev.lineno})` : "";
  remember(`${ev.message}${where}`);
});
window.addEventListener("unhandledrejection", (ev) => remember(`promessa: ${ev.reason?.message ?? ev.reason}`));

export class BugWindow {
  constructor({ send, getView }) {
    this.send = send;
    this.getView = getView;
    this.root = $("bug");
    this.root.hidden = true;
    const text = $("bug-text");
    text.addEventListener("keydown", (ev) => ev.stopPropagation()); // não move o personagem
    text.addEventListener("input", () => this.count());
    $("bug-close").addEventListener("click", () => this.toggle(false));
    $("bug-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const value = text.value.trim();
      if (value.length < 5) {
        this.result(false, "Descreva o problema com um pouco mais de detalhe.");
        return;
      }
      this.result(true, "Enviando…");
      this.send({
        t: "bug",
        text: value,
        client: {
          view: this.getView(),
          agent: navigator.userAgent,
          screen: `${window.innerWidth}×${window.innerHeight}`,
          errors: [...recentErrors],
        },
      });
    });
    this.count();
  }

  get isOpen() {
    return !this.root.hidden;
  }

  toggle(force) {
    this.root.hidden = force === undefined ? !this.root.hidden : !force;
    if (this.isOpen) {
      this.result(true, "");
      $("bug-text").focus();
    }
  }

  count() {
    $("bug-count").textContent = `${$("bug-text").value.length} / ${$("bug-text").maxLength}`;
  }

  // Resposta do servidor: aceito (limpa o texto) ou recusado (mantém para tentar de novo)
  result(ok, text) {
    const box = $("bug-result");
    box.textContent = text;
    box.className = ok ? "" : "error";
    if (ok && text.startsWith("Obrigado")) {
      $("bug-text").value = "";
      this.count();
    }
  }
}
