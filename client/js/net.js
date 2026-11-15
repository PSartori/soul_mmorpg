// Conexão WebSocket com o servidor. Mensagens são objetos JSON com o campo "t" (tipo).

export class Connection {
  constructor(url, { onMessage, onClose }) {
    this.queue = [];
    this.ws = new WebSocket(url);
    this.ws.addEventListener("open", () => {
      for (const text of this.queue) this.ws.send(text);
      this.queue = [];
    });
    this.ws.addEventListener("message", (ev) => {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      onMessage(data);
    });
    this.ws.addEventListener("close", () => onClose());
  }

  get closed() {
    return this.ws.readyState >= WebSocket.CLOSING;
  }

  send(obj) {
    const text = JSON.stringify(obj);
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(text);
    else if (this.ws.readyState === WebSocket.CONNECTING) this.queue.push(text);
  }
}

export function gameSocketUrl() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws`;
}
