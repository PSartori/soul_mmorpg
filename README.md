# RPG Online — protótipo

RPG online em 2D com treino por uso, combate em tempo real e PvP, inspirado nas
mecânicas dos jogos de anime do BYOND (veja `referencia-bleach-shinzui.md`).
Mundo, raças e nomes próprios ainda serão definidos; por enquanto os nomes são provisórios.

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) (já instalado nesta máquina).

```bash
./run.sh
```

Abra http://127.0.0.1:8080, escolha um nome e uma senha (o personagem é criado
na primeira vez). Para testar multiplayer, abra outra aba e entre com outro nome.

Testes do servidor:

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/rpg-online uv run python -m unittest -v
```

O ambiente virtual fica em `~/.venvs/rpg-online` (fora do Google Drive, para não
sincronizar milhares de arquivos). O banco de dados fica em `var/game.db`.

## Controles

| Tecla | Ação |
|---|---|
| WASD / setas | andar |
| Espaço | golpe corpo a corpo |
| F | rajada de energia (gasta energia) |
| M | meditar (treina Espírito, gera cansaço) |
| R | descansar (recupera cansaço e vida) |
| Enter | chat (`/who` lista quem está online) |

## Arquitetura

O **servidor é a autoridade**: o cliente só envia intenções (teclas, ações, chat)
e desenha o estado que recebe. Isso impede trapaça e deixa toda a regra do jogo em Python.

```
server/              Python (aiohttp)
  main.py            HTTP + WebSocket, loop de 20 ticks/s, envio das mensagens
  world.py           simulação: login, movimento, combate, treino, inimigos
  entities.py        Player, Enemy, Projectile e atributos derivados
  stats.py           atributos treináveis (nível + XP)
  tilemap.py         mapa ASCII e colisão de caixas com tiles
  db.py              SQLite: personagens e senhas (PBKDF2)
  config.py          TODOS os números de balanceamento
client/              JavaScript puro, sem build
  index.html, style.css
  js/main.js         estado, HUD, chat, loop de desenho
  js/render.js       desenho no canvas (arte provisória com formas)
  js/input.js        teclado
  js/net.js          WebSocket
data/maps/inicio.txt mapa editável em texto (legenda no topo do arquivo)
tests/               testes das regras do servidor (unittest)
```

`World` não conhece rede: tudo que precisa sair vai para uma caixa de saída
(`drain_outbox`), que o `main.py` envia. Por isso dá para testar as regras sem servidor.

### Protocolo (JSON pelo WebSocket, campo `t` = tipo)

Cliente → servidor: `login {name, password}`, `input {up, down, left, right}`,
`attack`, `blast`, `meditate`, `rest`, `chat {text}`.

Servidor → cliente: `welcome {id, map, stat_labels}`, `error {msg}`,
`snap {ents, projs, fx, you}` (20×/s), `chat {from, text}`, `sys {text}`.

### Regras atuais

- **Atributos**: Força (golpe), Espírito (energia e rajada), Agilidade (velocidade),
  Vitalidade (vida). Cada um sobe com o uso: andar → Agilidade, acertar golpes → Força,
  acertar rajadas e meditar → Espírito, levar dano → Vitalidade. Matar dá bônus.
- **Meditação** gera cansaço; ao chegar a 100 para sozinha, e só volta com cansaço
  abaixo de 75, o que obriga a descansar.
- **Zona segura** (piso de pedra): sem PvP e inimigos não entram.
- **Morte**: renasce no ponto inicial depois de 3 s.
- **Salvamento**: ao sair e automaticamente a cada 60 s.

## Pendências conhecidas

- Sem previsão de movimento no cliente: com ping alto o próprio personagem vai
  parecer "atrasado". Resolver quando hospedar na VPS.
- Login sem HTTPS: na VPS, colocar atrás de um proxy com TLS (ex.: Caddy) antes de abrir ao público.
- O hash de senha roda no loop do servidor (~0,1 s por login); ok para poucos jogadores.
- Arte provisória (formas geométricas).
