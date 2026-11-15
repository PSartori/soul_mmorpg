// Teclado: movimento é um estado (teclas seguradas); ações são eventos.

const MOVE_KEYS = {
  KeyW: "up", ArrowUp: "up",
  KeyS: "down", ArrowDown: "down",
  KeyA: "left", ArrowLeft: "left",
  KeyD: "right", ArrowRight: "right",
};

// repeat: se segurar a tecla, repete a ação (o servidor limita pelo cooldown)
const ACTION_KEYS = {
  Space: { action: "attack", repeat: true },
  KeyF: { action: "blast", repeat: true },
  KeyM: { action: "meditate", repeat: false },
  KeyR: { action: "rest", repeat: false },
};

export function isTypingInField() {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
}

export function setupInput({ onMove, onAction, onEnter, enabled }) {
  const held = { up: false, down: false, left: false, right: false };
  const emit = () => onMove({ ...held });

  function clear() {
    if (Object.values(held).some(Boolean)) {
      for (const k of Object.keys(held)) held[k] = false;
      emit();
    }
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
      if (!held[dir]) {
        held[dir] = true;
        emit();
      }
      return;
    }
    const binding = ACTION_KEYS[ev.code];
    if (binding) {
      ev.preventDefault();
      if (!ev.repeat || binding.repeat) onAction(binding.action);
    }
  });

  window.addEventListener("keyup", (ev) => {
    const dir = MOVE_KEYS[ev.code];
    if (dir && held[dir]) {
      held[dir] = false;
      emit();
    }
  });

  window.addEventListener("blur", clear);
  return { clear };
}
