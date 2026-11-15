# RPG Online — protótipo

RPG online em 2D com treino por uso, combate em tempo real e PvP, inspirado nas
mecânicas dos jogos de anime do BYOND (veja `referencia-bleach-shinzui.md`).
Mundo, raças e nomes próprios ainda serão definidos; por enquanto os nomes são provisórios.

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) (já instalado nesta máquina).

```bash
./run.sh
```

Abra http://127.0.0.1:8080, escolha um nome, uma senha e uma raça (o personagem é
criado na primeira vez; para personagens existentes a raça é ignorada). Para testar
multiplayer, abra outra aba e entre com outro nome.

Testes do servidor:

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/rpg-online uv run python -m unittest -v
```

O ambiente virtual fica em `~/.venvs/rpg-online` (fora do Google Drive, para não
sincronizar milhares de arquivos). O banco de dados fica em `var/game.db`, com
backups automáticos em `var/backups/` (um ao ligar o servidor e outro a cada 6 h;
guarda os 28 mais recentes). Para restaurar, pare o servidor e copie um backup
por cima de `var/game.db`.

## Controles

| Tecla | Ação |
|---|---|
| WASD / setas | andar |
| 1–8 (Espaço = 1, F = 2) | skills da barra |
| M | meditar (treina Espírito, gera cansaço) |
| R | descansar (recupera cansaço e vida) |
| I | inventário (clique: usar/equipar · Shift + clique: largar · clique no equipado: tirar) |
| Enter | chat (`/who` lista quem está online) |
| `/duelo Nome` | desafia alguém por perto para um duelo |
| `/aceitar`, `/recusar` | responde a um desafio |
| `/render` | desiste do duelo |

## Conteúdo (data/)

Skills, efeitos, inimigos, raças, classes e regras são **dados**, não código.
Cada arquivo tem no topo a explicação de todos os campos.

| Arquivo | O que define |
|---|---|
| `data/skills.yaml` | técnicas: tipo (melee, projectile, area, dash, self), custo, recarga, dano, efeitos, treino |
| `data/effects.yaml` | efeitos de status: modificadores (velocidade, dano...), flags (atordoado, silenciado), dano/cura periódicos |
| `data/enemies.yaml` | tipos de inimigo: vida, velocidade, alcance de perseguição, skills que a IA usa, letra no mapa, moedas e tabela de saque |
| `data/items.yaml` | itens: equipamento (slot, bônus, restrição de raça/classe), consumível (cura, energia, efeitos), material |
| `data/races.yaml` | raças: se aparecem na criação, classe inicial, bônus de vida/energia/velocidade |
| `data/classes.yaml` | classes: raça, skills da barra, multiplicador de treino por atributo |
| `data/rules.yaml` | regras "quando X, se Y, faça Z": morte, abates, subida de nível (veja abaixo) |
| `data/player.yaml` | raça padrão para personagens antigos |
| `data/maps/inicio.txt` | mapa em texto (legenda no topo do arquivo) |
| `data/maps/inicio.yaml` | zonas do mapa: zona padrão e áreas PvE/PvP |

### Regras (data/rules.yaml)

Cada regra diz **quando** (evento), **se** (condições) e **o que fazer** (ações):

```yaml
humano_vira_espectral:
  when: death
  if: { race: humano, killer_enemy: espectro }
  do:
    - set_race: espectral
    - message: "Algo frio toma conta de você. Você renasce como {class}."
  stop: true
