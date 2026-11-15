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

## Editando conteúdo com o jogo rodando

- **Botão "Recarregar conteúdo"** na janela de administração (permissão `reload`):
  lê de novo todos os YAML e mapas. Se algo estiver errado, **nada muda** e a
  janela mostra a mensagem do validador. Se estiver certo, quem está online recebe
  na hora as skills, itens, classes e mapas novos, sem cair. Inimigos renascem com
  os valores novos; o que deixou de existir (item, classe, cargo, mapa) é ajustado
  e listado.
- **Modo automático, para desenvolvimento:** `./run.sh --watch` recarrega sozinho
  sempre que um arquivo de `data/` é salvo (erros vão para o log e para a equipe
  online).

## Mapas no Tiled

Os mapas podem ser editados no [Tiled](https://www.mapeditor.org) (gratuito). Para
converter um mapa em texto:

```bash
./mapas.sh para-tiled inicio
```

Isso cria `data/maps/inicio.tmj` (abre no Tiled), o conjunto de tiles
`data/maps/terreno.tsj` + `terreno.png`, e move o `.txt`/`.yaml` antigos para
`data/maps/antigos/`. No Tiled, o mapa tem a camada de tiles **terreno** e as
camadas de objetos **zonas** (retângulos com propriedade `tipo` = pve/pvp),
**portais** (retângulos com `destino`, `chegada_x`, `chegada_y`) e **pontos**
(pontos com `tipo` = nascimento ou inimigo, e `inimigo` = id do inimigo). As
propriedades do mapa dão o nome e a zona padrão. Salve como JSON (`.tmj`). Detalhes
em `server/tiled.py`. Mapas em texto continuam funcionando; cada mapa usa um
formato só. `./mapas.sh verificar` confere todos.

## Administração

A equipe (moderadores, mestres do jogo e administradores) age pela janela
**Administração** (tecla G ou botão no painel, só para quem tem papel): lista de
jogadores online e, para o escolhido, expulsar, silenciar, banir (com tempo e
motivo), ir até/trazer, dar item ou moedas e trocar o papel; mais anúncio para
todos, lista de banidos (com desbanir) e o registro de tudo que a equipe fez.
Papéis e permissões ficam em `data/roles.yaml`; ninguém age sobre alguém da equipe
de nível igual ou maior.

O primeiro administrador (e emergências, como desbanir com o servidor parado) é
pelo terminal:

```bash
./admin.sh role SeuPersonagem admin
```

Outros: `./admin.sh staff`, `./admin.sh bans`, `./admin.sh unban Nome`,
`./admin.sh log`. Mudança de papel pelo terminal vale no próximo login.

## Balanceamento

```bash
./balance.sh --out docs/balanceamento.md
```

Gera um relatório (Markdown) com vida, energia e dano por segundo de cada classe,
de onde vem o dano de cada uma, quanto tempo cada classe leva para matar cada
inimigo (e o contrário), a matriz de PvP e o ganho de cada equipamento — por
nível, mais uma lista de **alertas** (classe muito acima/abaixo da média, skill
que quase não rende, inimigo fácil ou difícil demais, item que não muda nada).

As lutas rodam no próprio motor do jogo, então os números acompanham qualquer
mudança nos YAML. Opções: `--levels 1 20 50`, `--seconds 60`,
`--equip espada_curta,gibao_de_couro` (todos lutam equipados). Limitação: os
lutadores ficam parados, então alcance e esquiva não contam.

## Controles

| Tecla | Ação |
|---|---|
| WASD / setas | andar |
| 1–8 (Espaço = 1, F = 2) | skills da barra |
| M | meditar (treina Espírito, gera cansaço) |
| R | descansar (recupera cansaço e vida) |
| I | inventário (clique: usar/equipar · Shift + clique: largar · clique no equipado: tirar) |
| G | janela de administração (só para a equipe) |
| Enter | chat (`/who` lista quem está online) |
| `/duelo Nome` | desafia alguém por perto para um duelo |
| `/aceitar`, `/recusar` | responde a um desafio |
| `/render` | desiste do duelo |
| `/org [id]` | mostra sua organização (cargos e ocupantes) ou lista as organizações |
| `/entrar id`, `/sair` | entra / sai de uma organização |
| `/o texto` | fala só com os membros online da sua organização |
| `/assumir cargo` | assume um cargo de disputa que esteja vago |
| `/disputar Nome` | desafia o ocupante de um cargo acima do seu para um duelo valendo o cargo |
| `/promover Nome cargo`, `/rebaixar Nome`, `/expulsar Nome` | comandos de líder, para quem está abaixo |

## Conteúdo (data/)

Skills, efeitos, inimigos, raças, classes e regras são **dados**, não código.
Cada arquivo tem no topo a explicação de todos os campos.

| Arquivo | O que define |
|---|---|
| `data/skills.yaml` | técnicas: tipo (melee, projectile, area, dash, self), custo, recarga, dano, efeitos, treino |
| `data/effects.yaml` | efeitos de status: modificadores (velocidade, dano...), flags (atordoado, silenciado), dano/cura periódicos |
| `data/enemies.yaml` | tipos de inimigo: vida, velocidade, alcance de perseguição, skills que a IA usa, letra no mapa, moedas e tabela de saque |
| `data/items.yaml` | itens: equipamento (slot, bônus, restrição de raça/classe), consumível (cura, energia, efeitos), material |
| `data/organizations.yaml` | organizações: raças aceitas, nível mínimo, cargos em ordem (vagas, numerados, líder, como se conquista) |
| `data/roles.yaml` | papéis da equipe: selo, nível na hierarquia e permissões |
| `data/races.yaml` | raças: se aparecem na criação, classe inicial, bônus de vida/energia/velocidade |
| `data/classes.yaml` | classes: raça, skills da barra, multiplicador de treino por atributo |
| `data/rules.yaml` | regras "quando X, se Y, faça Z": morte, abates, subida de nível (veja abaixo) |
| `data/player.yaml` | raça padrão para personagens antigos |
| `data/maps/<mapa>.txt` | terreno de cada mapa em texto (legenda no topo de `inicio.txt`); todo `.txt` da pasta vira um mapa |
| `data/maps/<mapa>.yaml` | nome do mapa, zonas (padrão e áreas PvE/PvP) e portais para outros mapas |

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
  ativos, zona), `kill`, `level_up`, `duel_end` (vitória/derrota/empate e motivo)
  e `rank_change` (entrou, saiu, promovido, rebaixado, expulso, assumiu, ganhou ou
  perdeu numa disputa). Todo evento também tem `org` e `rank` de quem disparou.
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
  orgs.py            organizações, cargos e disputas (vínculos na tabela memberships)
  admin.py           equipe, sanções (ban/silêncio) e registro de ações
  gamedata.py        carrega conteúdo + mapas (ao ligar e ao recarregar)
  tiled.py           leitor de mapas do Tiled (.tmj)
  saves.py           versão do formato de save e migrações (sem wipes!)
  entities.py        Player, Enemy, Projectile, efeitos ativos e atributos derivados
  stats.py           atributos treináveis (nível + XP)
  tilemap.py         mapas ASCII, zonas, portais e colisão de caixas com tiles
  db.py              SQLite: personagens e senhas (PBKDF2)
  config.py          números de regra do motor (o resto está em data/)
