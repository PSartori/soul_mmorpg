# Contexto geral do projeto

Resumo de tudo o que foi conversado e construído até 18/11/2026 (atualizado com as ferramentas de roleplay): de onde o projeto
veio, como trabalhamos, o que foi decidido, o que existe hoje e o que vem a seguir.
Para detalhes técnicos, veja o `README.md`; para a visão do jogo e as decisões em
aberto, o `docs/design.md`; para números de balanceamento, o `docs/balanceamento.md`.

---

## 1. De onde veio

O ponto de partida foi o **[Bleach] Shinzui**, um RPG online de fãs que rodava no
BYOND (2007–2023, hoje fora do ar). O levantamento do que se sabe sobre ele está em
`referencia-bleach-shinzui.md` — raças e classes, treino por repetição (andar,
meditar, lutar), liberações, organizações com cargos ocupados por jogadores,
invasões em ondas, PvP e uma comunidade forte de roleplay.

**A decisão central:** fazer um jogo **original inspirado nas mecânicas** desse
estilo, com **motor próprio** — não uma recriação do Shinzui nem um jogo com o
universo de Bleach. Nomes, personagens, termos e visual da obra não são usados; o
mundo, as raças e os termos do jogo são próprios. Os nomes atuais (Humano,
Desperto, Espectral, Vigília, Coro Sombrio, Espectro, Devorador...) são
**provisórios**; a nomenclatura definitiva fica para o fim.

---

## 2. Como trabalhamos

- **Claude escreve o código e o dono do projeto revisa.** O dono programa em Python,
  por isso toda a regra do jogo fica no servidor em Python, com comentários em
  português.
- **Commits e envio ao GitHub** são feitos pelo Claude quando pedido, **sempre com o
  dono do projeto como autor** e sem menção ao Claude. O envio usa uma *deploy key*
  que só dá acesso a este repositório (`PSartori/soul_mmorpg`).
- **Interface, não comandos.** Nada que o jogador faça deve depender de digitar
  comandos no chat; toda funcionalidade nova já nasce com menus, botões e janelas.
  Comandos podem existir só como atalho.
- **Conteúdo é dado, não código.** Skills, efeitos, itens, inimigos, raças, classes,
  regras, organizações, papéis da equipe, NPCs, lojas, treinadores, oficinas, missões e mapas ficam em `data/`, validados ao
  carregar (um erro de digitação aponta arquivo, item e campo).
- **Cada etapa termina testada**: testes automáticos (hoje 534) e verificação no
  navegador, e só então commit.

---

## 3. A visão do jogo (decisões do dono)

| Tema | Decisão |
|---|---|
| Foco | **PvE e roleplay primeiro**, com elementos de PvP |
| Raça e classe | Escolhidas na criação; **podem mudar depois** (por eventos e, no futuro, por escolha) |
| Morte | **Penalidade depende de como morreu**; certas mortes **mudam a classe/raça** |
| Progressão | **Por repetição** (treino por uso); o teto ainda não foi definido |
| Hierarquia | Cargos ocupados por jogadores, **conquistados por disputa** |
| Itens | Saque de inimigos e balanceamento de classes importam |
| Moedas | **Só entram vendendo itens para lojas de NPC**; entre jogadores, circulam por troca. Inimigos, missões e eventos não dão moedas |
| Wipes | **Nunca** — o mundo é permanente |
| Mundo | Mundo aberto ou mapas com portais: **em aberto** (o motor atende aos dois) |

---

## 4. O que já existe

### Histórico

