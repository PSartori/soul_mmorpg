# Contexto geral do projeto

Resumo de tudo o que foi conversado e construído até 15/11/2026 (atualizado com NPCs e lojas): de onde o projeto
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
  regras, organizações, papéis da equipe, NPCs, lojas e mapas ficam em `data/`, validados ao
  carregar (um erro de digitação aponta arquivo, item e campo).
- **Cada etapa termina testada**: testes automáticos (hoje 284) e verificação no
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
| — | NPCs e lojas | NPCs com diálogo (condições e ações), lojas de compra e venda |

### Sistemas prontos

- **Motor**: servidor autoritativo (20 ticks/s) com WebSocket; cliente em JavaScript
  puro que prevê o próprio movimento (com teste que garante física idêntica nos
  dois lados); cada jogador só recebe o que está no mesmo mapa e por perto.
- **Personagem**: raça (escolhida na criação), classe (define as skills), quatro
  atributos treinados por uso (Força, Espírito, Agilidade, Vitalidade), meditação e
  descanso com cansaço, inventário com equipamento e moedas.
- **Combate**: skills de vários tipos (golpe, projétil, área, dash, buff) e efeitos
  de status (lento, atordoado, veneno, buffs).
- **Regras em YAML** (`data/rules.yaml`): "quando X, se Y, faça Z" para morte, abate,
  subida de nível, fim de duelo e mudança de cargo — penalidades, transformações,
  recompensas.
- **Zonas e PvP**: cidade segura, campos PvE, áreas PvP; duelos combinados que não
  matam.
- **Organizações**: cargos por nomeação ou por disputa em duelo, chat próprio,
  título acima do nome.
- **Mundo**: dois mapas ligados por portais (Campos de Início e Bosque Sombrio), três
  tipos de inimigo com saque.
- **NPCs e lojas**: NPCs parados nos mapas (`data/npcs.yaml`), conversa por botões
  (clique ou tecla E) com opções que dependem de raça, classe, organização, moedas,
  nível total e marcas, e que podem dar itens, marcar progressão ou trocar
  raça/classe; lojas (`data/shops.yaml`) que vendem com estoque infinito e compram por
  uma fração do valor — o primeiro escoamento de moedas.
- **Administração**: papéis da equipe, janela de administração (expulsar, silenciar,
  banir, teleporte, dar itens, anúncios, registro de tudo).
- **Ferramentas**: relatório de balanceamento (`./balance.sh`), administração pelo
  terminal (`./admin.sh`), mapas no Tiled (`./mapas.sh`), recarga de conteúdo com o
  jogo rodando (botão na administração ou `./run.sh --watch`), backups automáticos.

### Conteúdo de exemplo (provisório)

- **Raças**: Humano e Desperto (escolhíveis), Espectral (alcançada por regra).
- **Classes**: Aprendiz, Iniciado, Assombração.
- **Organizações**: Vigília (Humanos e Despertos) e Coro Sombrio (Espectrais).
- **NPCs**: Hilda (mercadora), Otto (ferreiro) e Vó Celeste (dá 3 poções uma vez) nos
  Campos de Início; Bruno (caçador, paga melhor por materiais) no Bosque Sombrio.
- **Inimigos**: Espectro, Bruto, Devorador. **Itens**: 11, entre poções, materiais e
  equipamentos.
- **Regras de exemplo**: Humano morto por Espectro vira Espectral; Espectral morto
  por Desperto volta a Humano; perda de XP ao morrer em PvE/PvP; marca de agressor.

Todo esse conteúdo foi criado para demonstrar os sistemas e deve ser substituído
pelo conteúdo real do jogo.

---

## 5. O que o balanceamento mostrou

Do `docs/balanceamento.md` (lutas simuladas no próprio motor):

- **O golpe básico domina o dano no começo** (78–90% no nível 1); as skills de
  energia pesam pouco porque a energia é curta.
- **Inimigos não acompanham a progressão**: a partir do nível 10 o Espectro é
  trivial, e no 25 até o Devorador. Faltam inimigos para níveis altos.
- **O Devorador** cobre a faixa intermediária: vence qualquer classe no nível 1 e dá
  uma luta razoável no nível 10.
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

- **Contas separadas de personagens** (hoje cada personagem é uma conta); ban por
  conta/IP; recuperação de senha.
- **Hospedagem na VPS**: HTTPS, reinício automático, backup fora da máquina.
- **Proteções**: limite de mensagens por segundo, tentativas de senha.
- **Missões** (entregar itens, contar abates) e treinadores, em cima dos NPCs; lojas
  com estoque limitado ou preço variável; eventos iniciados pela equipe (ex.: invasões).
- **Arte e som** (hoje tudo são formas geométricas); tutorial dos primeiros minutos.

---

## 7. Próximos passos recomendados

1. **Liberações** — é o que mais define o jogo; depende das respostas acima.
2. **Missões** — os NPCs já têm diálogo com condições e ações; faltam objetivos
   (entregar itens, matar X inimigos) e recompensas.
3. **Alfa fechado na VPS** — contas, proteções e hospedagem, para o dono e alguns
   amigos jogarem de verdade.
4. **Inimigos para níveis altos** e ajustes que o balanceamento apontar.
5. Arte e som em paralelo, a qualquer momento.

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
- O banco (`var/game.db`), os backups e a lista local de personagens de teste
  (`var/contas-de-teste.txt`) ficam em `var/`, que **não** vai para o GitHub.
- Para testar multiplayer, abra duas abas do navegador e entre com personagens
  diferentes.