```

- **Eventos:** `death` (com quem matou, raça/classe dele, skill, efeito, efeitos
  ativos, zona), `kill`, `level_up` e `duel_end` (vitória/derrota/empate e motivo).
- **Ações:** mensagens, perder/ganhar XP, trocar raça ou classe, marcas de
  progressão (`set_flag`), aplicar efeitos, dar itens e dar/tirar moedas.
- **Condições especiais:** `chance`, `flags` e `not_flags`.

As regras de cada evento rodam de cima para baixo; `stop: true` encerra. A lista
completa de campos e ações está no topo do arquivo. As regras que estão lá hoje
são **exemplos provisórios**: troque pelas regras reais do jogo.

Os arquivos são validados ao subir o servidor: campo com erro de digitação, tipo
errado ou referência inexistente (ex.: skill que aplica um efeito que não existe)
para o servidor com uma mensagem dizendo o arquivo, o item e o campo.

Para criar uma skill nova basta adicioná-la em `skills.yaml` e na lista de
`player.yaml`; só é preciso mexer em Python para criar um *tipo* novo de skill
ou de efeito.

## Arquitetura

O **servidor é a autoridade**: o cliente só envia intenções (movimento, skills, chat)
e desenha o estado que recebe. Isso impede trapaça e deixa toda a regra do jogo em Python.

```
server/              Python (aiohttp)
  main.py            HTTP + WebSocket, loop de 20 ticks/s, envio das mensagens
  world.py           simulação: login, movimento, skills, efeitos, IA, execução das regras
  content.py         carrega e valida data/*.yaml
  rules.py           vocabulário das regras (eventos, campos, ações) e comparação
  duels.py           desafios e duelos
  inventory.py       inventário: espaços que empilham, equipamento e moedas
  saves.py           versão do formato de save e migrações (sem wipes!)
  entities.py        Player, Enemy, Projectile, efeitos ativos e atributos derivados
  stats.py           atributos treináveis (nível + XP)
  tilemap.py         mapa ASCII, zonas e colisão de caixas com tiles
  db.py              SQLite: personagens e senhas (PBKDF2)
  config.py          números de regra do motor (o resto está em data/)
client/              JavaScript puro, sem build
  index.html, style.css
  js/main.js         estado, previsão de movimento, HUD, barra de skills, chat
  js/physics.js      cópia da colisão/movimento do servidor (previsão)
  js/render.js       desenho no canvas (arte provisória com formas)
  js/inventory.js    janela de inventário
  js/input.js        teclado
  js/net.js          WebSocket
data/                conteúdo do jogo (veja acima)
tests/               testes das regras do servidor (unittest)
```

`World` não conhece rede: tudo que precisa sair vai para uma caixa de saída
(`drain_outbox`), que o `main.py` envia. Por isso dá para testar as regras sem servidor.

### Previsão de movimento

O cliente manda um comando de movimento numerado por tick (20/s) e já move o
próprio personagem na hora, com a mesma física do servidor (`physics.js`). O
servidor executa um comando por tick e devolve no snapshot a posição oficial e o
número do último comando executado (`ack`). O cliente parte dessa posição e
reaplica os comandos ainda não confirmados. Como a física é idêntica, o resultado
bate e não há "puxões"; o teste `tests/test_physics_parity.py` garante isso
comparando os dois lados (precisa do Node instalado).

**Se mudar `move_box` em `tilemap.py` ou `_move_player` em `world.py`, mude
`physics.js` também** — o teste de paridade acusa se esquecer.

Para depurar no console do navegador: `__game.state`, `__game.pred`, `__game.pending`.

### Protocolo (JSON pelo WebSocket, campo `t` = tipo)

HTTP: `GET /api/races` lista as raças da tela de criação.

Cliente → servidor: `login {name, password, race}`, `move {seq, up, down, left, right}`,
`skill {id, f}`, `item {op: use|drop, slot: n}`, `item {op: unequip, slot: weapon|armor|accessory}`,
`meditate`, `rest`, `chat {text}`.

Servidor → cliente: `welcome {id, map, skills, effects, enemies, stat_labels, ...}`,
`error {msg}`, `snap {ents, projs, drops, fx, you}` (20×/s; `you.inv` só vem quando o
inventário muda), `chat {from, text}`, `sys {text}`.

### Regras atuais

- **Atributos**: Força, Espírito (energia), Agilidade (velocidade), Vitalidade (vida).
  Cada um sobe com o uso: andar → Agilidade, meditar → Espírito, levar dano →
  Vitalidade, e cada skill treina o que estiver no campo `train`. Matar dá bônus
  ao atributo principal da skill que deu o golpe final.
- **Efeitos**: reaplicar renova a duração (não acumula); efeitos diferentes se
  multiplicam; morrer limpa todos.
- **Inimigos** perseguem em linha reta e contornam obstáculos simples pelo lado
  (não é pathfinding: labirintos ainda os prendem).
- **Meditação** gera cansaço; ao chegar a 100 para sozinha, e só volta com cansaço
  abaixo de 75, o que obriga a descansar.
- **Zonas** (`data/maps/<mapa>.yaml`):
  - **segura** (piso de pedra `:`): ninguém fere jogadores e inimigos não entram;
  - **PvE** (padrão): inimigos atacam; jogadores não se atacam;
  - **PvP** (áreas marcadas, com borda vermelha no mapa): vale tudo entre jogadores.
- **Duelos**: combinados com `/duelo` e `/aceitar`, valem em qualquer zona (inclusive
  na segura) depois de 3 s de contagem. Só os dois se ferem e ninguém de fora
  interfere. **Não matam**: o perdedor fica com 1 de vida. Também terminam por
  desistência, saída do jogo, distância (15 tiles) ou tempo (3 min, empate). Ao
  fim, os efeitos dos dois são limpos e o evento `duel_end` das regras dispara.
- **Saque**: inimigos deixam moedas e itens no chão conforme a tabela deles. Quem
  matou tem 15 s de exclusividade; depois qualquer um pega (passando por cima), e
  tudo some em 60 s. Item largado cai à frente e quem largou só pega de volta
  depois de 3 s.
- **Itens**: 20 espaços que empilham; 3 de equipamento (arma, armadura, acessório)
  que somam bônus e multiplicadores. Se a raça/classe mudar e o item não servir
  mais, ele volta para a bolsa. Consumíveis têm recarga e não são gastos à toa
  (poção de vida com a vida cheia).
- **Morte**: renasce no ponto inicial depois de 3 s.
- **Salvamento**: ao sair e automaticamente a cada 60 s.

## Pendências conhecidas

- Ainda não há como trocar de raça/classe por escolha (NPC, item); hoje só por regras.
- Outros jogadores são desenhados com suavização simples, não interpolação com atraso fixo.
- Login sem HTTPS: na VPS, colocar atrás de um proxy com TLS (ex.: Caddy) antes de abrir ao público.
- O hash de senha roda no loop do servidor (~0,1 s por login); ok para poucos jogadores.
- Arte provisória (formas geométricas).