| Commit | Etapa | O que trouxe |
|---|---|---|
| `1e756af` | Protótipo | Servidor autoritativo em Python, cliente no navegador, mapa, combate, inimigos, meditação, treino por uso, salvamento |
| `69ff086` | Fase 1 | Conteúdo em YAML, skills e efeitos genéricos, previsão de movimento no cliente |
| `2c0164d` | Design | `docs/design.md` com visão, decisões e ordem das bases |
| `3e2ed33` | Fase 2 | Saves versionados, regras em YAML, raças e classes, morte com contexto |
| `b0f3324` | Base 5 | Zonas segura/PvE/PvP e duelos |
| `7f0a296` | Base 6 | Itens, inventário e saque |
| `928b66d` | Base 7 | Ferramenta de balanceamento |
| `9e2f7f7` | Base 8 | Organizações e cargos |
| `6719f58` | Base 9 | Vários mapas, portais e visão por raio |
| `abc99f6` | Base 10 | Administração |
| `c9a094c` | Base 11 | Recarregar conteúdo ao vivo e mapas no Tiled |
| `ceb9c05` | Interface | Duelos e organizações por menus e botões, no lugar dos comandos |
| `2f9d345` | NPCs e lojas | NPCs com diálogo (condições e ações), lojas de compra e venda |
| `4f487ce` | Missões | Oferta e entrega pelos NPCs, objetivos, diário, recompensas, save v5 |
| `3e2b17f` | Colisão | Jogadores, inimigos e NPCs não se atravessam (servidor e previsão) |
| `0ab69da` | Inimigos por nível | Nível, elites, chefes com fases, Ruínas e Fenda (níveis 15–50) |
| `9518f90` | Caminho dos inimigos | A* na grade de tiles: contornam paredes, saem de salas, desistem sem rota |
| `e6ea339` | Treinadores e skills | Skills conhecidas + barra escolhida (janela K, arrastar), treinadores com requisitos, save v6 |
| `a757a70` | Criação de itens | Oficinas nos NPCs, receitas com materiais, preço e requisitos, receitas secretas, evento `craft` |
| `671f705` | Visão 3D (protótipo) | Mundo 3D com personagens 2D virados para a câmera (three.js), zoom, giro de 90°, troca com o 2D (V) |
| `526cfad` | Grupos | Convite pelo menu, painel com a vida dos membros, chat do grupo, abates/missões/moedas divididos, sem fogo amigo |
| `9ba88c1` | Contas e proteções | Conta com até 4 personagens, troca e senha temporária, bans por conta/IP, travas de senha, limites de mensagens e chat |
| `3fa50f1` | Relatar bug | Botão no painel; o relato vai com mapa, posição, hora e dados do navegador; equipe vê, vai até o local e resolve |
| `e44704b` | Roleplay | Fala local em balão, ações (/me), mensagem privada, ignorar e perfil do personagem (janela P), save v7 |
| `ddaba3b` | Minimapa | Mapa inteiro no canto (tecla N: pequeno, grande, escondido), com você, grupo, jogadores, NPCs e marcas de missão, inimigos por perto, portais e PvP |
| `bd6b0f6` | Troca e banco | Troca entre jogadores (pedido pelo menu, os dois aceitam, salva na hora e fica registrada) e banco da conta no banqueiro e nos acampamentos |
| `026ff9a` | Eventos em ondas | Invasões em `data/events.yaml`: ondas que marcham até um ponto, tempo, recompensa para quem lutou, começo pela equipe ou agenda, evento `event_end` nas regras |
| — | Economia | Moedas só entram vendendo itens para NPCs (sem moedas de inimigos, missões, regras ou eventos); registro de entradas e saídas, relatório na administração e em `./admin.sh economia`, moedas por inimigo no balanceamento |

### Sistemas prontos

- **Motor**: servidor autoritativo (20 ticks/s) com WebSocket; cliente em JavaScript
  puro que prevê o próprio movimento (com teste que garante física idêntica nos
  dois lados); cada jogador só recebe o que está no mesmo mapa e por perto. Jogadores,
  inimigos e NPCs têm corpo: não se atravessam (cada par liga/desliga em `config.py`).
- **Personagem**: raça (escolhida na criação), classe (dá as skills iniciais), quatro
  atributos treinados por uso (Força, Espírito, Agilidade, Vitalidade), meditação e
  descanso com cansaço, inventário com equipamento e moedas.
- **Combate**: skills de vários tipos (golpe, projétil, área, dash, buff) e efeitos
  de status (lento, atordoado, veneno, buffs).
- **Skills aprendidas**: cada personagem conhece skills (iniciais da classe, compradas de
  treinadores ou dadas por regras/missões) e escolhe 8 para a barra na janela de skills (K),
  arrastando. A barra trava em combate e em duelos. Skills restritas a uma raça/classe ficam
  bloqueadas depois de uma transformação, mas não se perdem. Treinadores (`data/trainers.yaml`)
  mostram cada skill com preço e o que falta (nível de atributo, outra skill, raça/classe,
  nível total, missão).
- **Criação de itens**: oficinas abertas por NPCs (`data/crafting.yaml`) com receitas que
  transformam materiais e moedas em itens, mostrando o que o jogador tem de cada material;
  cria uma ou várias vezes (tudo ou nada); receitas secretas aparecem só com a condição
  (ex.: depois de uma missão); evento `craft` nas regras; o validador recusa receitas que
  dariam lucro sem fim com as lojas.
