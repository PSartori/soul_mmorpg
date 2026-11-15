// Teclado: movimento é amostrado a cada tick do cliente; skills e ações são eventos.

const MOVE_KEYS = {
  KeyW: "up", ArrowUp: "up",
  KeyS: "down", ArrowDown: "down",
  KeyA: "left", ArrowLeft: "left",
  KeyD: "right", ArrowRight: "right",
};

// Barra de skills: teclas 1–8; Espaço e F são atalhos para os slots 1 e 2
const SLOT_KEYS = {
  Space: 0, KeyF: 1,
  Digit1: 0, Digit2: 1, Digit3: 2, Digit4: 3, Digit5: 4, Digit6: 5, Digit7: 6, Digit8: 7,
};
export const SLOT_LABELS = ["1 · Espaço", "2 · F", "3", "4", "5", "6", "7", "8"];

const ACTION_KEYS = { KeyM: "meditate", KeyR: "rest" };
const LOCAL_KEYS = { KeyI: "inventory", KeyG: "admin", Escape: "escape" }; // só mexem na interface

export function isTypingInField() {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
}

export function setupInput({ onSlot, onAction, onLocal, onEnter, enabled }) {
  const held = { up: false, down: false, left: false, right: false };
  // Toques rápidos (apertou e soltou entre dois ticks) ainda contam no próximo tick
  const tapped = { up: false, down: false, left: false, right: false };

  function clear() {
    for (const k of Object.keys(held)) held[k] = tapped[k] = false;
  }

  window.addEventListener("keydown", (ev) => {
    if (!enabled() || isTypingInField() || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (ev.code === "Enter") {
      ev.preventDefault();
      clear();
      onEnter();
      return;
    }
    const dir = MOVE_KEYS[ev.code];
    if (dir) {
      ev.preventDefault();
      held[dir] = tapped[dir] = true;
      return;
    }
    if (ev.code in SLOT_KEYS) {
      ev.preventDefault();
      onSlot(SLOT_KEYS[ev.code]); // segurar repete; o cliente e o servidor respeitam o cooldown
      return;
    }
    const local = LOCAL_KEYS[ev.code];
    if (local && !ev.repeat) {
      ev.preventDefault();
      onLocal(local);
      return;
    }
    const action = ACTION_KEYS[ev.code];
    if (action && !ev.repeat) {
      ev.preventDefault();
      onAction(action);
    }
  });

  window.addEventListener("keyup", (ev) => {
    const dir = MOVE_KEYS[ev.code];
    if (dir) held[dir] = false;
  });

  window.addEventListener("blur", clear);

  return {
    clear,
    // Estado das setas para este tick (seguradas ou tocadas desde o último)
    sample() {
      const keys = {};
      for (const k of Object.keys(held)) {
        keys[k] = held[k] || tapped[k];
        tapped[k] = false;
      }
      return keys;
    },
  };
}
