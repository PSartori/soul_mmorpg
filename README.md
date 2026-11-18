# RPG Online — protótipo

RPG online em 2D com treino por uso, combate em tempo real e PvP, inspirado nas
mecânicas dos jogos de anime do BYOND (veja `referencia-bleach-shinzui.md`).
Mundo, raças e nomes próprios ainda serão definidos; por enquanto os nomes são provisórios.

## Como rodar

Requer [uv](https://docs.astral.sh/uv/) (já instalado nesta máquina).

```bash
./run.sh
```

Abra http://127.0.0.1:8080, crie uma conta (nome e senha) ou entre na sua, e na
tela de personagens escolha um para jogar ou crie um novo (nome e raça). Cada conta
tem até 4 personagens, um no jogo por vez. Para testar multiplayer, abra outra aba
e entre com outra conta.

Testes do servidor:

```bash
UV_PROJECT_ENVIRONMENT=~/.venvs/rpg-online uv run python -m unittest -v
```

O ambiente virtual fica em `~/.venvs/rpg-online` (fora do Google Drive, para não
sincronizar milhares de arquivos). O banco de dados fica em `var/game.db`, com
backups automáticos em `var/backups/` (um ao ligar o servidor e outro a cada 6 h;
guarda os 28 mais recentes). Para restaurar, pare o servidor e copie um backup
por cima de `var/game.db`.

**O banco fica só na máquina:** a pasta `var/` não vai para o GitHub. Quem baixa o
repositório começa sem banco: ao ligar o servidor (`./run.sh`), `var/game.db` é
criado vazio. Crie uma conta e um personagem no jogo e torne-se administrador com
`./admin.sh role SeuPersonagem admin`. Para levar os dados de uma máquina para outra
(ex.: para a VPS), copie `var/game.db` (ou um backup) com o servidor parado.

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
(pontos com `tipo` = nascimento, inimigo — com `inimigo` = id do inimigo — ou
**npc** (com `npc` = id do NPC). As propriedades do mapa dão o nome e a zona padrão. Salve como JSON (`.tmj`). Detalhes
em `server/tiled.py`. Mapas em texto continuam funcionando; cada mapa usa um
formato só. `./mapas.sh verificar` confere todos.

## Administração

A equipe (moderadores, mestres do jogo e administradores) age pela janela
**Administração** (tecla G ou botão no painel, só para quem tem papel): lista de
jogadores online (com a conta de cada um, para quem pode banir) e, para o escolhido,
expulsar, silenciar, banir (com tempo, motivo e **alcance**: só o personagem, a conta
toda ou a conta e o IP), ir até/trazer, dar item ou moedas (moedas só para corrigir problemas: ficam no registro e no
relatório de economia), trocar o papel e gerar uma
**senha temporária** para a conta (permissão `account`, para quem esqueceu a senha);
mais anúncio para todos, **eventos em ondas** (iniciar e encerrar; permissão `events`), lista de banidos
(personagens, contas e IPs, com desbanir), os
**relatos de bug** abertos (quem, onde, quando, o texto e dados do navegador, com "Ir até o
local" e "Resolvido") e o registro de tudo que a equipe fez. Quem tem a permissão `log`
recebe um aviso no chat a cada relato novo.
Papéis e permissões ficam em `data/roles.yaml`; ninguém age sobre alguém da equipe
de nível igual ou maior.

O primeiro administrador (e emergências, como desbanir com o servidor parado) é
pelo terminal:

```bash
./admin.sh role SeuPersonagem admin
```

Outros: `./admin.sh staff`, `./admin.sh bans`, `./admin.sh unban Nome` (personagem e conta
com esse nome), `./admin.sh unban-ip IP`, `./admin.sh conta Personagem` (de que conta é e
os outros personagens dela), `./admin.sh senha Conta` (senha temporária) e
`./admin.sh log`, `./admin.sh bugs` (relatos abertos; `--todos` inclui os resolvidos) e
`./admin.sh bug-resolvido N`, `./admin.sh trocas [Nome] [-n N]` (últimas trocas entre jogadores, de todos ou de
um personagem). Papéis são por personagem; mudança de papel pelo terminal vale no
próximo login.

## Contas e proteções

- **Contas**: nome e senha (pelo menos 6 caracteres); até 4 personagens (`MAX_CHARACTERS`),
  um no jogo por vez. A janela **Conta** (botão no painel) troca a senha e volta para a
  lista de personagens. Esqueceu a senha: a equipe gera uma temporária (ainda não há
  recuperação por e-mail, que precisa de envio de e-mail na VPS).
- **Bancos antigos** (de antes das contas) são migrados ao abrir: cada personagem vira uma
  conta com o mesmo nome e a mesma senha.
- **Senha fora do loop**: a senha é conferida (PBKDF2, ~0,1 s) numa thread, sem parar o jogo.
- **Tentativas de senha**: 5 erros (`LOGIN_MAX_FAILS`) travam o IP e a conta por 5 min. A
  mensagem de erro é a mesma para conta inexistente e senha errada.
- **Contas novas**: até 3 por IP por hora (`ACCOUNTS_PER_IP`); o próprio computador
  (127.0.0.1) não tem limite.
- **Mensagens**: cada conexão manda até 60 por segundo, com rajada de 120 (`MSG_RATE`,
  `MSG_BURST`); o excesso é descartado e quem insiste (300 descartes) é desconectado.
- **Chat**: até 5 mensagens a cada 6 s por jogador (`CHAT_BURST`, `CHAT_WINDOW`).
- **Bans**: de personagem (ao entrar nele), de conta e de IP (ao entrar na conta ou criar uma).
- **IP real atrás do Caddy**: com `TRUST_PROXY=1`, o IP vem do cabeçalho `X-Forwarded-For`.
- **Relatos de bug**: até 1000 caracteres, um por minuto por jogador (`BUG_REPORT_*`); os
  dados do navegador são cortados a tamanhos fixos.

## Balanceamento

```bash
./balance.sh --out docs/balanceamento.md
```

Gera um relatório (Markdown) com vida, energia e dano por segundo de cada classe,
de onde vem o dano de cada uma, quanto tempo cada classe leva para matar cada
inimigo (e o contrário), a matriz de PvP e o ganho de cada equipamento — por
nível, mais uma lista de **alertas** (classe muito acima/abaixo da média, skill
que quase não rende, item que não muda nada, nível sem inimigos por perto).

A seção **Moedas por inimigo** mostra quanto o saque de um abate rende vendido na loja que paga mais (é de onde
vêm as moedas do jogo) e avisa de inimigo cujo saque nenhuma loja compra.

## Economia

Moedas só entram no jogo vendendo itens para lojas de NPC. Cada movimento é anotado por dia, tipo e detalhe
(`server/economy.py`, tabela `economy`, gravada no salvamento automático):

- **entram**: vendas para lojas (por item) e moedas dadas pela equipe;
- **saem**: compras em lojas (por item), preço de receitas (por oficina), treinadores e perdas por regras;
- **só mudam de lugar**: trocas entre jogadores e banco (guardar/sacar).

O relatório mostra quantas moedas existem (bolsas e bancos, com os valores atuais de quem está online), quanto
entrou e saiu por dia, por tipo, os itens que mais trouxeram moedas, onde mais se gastou, quem tem mais moedas e
**alertas** (moedas em circulação cresceram mais de 25% no período; a equipe deu moedas). Está na janela de
administração (seção **Economia**, permissão `log`) e no terminal:

```bash
./admin.sh economia        # últimos 7 dias
./admin.sh economia 30
```

A seção **Inimigos no próprio nível** põe cada inimigo contra cada classe no nível
dele — normal, elite e, para chefes, fase a fase — e avisa quem sai da faixa-alvo de
folga (normal 1,5–6; elite 1–3; chefe 0,7–1,5). Os invocados dos chefes não entram
na conta da luta do chefe.

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
| E | conversar com o NPC mais próximo (também dá para clicar nele) |
| J | diário de missões: em andamento (objetivos, recompensa, a quem entregar, abandonar) e concluídas |
| K | skills: as que você conhece e a barra (arraste uma skill para um espaço, ou clique nela e depois no espaço; × tira) |
| N | minimapa: pequeno → grande → escondido (o jogo lembra a escolha); clicar nele amplia ou diminui, Esc fecha o grande |
| P | seu perfil: descrição do personagem (aparência, história) e a lista de quem você ignora |
| G | janela de administração (só para a equipe) |
| O | janela da organização: entrar, sair, cargos e ocupantes, assumir vaga, disputar, promover, rebaixar, expulsar |
| Enter | chat; o seletor ao lado escolhe o canal: Geral (todos), **Local** (só quem está perto, em balão), **Ação** (narra o que você faz: "\* Ana acena \*"), Organização, Grupo ou **Privado** (com quem você escolheu) |
| V | troca entre a visão 2D e a 3D (protótipo; o jogo lembra a escolha) |
| Q / Z | na visão 3D, gira a câmera 90° para um lado ou para o outro |
| Rodinha do mouse | na visão 3D, aproxima ou afasta a câmera |
| **Botão "Relatar bug"** | no painel: descreva o problema; mapa, posição, hora e dados do navegador vão junto |
| **Clique num jogador** | menu: ver perfil, mensagem privada, ignorar (ou deixar de ignorar), desafiar para duelo, convidar para o grupo (ou, se for do seu grupo e você liderar, passar a liderança e tirar), disputar o cargo dele e, para líderes, promover, rebaixar e expulsar |
| **Clique num NPC** | conversa; as respostas são botões, e alguns abrem a loja (comprar e vender), o treinador (aprender skills), a oficina (criar itens) ou o banco (guardar itens e moedas) |
| **Propor troca** (menu do jogador) | abre a janela de troca: clique nos itens da sua bolsa para oferecer, ajuste quantidades e moedas; os dois aceitam |
| **Clique num nome no chat** | escolhe a pessoa para mensagem privada (responder) |
| **Arrastar na barra** | arrastar uma skill para outro espaço troca as duas de lugar; clicar num espaço vazio abre as skills |

Um desafio recebido aparece na tela com **Aceitar** e **Recusar**; durante o duelo o
painel mostra **Desistir**. Os antigos comandos de chat (`/duelo`, `/aceitar`,
`/recusar`, `/render`, `/grupo Nome`, `/g`, `/me ação`, `/l fala`, `/p Nome texto`, `/r texto`, `/ignorar Nome`,
`/designorar Nome`, `/org`, `/entrar`, `/sair`, `/o`, `/assumir`, `/disputar`,
`/promover`, `/rebaixar`, `/expulsar`, `/who`) continuam funcionando como atalho.

## Conteúdo (data/)

Skills, efeitos, inimigos, raças, classes e regras são **dados**, não código.
Cada arquivo tem no topo a explicação de todos os campos.

| Arquivo | O que define |
|---|---|
| `data/skills.yaml` | técnicas: tipo (melee, projectile, area, dash, self), custo, recarga, dano, efeitos, treino; descrição e quem pode usar (`races`/`classes`) |
| `data/effects.yaml` | efeitos de status: modificadores (velocidade, dano...), flags (atordoado, silenciado), dano/cura periódicos |
| `data/enemies.yaml` | tipos de inimigo: vida, velocidade, alcance de perseguição, skills que a IA usa, letra no mapa e tabela de saque (só itens: inimigos não deixam moedas) |
| `data/items.yaml` | itens: equipamento (slot, bônus, restrição de raça/classe), consumível (cura, energia, efeitos), material |
| `data/organizations.yaml` | organizações: raças aceitas, nível mínimo, cargos em ordem (vagas, numerados, líder, como se conquista) |
| `data/roles.yaml` | papéis da equipe: selo, nível na hierarquia e permissões |
| `data/npcs.yaml` | NPCs: nome, título, cor e diálogo em nós (opções com condições, ações, `goto`, `shop`, `trainer`, `craft` e `bank`) |
| `data/shops.yaml` | lojas: o que vendem (preço = `value` do item ou próprio) e o que compram (`buys`, `buy_rate`) |
| `data/trainers.yaml` | treinadores: que skills ensinam, por quantas moedas e com que requisitos (atributos, outras skills, condições) |
| `data/crafting.yaml` | oficinas: receitas (item criado, materiais, preço, requisitos, receitas secretas com `hidden`) |
| `data/quests.yaml` | missões: quem oferece e a quem entregar, condições, objetivos (kill, collect, talk, reach, flag), recompensas, repetição |
| `data/races.yaml` | raças: se aparecem na criação, classe inicial, bônus de vida/energia/velocidade |
| `data/classes.yaml` | classes: raça, skills iniciais (`start_skills`), multiplicador de treino por atributo |
| `data/rules.yaml` | regras "quando X, se Y, faça Z": morte, abates, subida de nível (veja abaixo) |
| `data/events.yaml` | eventos em ondas (invasões): mapa, área de nascimento, ponto de marcha, ondas, tempo, recompensas, agenda |
| `data/player.yaml` | raça padrão para personagens antigos |
| `data/maps/<mapa>.txt` | terreno de cada mapa em texto (legenda no topo de `inicio.txt`); todo `.txt` da pasta vira um mapa |
| `data/maps/<mapa>.yaml` | nome do mapa, zonas (padrão e áreas PvE/PvP), portais para outros mapas e onde ficam os NPCs |

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
  ativos, zona), `kill`, `level_up`, `duel_end` (vitória/derrota/empate e motivo),
  `rank_change` (entrou, saiu, promovido, rebaixado, expulso, assumiu, ganhou ou
  perdeu numa disputa), `quest_complete`, `craft` (criou um item numa oficina: `item` e
  `station`) e `event_end` (um evento em ondas terminou: `event`, `result` win/lose e `damage`; dispara para
  quem participou). Todo evento também tem `org` e `rank` de quem disparou.
- **Ações:** mensagens, perder/ganhar XP, trocar raça ou classe, marcas de
  progressão (`set_flag`), aplicar efeitos, dar itens, tirar moedas (`lose_gold`; **não existe dar moedas**), começar
  missão e ensinar uma skill (`learn_skill`).
- **Condições especiais:** `chance`, `flags` e `not_flags`.

As regras de cada evento rodam de cima para baixo; `stop: true` encerra. A lista
completa de campos e ações está no topo do arquivo. As regras que estão lá hoje
são **exemplos provisórios**: troque pelas regras reais do jogo.

Os arquivos são validados ao subir o servidor: campo com erro de digitação, tipo
errado ou referência inexistente (ex.: skill que aplica um efeito que não existe)
para o servidor com uma mensagem dizendo o arquivo, o item e o campo.

Para criar uma skill nova basta adicioná-la em `skills.yaml` e dizer como se aprende:
nas `start_skills` de uma classe, num treinador (`trainers.yaml`) ou com a ação
`learn_skill`; só é preciso mexer em Python para criar um *tipo* novo de skill
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
  pathfinding.py     caminho dos inimigos: A* na grade de tiles e teste de linha livre
  npcs.py            NPCs: conversa (nós de diálogo), lojas (compra e venda) e banco (guardar e sacar)
  trade.py           troca entre jogadores: pedido, ofertas, aceite dos dois e troca de uma vez
  economy.py         economia: anota de onde as moedas entram e para onde vão; relatório (moedas existentes)
  quests.py          missões: progresso, entrega, recompensas e marcadores ! / ?
  training.py        skills conhecidas, barra de atalhos (trava em combate) e treinadores
  crafting.py        oficinas: receitas, criação de itens (tudo ou nada) e evento craft
  parties.py         grupos: convites, liderança, chat e quem divide abates, missões e saque
  accounts.py        contas: entrar/criar, personagens da conta, troca de senha, travas e limites
  bugs.py            relatos de bug: guarda texto, personagem, mapa, posição, zona, hora e dados do navegador
  events.py          eventos em ondas: ondas, marcha, participação, vitória/derrota, recompensas e agenda
  social.py          roleplay: fala local e ações (com balão), mensagem privada, ignorar e perfil
  requirements.py    requisitos de treinadores e receitas (atributos, skills, condições) em texto
  inventory.py       inventário: espaços que empilham, equipamento e moedas
  orgs.py            organizações, cargos e disputas (vínculos na tabela memberships)
  admin.py           equipe, sanções (ban/silêncio) e registro de ações
  gamedata.py        carrega conteúdo + mapas (ao ligar e ao recarregar)
  tiled.py           leitor de mapas do Tiled (.tmj)
  saves.py           versão do formato de save e migrações (sem wipes!)
  entities.py        Player, Enemy, Projectile, efeitos ativos e atributos derivados
  stats.py           atributos treináveis (nível + XP)
  tilemap.py         mapas ASCII, zonas, portais e colisão de caixas com tiles
  db.py              SQLite: contas (senhas com PBKDF2), personagens, equipe, sanções; migra bancos antigos
  config.py          números de regra do motor (o resto está em data/)
client/              JavaScript puro, sem build
  index.html, style.css
  js/main.js         estado, previsão de movimento, HUD, barra de skills, chat
  js/physics.js      cópia da colisão/movimento do servidor (previsão)
  js/render.js       desenho no canvas (arte provisória com formas)
  js/render3d.js     visão 3D (protótipo): mundo 3D com personagens 2D virados para a câmera
  js/inventory.js    janela de inventário
  js/admin.js        janela de administração
  js/social.js       menu do jogador, aviso de desafio e janela da organização
  js/npc.js          conversa com NPCs e janela da loja
  js/bank.js         janela do banco (baú da conta, bolsa e moedas)
  js/trade.js        aviso de pedido de troca e janela da troca
  js/quests.js       diário de missões, acompanhamento no painel e oferta na conversa
  js/skills.js       janela de skills (K), arrastar para a barra e janela do treinador
  js/crafting.js     janela da oficina (receitas, materiais que tem, criar uma ou várias vezes)
  js/party.js        painel do grupo, aviso de convite e opções de grupo no menu do jogador
  js/account.js      telas de conta e de personagens, janela Conta (trocar senha, trocar de personagem)
  js/bugs.js         janela de relatar bug (e os últimos erros de JavaScript da página, que vão junto)
  js/events.js       painel dos eventos em ondas (onda, inimigos, tempo, sua participação)
  js/roleplay.js     balões de fala, janela de perfil (P) e opções de roleplay no menu do jogador
  js/minimap.js      minimapa (N): terreno, portais, PvP, NPCs com marcas de missão, jogadores, grupo e inimigos
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

A previsão também encosta nos outros corpos (jogadores, inimigos e NPCs), usando as
posições do último snapshot. Com NPCs, que ficam parados, bate sempre; perto de quem se
move pode haver uma pequena correção, suavizada no desenho.

**Se mudar `move_box`/`clamp_by_solids` em `tilemap.py` ou `_move_player` em `world.py`,
mude `physics.js` também** — o teste de paridade (com e sem corpos) acusa se esquecer.

Para depurar no console do navegador: `__game.state`, `__game.pred`, `__game.pending`.

### Protocolo (JSON pelo WebSocket, campo `t` = tipo)

HTTP: `GET /api/races` lista as raças da tela de criação.

Antes de jogar: `account {op: login|register, name, password}`, `char {op: create, name, race}`,
`char {op: enter, name}`, `account {op: logout}`; no jogo, `account {op: password, old, new}` e
`char {op: leave}`. O servidor responde `account {name, chars, max, races}` (lista de personagens;
name null = saiu da conta), `account_result {ok, text}` (troca de senha) ou `error {msg}`.

Cliente → servidor (no jogo): `move {seq, up, down, left, right}`,
`skill {id, f}`, `duel {op: challenge|accept|decline|forfeit, target}`,
`org {op: info|join|leave|claim|dispute|promote|demote|expel, ...}`,
`npc {op: talk|choose|buy|sell|learn|craft|deposit|withdraw|deposit_gold|withdraw_gold|close, id|i|item|slot|skill|amount, qty|times}`,
`trade {op: invite|accept|decline|set|gold|confirm|cancel, target|item|qty|amount|v}`, `quest {op: abandon, id}`,
`skills {op: bar, slot, id}` (id null = esvazia), `party {op: invite|accept|decline|leave|kick|lead, target}`,
`chat {text, ch: party}`, `bug {text, client: {view, agent, screen, errors}}`, `chat {text, ch: local|emote|org|party}`,
`chat {text, ch: pm, to}`, `social {op: profile|set_profile|ignore|unignore, name|text}`, `admin {op, ...}` (equipe; eventos: `event_start`/`event_stop {id}`), `item {op: use|drop, slot: n}`, `item {op: unequip, slot: weapon|armor|accessory}`,
`meditate`, `rest`, `chat {text}`.

Servidor → cliente: `welcome {id, map, skills, effects, enemies, stat_labels, ...}`,
`map {map, x, y}` (atravessou um portal; o mapa traz `npcs: [{id, t, x, y}]` para o minimapa), `org_data` (janela da organização),
`profile {name, race, cls, lv, o, st, text, me, ignored, can_clear}` (perfil), `admin_economy` (relatório de economia), `dialog {npc, name, title, text, options}` / `shop {npc, shop, sells, buys}` /
`trainer {npc, trainer, teaches: [{id, price, known, missing}]}` /
`craft {npc, station, recipes: [{i, make, qty, needs, price, missing}]}` / `bank {npc, size, slots, gold}` / `npc_close` (NPCs), `announce {text, by}`, `kick {text}` (antes de
desconectar), `admin_data`/`admin_result` (equipe), `error {msg}`, `snap {ents, projs, drops, fx, you}`
(20×/s, só o que está perto; `you.skills` é a barra, com null nos espaços vazios; `you.inv`, `you.q` —
diário e marcadores das missões —, `you.known` — skills conhecidas, com `ok` se a raça/classe atual
pode usar — e `you.party` — membros do grupo com vida, nível e mapa; null = sem grupo — só vêm quando
mudam; `you.pinv` é o convite de grupo esperando resposta; `you.tinv` é o pedido de troca esperando resposta;
`you.trade` — `{with, mine, theirs, v, ok_me, ok_them}`, null = sem troca — só vem quando muda; `you.ev` —
eventos em ondas em andamento `[{id, name, map, here, wave, waves, left, time, pause, dmg, min}]` — só quando muda; `you.mates` — `[id, x, y]` dos membros do grupo no
mesmo mapa, mesmo longe, para o minimapa; `you.ign` — lista de ignorados — também só vem quando
muda), `chat {from, text, ch?, id?, to?}` (`ch`: nada = Geral, `local`/`emote` com o `id` de quem falou para o balão,
`pm` com `to` no eco para quem mandou, ou o nome do canal da organização/grupo), `sys {text}`.

### Regras atuais

- **Atributos**: Força, Espírito (energia), Agilidade (velocidade), Vitalidade (vida).
  Cada um sobe com o uso: andar → Agilidade, meditar → Espírito, levar dano →
  Vitalidade, e cada skill treina o que estiver no campo `train`. Matar dá bônus
  ao atributo principal da skill que deu o golpe final.
- **Efeitos**: reaplicar renova a duração (não acumula); efeitos diferentes se
  multiplicam; morrer limpa todos.
- **Inimigos** andam reto até o alvo quando nada está no meio; senão seguem um caminho
  calculado com A* na grade de tiles (`server/pathfinding.py`), contornando paredes,
  água, árvores e a zona segura, saindo de salas e atravessando labirintos. O caminho
  é recalculado no máximo a cada 0,5 s ou quando o alvo muda de tile, com limite de
  busca (`PATH_*` em `config.py`). Sem caminho possível (ex.: alvo do outro lado da
  água), o inimigo desiste daquele jogador por 3 s, em vez de ficar empurrando a
  parede. Quem ataca de longe continua andando até ter linha de tiro. O desvio pelo
  lado continua resolvendo corpos no caminho e quinas. Também voltam para casa pelo
  caminho depois de uma perseguição.
- **Colisão entre corpos**: jogadores, inimigos e NPCs não entram uns nos outros (caixas
  de 20 px); quem anda encosta e desliza, como nas paredes, e o dash para no primeiro
  corpo. Mortos não bloqueiam. Corpos já sobrepostos (ex.: dois jogadores nascendo no
  mesmo ponto) podem se separar. Cada par liga/desliga em `config.py`
  (`PLAYERS_BLOCK_PLAYERS`, `PLAYERS_BLOCK_ENEMIES`, `ENEMIES_BLOCK_ENEMIES`, `NPCS_BLOCK`).
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
- **Moedas**: **só entram no jogo quando alguém vende um item para uma loja de NPC.** Inimigos não deixam moedas,
  e missões, regras e eventos não dão moedas (dão itens, que podem ser vendidos). Entre jogadores, as moedas
  circulam pela troca. Saem do jogo em compras nas lojas, receitas de oficina, treinadores e perdas por regras.
  O validador recusa `gold` em inimigos e a ação `give_gold`. Veja **Economia** acima.
- **Saque**: inimigos deixam itens no chão conforme a tabela deles. Quem
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
- **NPCs**: ficam parados, não lutam e não podem ser feridos. Conversa começa a até
  80 px (`NPC_TALK_RANGE`) e fecha ao se afastar mais de 128 px, morrer, mudar de mapa
  ou recarregar o conteúdo. As opções do diálogo aparecem conforme as condições
  (`if`: raça, classe, organização, cargo, moedas, nível total, marcas) e rodam as
  mesmas ações das regras (`do`) — dá para dar itens, marcar progressão e até trocar
  raça/classe por um NPC.
- **Skills e barra**: cada personagem conhece um conjunto de skills — as iniciais da classe
  (`start_skills`), as compradas de treinadores e as dadas pela ação `learn_skill` — e escolhe
  até 8 para a barra (`BAR_SIZE`). Conhecer não se perde: se a raça/classe mudar e uma skill
  for restrita (`races`/`classes` em `skills.yaml`), ela fica bloqueada (cinza) e sai da
  barra, e volta a funcionar se o personagem voltar a poder usá-la. Ao entrar numa classe,
  aprende as iniciais dela, que vão para os espaços vazios; o que o jogador tirou da barra de
  propósito não volta sozinho. A barra **trava em combate**: 5 s depois de atacar ou levar
  dano (`BAR_COMBAT_LOCK`) e durante duelos; com `BAR_SAFE_ZONE_ONLY` só muda na zona segura.
- **Treinadores**: NPCs com a opção `trainer: id` abrem a janela do treinador, que mostra
  todas as skills dele, o preço e o que falta para cada uma (nível de atributo, outra skill,
  raça/classe, condições como nível total ou missão concluída). Aprender cobra as moedas e
  põe a skill no primeiro espaço vazio da barra.
- **Criação de itens**: NPCs com a opção `craft: id` abrem uma oficina (`data/crafting.yaml`),
  com receitas que gastam materiais (qualquer item) e moedas para criar um item. A janela
  mostra o que você tem de cada material (ex.: "Couro grosso 9/4"), o preço e o que falta
  (nível de atributo, skill, condições); dá para criar várias vezes de uma vez (até 99,
  `CRAFT_MAX_TIMES`): ou cabe tudo e cria tudo, ou não cria nada. Os materiais saem antes de
  conferir o espaço, então gastar uma pilha inteira libera lugar para o resultado. Receitas
  `hidden` só aparecem quando as condições do `if` valem (receita secreta, liberada por
  missão ou marca). Cada criação dispara o evento `craft` das regras. O servidor recusa
  receitas que dão lucro sem fim (materiais todos à venda numa loja, resultado vendido de
  volta por mais).
- **Grupos**: até 5 jogadores (`PARTY_MAX`) com um líder. Convite pelo menu do jogador
  (aviso com Aceitar/Recusar, expira em 30 s); quem não tem grupo cria um ao convidar. O líder
  tira membros e passa a liderança; quem sai do jogo sai do grupo; se o líder sair, o próximo
  assume; com um membro só, o grupo acaba. Grupos não vão para o save. Painel no lado com a
  vida e o nível de cada um, canal "Grupo" no chat e nomes dos membros em verde. Efeitos, com
  os membros vivos, no mesmo mapa e a até 15 tiles (`PARTY_SHARE_RANGE`):
  - **abates**: quem dá o golpe final ganha o bônus de abate inteiro, como sempre; cada membro
    perto que lutou nos últimos 5 s (atacou ou levou dano) ganha 50% dele (`PARTY_XP_SHARE`),
    no atributo da última skill que usou — quem só fica parado perto não ganha;
  - **missões**: o abate conta para os objetivos "derrotar" de todos os membros perto;
  - **saque**: a exclusividade de 15 s vale para o grupo todo (quem pegar primeiro fica com o item);
  - **PvP**: membros do mesmo grupo não se ferem em áreas PvP (`PARTY_FRIENDLY_FIRE`); duelo
    entre eles continua valendo.
- **Roleplay** (`server/social.py`):
  - **Local**: quem está no mesmo mapa e a até 12 tiles (`LOCAL_CHAT_RANGE`) vê no chat e num balão acima
    do personagem (some em 4–10 s, conforme o tamanho). **Ação** (`/me`) tem o mesmo alcance e balão, em itálico.
  - **Privado**: só os dois veem; quem recebeu responde clicando no nome ou com `/r`.
  - **Ignorar**: quem você ignora não te manda nada (Geral, Local, Ação, Organização, Grupo, Privado),
    nem desafios e convites de grupo (ignorar também apaga os que estão na tela). Avisos do sistema e
    anúncios sempre chegam. Não dá para ignorar a equipe. Até 100 nomes (`IGNORE_MAX`), também de quem está offline.
  - **Perfil**: descrição livre de até 600 caracteres (`PROFILE_MAX_LEN`), vista por quem clica em "Ver
    perfil" (só de quem está online). Silenciado não muda o perfil; a equipe com `mute` pode apagar
    ("Apagar descrição" no perfil; fica no registro).
  - Comandos digitados começam com `/` em qualquer canal.
- **Eventos em ondas** (`server/events.py`, `data/events.yaml`): os inimigos de cada onda nascem em tiles livres da
  área `spawn` e marcham até `march` (a "casa" deles), atacando quem encontram; enxergam jogadores mais longe
  (`EVENT_AGGRO_MULT`), não desistem por distância e não renascem. Quando todos de uma onda caem, a próxima vem
  `EVENT_WAVE_PAUSE` (8 s) depois. Vencer a última dentro de `time` é vitória: cada participante online (dano aos
  invasores ≥ `min_damage`) recebe `rewards`; acabar o tempo é derrota e os invasores somem. Nos dois casos o
  evento `event_end` das regras dispara para os participantes. Começa pela equipe (janela de administração,
  permissão `events`, fica no registro) ou sozinho a cada `every` minutos com `min_players` online; um evento por
  mapa. A equipe pode encerrar (sem recompensa); recarregar o conteúdo encerra os que estão em andamento. Os
  invasores aparecem com ⚔ no nome e em laranja no minimapa; o painel no lado mostra onda, inimigos, tempo e
  a sua participação para todos os jogadores ("Vá até ... para ajudar").
- **Banco** (opção `bank: true` num NPC): um por **conta**, dividido pelos personagens dela (`BANK_SIZE` = 40
  espaços, mais moedas). Abre no banqueiro Matias (Campos de Início) e nos NPCs dos acampamentos (Lia nas Ruínas,
  Dorian na Fenda): é o mesmo baú em qualquer um. Clique move a pilha inteira, Shift + clique só 1; move o que
  couber. Itens equipados ficam com o personagem. Cada depósito ou saque salva o personagem e o banco juntos
  (uma transação), na hora: se o servidor cair, o item não fica nos dois lugares nem some.
- **Troca entre jogadores** (`server/trade.py`): "Propor troca" no menu do jogador ou `/troca Nome`, a até 4
  tiles (`TRADE_RANGE`); o outro responde no aviso (expira em 30 s). Cada um põe itens da bolsa (até 8
  diferentes, `TRADE_MAX_ITEMS`) e moedas. Os dois aceitam; **qualquer mudança desfaz os dois aceites** e o
  aceite vale só para a versão que o jogador viu. Na hora de trocar, o servidor confere de novo (ainda tem os
  itens? cabe tudo nas duas bolsas?) e faz tudo de uma vez ou nada. Os dois personagens e o registro da troca
  (tabela `trades`, `./admin.sh trocas`) são salvos juntos, na hora. Se alguém usar, largar ou vender algo da
  oferta, ela diminui sozinha. A troca é cancelada se alguém se afastar mais de 10 tiles, sair, for derrotado,
  mudar de mapa ou entrar em duelo. Quem ignora alguém não recebe pedidos de troca dessa pessoa.
- **Lojas**: estoque infinito; o jogador compra pelo `value` do item (ou pelo `price`
  da loja) e vende por uma fração dele (`buy_rate`, padrão 40%, no mínimo 1 moeda).
  Compra só sai se houver moedas e espaço para tudo. O servidor recusa conteúdo em
  que uma loja compra um item por mais do que outra o vende. Vender para uma loja é a **única** forma de moedas
  entrarem no jogo.
- **Missões**: o NPC que dá a missão mostra **!** e a oferece na conversa ("Missão: …",
  com objetivos e recompensa); quando os objetivos se cumprem, o NPC de entrega mostra
  **?** e a opção "Entregar: …". Abates e conversas são contados quando acontecem; lugar
  (`reach`) é conferido a cada tick; itens (`collect`) e marcas (`flag`) são medidos na
  hora — vender os itens desfaz o "pronta". Itens de `collect` são entregues ao
  concluir. Sem NPC de entrega, a missão conclui sozinha. Até 10 em andamento
  (`MAX_ACTIVE_QUESTS`); repetíveis esperam o `cooldown` (horário real). Concluir
  dispara o evento `quest_complete` das regras. Progresso fica no save (v5).
- **Mapas**, ligados por portais, em ordem de perigo: `inicio` (Campos de Início,
  níveis 1–5, onde nascem personagens novos), `bosque` (Bosque Sombrio, 5–12),
  `ruinas` (Ruínas Esquecidas, 15–30, com o chefe Guardião das Ruínas) e `fenda`
  (Fenda do Vazio, 35–50, com o chefe Devorador Ancião). Ruínas e Fenda têm um
  acampamento seguro com NPC, loja e uma missão para o chefe. Cada mapa tem seus
  inimigos, saque e projéteis; o mapa onde o personagem está é salvo. Duelo termina
  se um dos dois mudar de mapa.
- **Nível**: o do personagem é a média dos níveis dos atributos (aparece no painel);
  o de cada inimigo vem de `enemies.yaml` e aparece no nome, na cor da dificuldade
  para você (cinza fácil → vermelho perigoso).
- **Elites**: cada inimigo com `elite_chance` sorteia, a cada nascimento, se vem
  elite (★ e anel dourado): vida ×2, dano ×1,25, corpo maior, XP ×3 e o
  saque sorteado duas vezes (`ELITE_*` em `config.py`).
- **Chefes** (`boss: true`): corpo grande, barra de vida no topo da tela quando
  estão perto ou feridos, e **fases** por limite de vida (trocam skills, ficam mais
  fortes ou rápidos, aplicam efeitos como fúria e invocam ajudantes, que não
  renascem). Avisam quem está perto a cada fase e o servidor anuncia quem os
  derrotou. Se a luta se afastar demais de casa, ou ficarem 5 s sem ninguém para
  lutar (ex.: quem lutava morreu), voltam inteiros, na primeira fase e sem invocados.
- **Corpos grandes** (chefes, elites): golpes e áreas partem da borda do corpo, não
  do centro, para alcançarem quem está encostado.
- **Minimapa** (`client/js/minimap.js`): o mapa inteiro no canto de cima, à esquerda. Terreno, portais, áreas
  PvP e todos os NPCs (com **!** e **?** das missões) vêm com o mapa; jogadores e inimigos, do snapshot (só os
  que estão por perto); os membros do grupo no mesmo mapa vêm em `you.mates`, mesmo longe. O grande mostra
  os nomes dos NPCs e dos destinos dos portais. Mostra o mapa todo, sem névoa de exploração.
- **Visão**: cada jogador só recebe o que está no mesmo mapa e a até 900 px dele
  (`VIEW_RADIUS`), o que mantém o tráfego proporcional ao que está por perto, e não
  ao total de jogadores.
- **Morte**: renasce no ponto de nascimento (`S`) do mapa onde está, depois de 3 s.
- **Salvamento**: ao sair e automaticamente a cada 60 s. Save v6 guarda skills conhecidas e
  barra; personagens de antes continuam com as skills que a classe dava e a mesma barra. Save v7 guarda
  o perfil e a lista de ignorados (começam vazios).

## Visão 3D (protótipo)

Com **V** (ou o botão no painel) o jogo troca o desenho 2D por uma visão 3D: o chão é
uma textura feita dos tiles do mapa, paredes e árvores ganham volume e sombra, e
jogadores, inimigos, NPCs e itens são imagens 2D sempre viradas para a câmera. Nomes,
barras de vida, marcas de missão e números são desenhados no canvas 2D por cima.

- **Só o desenho muda.** O servidor continua num plano: física, colisão, alcance,
  previsão de movimento, mapas e Tiled são os mesmos nas duas visões.
- **Câmera inclinada** que segue o personagem, com zoom (rodinha) e giro de 90° (Q/Z).
  Com a câmera girada, o cliente converte as teclas: W anda sempre "para longe da câmera"
  (`rotateKeys` em `render3d.js`). O giro é em passos de 90° porque o servidor só recebe
  cima/baixo/esquerda/direita.
- **Clique** em jogadores e NPCs compara o mouse com a imagem de cada um na tela.
- A biblioteca 3D ([three.js](https://threejs.org), versão 0.160.0 fixa) vem da CDN
  jsDelivr pelo *import map* do `index.html`, e só é baixada quando a visão 3D é ligada. Se
  não carregar (sem internet ou sem WebGL), o jogo avisa no chat e continua em 2D.
- As imagens dos personagens são geradas com formas (de frente, de costas e de lado,
  conforme para onde olham em relação à câmera), até termos sprites de verdade.

## Pendências conhecidas

- Skills não sobem de nível com o uso (só os atributos); não há como esquecer uma skill.
- Trocar de raça/classe por escolha já é possível por NPC (ação `set_race`/`set_class`
  num diálogo), mas o conteúdo atual não tem nenhum NPC assim.
- Lojas com estoque infinito e preço fixo.
- Banco sem abas nem ordenação, e sem expansão de espaços (fica em `BANK_SIZE`).
- Troca sem mercado/leilão: só entre dois jogadores perto um do outro.
- Eventos sem agenda por horário do dia (só "a cada N minutos") e sem objetivos além de derrotar as ondas
  (ex.: proteger um NPC ou uma estrutura); a recompensa é igual para todos os participantes.
- Missões não exigem grupo (o grupo só divide os abates); sem missões com tempo limite ou que falham.
- Saque de itens do grupo é de quem pegar primeiro (sem rodízio nem sorteio).
- Outros jogadores são desenhados com suavização simples, não interpolação com atraso fixo.
- Com colisão entre jogadores, alguém pode bloquear passagens estreitas (ex.: a saída de
  2 tiles da cidade). Se incomodar: desligar `PLAYERS_BLOCK_PLAYERS` ou só na zona segura.
- A colisão confere todos os corpos do mapa perto de cada um (sem grade espacial); ok
  para dezenas de entidades por mapa.
- Login sem HTTPS: na VPS, colocar atrás de um proxy com TLS (ex.: Caddy) antes de abrir ao público.
- Recuperação de senha só pela equipe (senha temporária); sem e-mail ainda.
- Não dá para apagar personagem nem renomear conta pelo jogo.
- Perfil só de quem está online; sem campos separados (idade, aparência...), só um texto livre.
- Sem balão para os canais Geral, Organização e Grupo (só Local e Ação).
- Minimapa sem névoa de exploração (mostra o mapa todo desde a chegada) e sem clicar para andar até um ponto.
- Arte provisória (formas geométricas).