- **Grupos**: até 5 jogadores, convite pelo menu do jogador, painel com a vida de cada um,
  canal "Grupo" no chat. Membros perto dividem o bônus de abate (quem lutou ganha 50%), os
  abates contam para as missões de todos e o saque do grupo é de qualquer membro; não se ferem
  em áreas PvP.
- **Troca entre jogadores**: "Propor troca" no menu do jogador (ou `/troca Nome`), aviso com
  Aceitar/Recusar; cada um põe itens da bolsa e moedas; os dois aceitam e qualquer mudança desfaz os
  aceites. O servidor confere tudo de novo e faz a troca de uma vez (ou nada, se não couber); os dois
  personagens e o registro da troca são salvos juntos, na hora (`./admin.sh trocas`). Cancela se alguém
  se afastar, sair, for derrotado ou entrar em duelo; quem ignora não recebe pedidos.
- **Banco**: um por **conta** (todos os personagens dela), 40 espaços e moedas, aberto pelo banqueiro
  Matias nos Campos de Início e pelos NPCs dos acampamentos das Ruínas e da Fenda. Cada depósito ou
  saque salva personagem e banco juntos, na hora.
- **Contas e proteções**: conta com até 4 personagens (tela de escolher/criar), troca de
  senha no jogo e senha temporária pela equipe; bans por personagem, conta ou conta + IP;
  senha conferida fora do loop do jogo; travas de tentativas de senha, limite de contas por
  IP, de mensagens por conexão e de chat. Bancos antigos viram uma conta por personagem.
- **Relatar bug**: botão no painel; o relato guarda personagem, conta, mapa, posição, zona,
  hora, visão 2D/3D, navegador e os últimos erros de JavaScript. A equipe é avisada, vê a
  lista na administração (ir até o local, resolvido) ou usa `./admin.sh bugs`.
- **Roleplay**: canais **Local** (só quem está a até 12 tiles, com balão acima da cabeça, no 2D e no
  3D) e **Ação** ("\* Ana acena \*", também em balão); **mensagem privada** pelo menu do jogador, pelo
  perfil ou clicando num nome no chat; **ignorar** (sem mensagens de nenhum canal, desafios nem convites;
  a equipe não pode ser ignorada); **perfil** com descrição livre (janela P e "Ver perfil" no menu), que
  a equipe pode apagar. Ignorados e perfil vão no save (v7).
- **Economia**: moedas só entram no jogo vendendo itens para lojas de NPC e circulam entre jogadores pela
  troca; saem em compras, receitas, treinadores e perdas por regras. Tudo é anotado por dia e tipo; o relatório
  (administração, seção Economia, e `./admin.sh economia`) mostra quantas moedas existem, o que entrou e saiu,
  os itens que mais trouxeram moedas, quem tem mais e alertas de inflação. O relatório de balanceamento mostra
  quanto cada inimigo rende vendendo o saque.
- **Eventos em ondas (invasões)**: definidos em `data/events.yaml`. Os inimigos de cada onda aparecem
  numa área e marcham até um ponto (ex.: a porta da cidade), atacando quem encontram; a próxima onda vem
  8 s depois de a anterior cair. Vencer todas dentro do tempo dá recompensas (ações das regras) a quem
  causou dano suficiente; acabar o tempo é derrota. Começa pela equipe (permissão `events`, na janela de
  administração) ou sozinho a cada tantos minutos, com um mínimo de jogadores online. Painel no lado com
  onda, inimigos restantes, tempo e a sua participação; invasores aparecem com ⚔ e em laranja no minimapa.
- **Regras em YAML** (`data/rules.yaml`): "quando X, se Y, faça Z" para morte, abate,
  subida de nível, fim de duelo e mudança de cargo — penalidades, transformações,
  recompensas.
- **Zonas e PvP**: cidade segura, campos PvE, áreas PvP; duelos combinados que não
  matam.
- **Organizações**: cargos por nomeação ou por disputa em duelo, chat próprio,
  título acima do nome.
- **Minimapa**: o mapa inteiro no canto da tela, no 2D e no 3D; tecla N alterna pequeno → grande →
  escondido, clique amplia ou diminui, Esc fecha o grande. Mostra você (branco com contorno amarelo e
  para onde olha), o grupo (verde, mesmo longe), outros jogadores, todos os NPCs do mapa com os **!** e **?**
  das missões, inimigos por perto, portais e áreas PvP; no grande, os nomes dos NPCs e dos destinos.