client/              JavaScript puro, sem build
  index.html, style.css
  js/main.js         estado, previsão de movimento, HUD, barra de skills, chat
  js/physics.js      cópia da colisão/movimento do servidor (previsão)
  js/render.js       desenho no canvas (arte provisória com formas)
  js/inventory.js    janela de inventário
  js/admin.js        janela de administração
  js/input.js        teclado
  js/net.js          WebSocket
data/                conteúdo do jogo (veja acima)
tools/               ferramentas de desenvolvimento
  simulator.py       lutas simuladas no motor do jogo
  balance.py         relatório de balanceamento (./balance.sh)
  admin.py           administração pelo terminal (./admin.sh)
  maps.py            ferramentas de mapa: converter para o Tiled, verificar (./mapas.sh)
tests/               testes do servidor e das ferramentas (unittest)
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
`skill {id, f}`, `admin {op, ...}` (equipe), `item {op: use|drop, slot: n}`, `item {op: unequip, slot: weapon|armor|accessory}`,
`meditate`, `rest`, `chat {text}`.

Servidor → cliente: `welcome {id, map, skills, effects, enemies, stat_labels, ...}`,
`map {map, x, y}` (atravessou um portal), `announce {text, by}`, `kick {text}` (antes de
desconectar), `admin_data`/`admin_result` (equipe), `error {msg}`, `snap {ents, projs, drops, fx, you}`
(20×/s, só o que está perto; `you.inv` só vem quando o inventário muda), `chat {from, text}`, `sys {text}`.

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
- **Organizações**: uma por personagem. Cargos podem ser ilimitados, ter vagas ou
  vagas numeradas, e são conquistados de três jeitos: inicial (quem entra),
  nomeação (um líder promove) ou disputa (vago: `/assumir`; ocupado: vencer o
  ocupante num duelo valendo o cargo — quem vence fica com o cargo e o outro com o
  do vencedor; quem perde espera 5 min para disputar de novo). Os vínculos ficam no
  banco e valem com o ocupante offline. Se a raça mudar para uma que a organização
  não aceita, o personagem sai.
- **Mapas**: `inicio` (Campos de Início, onde nascem personagens novos) e `bosque`
  (Bosque Sombrio, mais perigoso, com Devoradores), ligados por portais. Cada mapa
  tem seus inimigos, saque e projéteis; o mapa onde o personagem está é salvo.
  Duelo termina se um dos dois mudar de mapa.
- **Visão**: cada jogador só recebe o que está no mesmo mapa e a até 900 px dele
  (`VIEW_RADIUS`), o que mantém o tráfego proporcional ao que está por perto, e não
  ao total de jogadores.
- **Morte**: renasce no ponto de nascimento (`S`) do mapa onde está, depois de 3 s.
- **Salvamento**: ao sair e automaticamente a cada 60 s.

## Pendências conhecidas

- Ainda não há como trocar de raça/classe por escolha (NPC, item); hoje só por regras.
- Outros jogadores são desenhados com suavização simples, não interpolação com atraso fixo.
- Login sem HTTPS: na VPS, colocar atrás de um proxy com TLS (ex.: Caddy) antes de abrir ao público.
- O hash de senha roda no loop do servidor (~0,1 s por login); ok para poucos jogadores.
- Arte provisória (formas geométricas).