- **Mundo**: quatro mapas ligados por portais, do nível 1 ao 50 (Campos de Início,
  Bosque Sombrio, Ruínas Esquecidas e Fenda do Vazio), inimigos com nível, elites
  sorteadas e dois chefes com fases e invocações. Os inimigos acham caminho (A*) em
  volta de paredes e água, e desistem quando não há rota.
- **NPCs e lojas**: NPCs parados nos mapas (`data/npcs.yaml`), conversa por botões
  (clique ou tecla E) com opções que dependem de raça, classe, organização, moedas,
  nível total e marcas, e que podem dar itens, marcar progressão ou trocar
  raça/classe; lojas (`data/shops.yaml`) que vendem com estoque infinito e compram por
  uma fração do valor — o primeiro escoamento de moedas.
- **Missões**: oferecidas e recebidas pelos NPCs na conversa (com **!** e **?** sobre a
  cabeça), objetivos de derrotar, trazer itens, falar com NPC, chegar a um lugar ou ter
  uma marca; recompensas com as ações das regras; repetíveis com intervalo; diário (J)
  e acompanhamento no painel lateral.
- **Administração**: papéis da equipe, janela de administração (expulsar, silenciar,
  banir, teleporte, dar itens, anúncios, registro de tudo).
- **Ferramentas**: relatório de balanceamento (`./balance.sh`), administração pelo
  terminal (`./admin.sh`), mapas no Tiled (`./mapas.sh`), recarga de conteúdo com o
  jogo rodando (botão na administração ou `./run.sh --watch`), backups automáticos.

### Conteúdo de exemplo (provisório)

- **Raças**: Humano e Desperto (escolhíveis), Espectral (alcançada por regra).
- **Classes**: Aprendiz, Iniciado, Assombração.
- **Organizações**: Vigília (Humanos e Despertos) e Coro Sombrio (Espectrais).
- **NPCs**: Hilda (mercadora), Otto (ferreiro e forja), Vó Celeste (dá 3 poções uma vez e
  faz poções), Ivo
  (treinador: Onda de choque, Leque de energia, Foco) e Matias (banqueiro) nos Campos de Início; Bruno (caçador,
  paga melhor por materiais) e Eco (treinador dos Espectrais: Leque) no Bosque Sombrio;
  Dorian (altar do vazio) na Fenda. Lia (Ruínas) e Dorian também abrem o banco.
- **Receitas**: Forja (Gibão de couro, Espada curta, Couraça de bruto e, secreta depois do
  Guardião, Armadura de pedra), Ervas e infusões (poções e Tônico veloz) e Altar do vazio
  (Manto do vazio).
- **Skills iniciais**: Aprendiz (Golpe, Rajada, Passo veloz), Iniciado (Golpe, Rajada,
  Flecha gélida), Assombração (Golpe, Dardo venenoso, Passo veloz); o resto vem dos
  treinadores.
- **Missões**: Primeiros passos → Notícias do bosque → Caça ao Devorador (Vó Celeste e
  Bruno), Couro para a forja (Otto) e Fragmentos para a mercearia (Hilda, repetível).
- **Inimigos**: Espectro (1), Bruto (4), Devorador (9), Sentinela de pedra (16),
  Vulto (20), Carniçal (25), Abissal (36), Arauto do Vazio (42), Colosso (47); chefes
  Guardião das Ruínas (30) e Devorador Ancião (50), com os invocados Estilhaço e Cria
  do vazio. **Itens**: 19, entre poções, materiais e equipamentos (até épicos).
- **Evento de exemplo**: Invasão dos Campos (3 ondas de Espectros, Brutos e um Devorador marchando até a
  porta leste da cidade; 3 poções e 2 couros para quem lutar; sozinho a cada 90 min com 3 online).
- **Regras de exemplo**: Humano morto por Espectro vira Espectral; Espectral morto
  por Desperto volta a Humano; perda de XP ao morrer em PvE/PvP; marca de agressor.

Todo esse conteúdo foi criado para demonstrar os sistemas e deve ser substituído
pelo conteúdo real do jogo.

---

## 5. O que o balanceamento mostrou

Do `docs/balanceamento.md` (lutas simuladas no próprio motor):

- **O golpe básico domina o dano no começo** (78–90% no nível 1); as skills de
  energia pesam pouco porque a energia é curta.
- **Inimigos por faixa de nível** (resolvido): cada inimigo é testado no próprio
  nível e todos estão na faixa-alvo (normais folgados, elites apertadas, chefes
  perto do empate).
- **A Assombração fica para trás** em níveis altos (dano/s ~35% abaixo das outras),
  e perde para várias elites e chefes no próprio nível.
- **PvP fica rápido demais em níveis altos** (lutas de ~2,5 s no nível 25).
- Limitação da ferramenta: os lutadores ficam parados, então alcance e esquiva não
  contam.

---

## 6. Em aberto

### Decisões do dono

- **Liberações (estágios 1 e 2)**: como se conquistam, o que fazem, se são por classe
  ou por raça, o que muda entre os estágios.
- **Teto de progressão**.
- **Penalidades de morte e transformações de verdade** (as atuais são exemplos).
- **Hierarquia**: limite de recusas de desafio de cargo, cargo vago por inatividade,
  cargos por torneio ou contribuição.
- **Mundo aberto ou portais**; quantos jogadores ao mesmo tempo.
- **Nomenclatura** e identidade do mundo (por último).

### Trabalho técnico ainda não feito

- Recuperação de senha por e-mail (hoje só a equipe gera uma senha temporária).
- **Hospedagem na VPS**: HTTPS, reinício automático, backup fora da máquina.
- Lojas com estoque
  limitado ou preço variável; eventos iniciados pela equipe (ex.: invasões).
- Skills que sobem de nível com o uso; mais skills e treinadores para os níveis altos.
- Criação com tempo ou chance de falha, ofícios que sobem de nível, objetivo de missão "criar".
- **Arte e som** (hoje tudo são formas geométricas); tutorial dos primeiros minutos.
- **Visão 3D**: há um protótipo (tecla V) com mundo 3D e personagens 2D virados para a
  câmera. Falta decidir se vira o visual oficial e, com isso, o formato da arte (sprites em
  4 ou 8 direções, texturas de chão e parede, modelos simples para árvores e construções).

---

## 7. Próximos passos recomendados

O roteiro completo, por fases, está em `docs/design.md` (seção 12).

1. **Alfa fechado na VPS** — contas, proteções e hospedagem, para o dono e alguns
   amigos jogarem de verdade.
2. **Liberações e teto de progressão** — é o que mais define o jogo; depende das
   decisões em aberto.
3. **Ajustes de classes** que o balanceamento aponta (golpe básico dominando, Assombração
   fraca).
4. Arte e som em paralelo, a qualquer momento.

---

## 8. Como rodar e testar

```bash
./run.sh              # servidor em http://127.0.0.1:8080
./run.sh --watch      # idem, recarregando o conteúdo ao salvar arquivos de data/
./balance.sh --out docs/balanceamento.md
./admin.sh role NomeDoPersonagem admin
./mapas.sh verificar
UV_PROJECT_ENVIRONMENT=~/.venvs/rpg-online uv run python -m unittest
```

Observações do ambiente:

- O projeto fica numa pasta do Google Drive; o ambiente virtual do Python fica fora
  dela (`~/.venvs/rpg-online`) para não sincronizar milhares de arquivos.
- **O banco de dados fica só na máquina.** O banco (`var/game.db`, com contas,
  personagens, equipe, bans e organizações), os backups e a lista local de contas de
  teste (`var/contas-de-teste.txt`) ficam em `var/`, que **não** vai para o GitHub (está
  no `.gitignore`). Ou seja: quem baixar o repositório do GitHub (outro computador, a
  VPS) **não recebe banco nenhum** e precisa criar o seu.
- **Como criar o banco local depois de baixar o repositório:** basta ligar o servidor
  (`./run.sh`). Se `var/game.db` não existir, ele é criado vazio, com todas as tabelas.
  Depois:
  1. abra o jogo e crie uma conta e um personagem;
  2. dê o papel de administrador ao seu personagem: `./admin.sh role SeuPersonagem admin`
     (vale no próximo login);
  3. as contas de teste e a lista `var/contas-de-teste.txt` desta máquina não existem lá:
     crie as que precisar.
- **Para levar os dados desta máquina para outra** (ex.: a VPS), copie o arquivo
  `var/game.db` (ou um backup de `var/backups/`) com o servidor parado. Não há outro
  jeito: o GitHub só tem o código e o conteúdo (`data/`).
- Para testar multiplayer, abra duas abas do navegador e entre com personagens
  diferentes.
