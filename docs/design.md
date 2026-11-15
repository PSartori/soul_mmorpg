# Documento de design

Visão do jogo e ordem das bases a construir. É um documento vivo: decisões
marcadas como **[a decidir]** ainda estão abertas. Nomes de raças, classes e
termos do mundo são provisórios; a nomenclatura fica para o fim.

## 1. Visão

RPG online 2D persistente, com progressão longa por treino e repetição,
inspirado nos jogos de anime do BYOND (veja `referencia-bleach-shinzui.md`).

### Pilares

1. **PvE e roleplay primeiro, com PvP presente.** O dia a dia é treinar, caçar e
   interagir; o PvP existe em lugares e situações definidos, não em todo lugar.
2. **A morte importa.** Como e por quem o personagem morreu muda o que acontece
   com ele — de uma penalidade leve até uma mudança de classe.
3. **Progressão por repetição.** O personagem melhora fazendo: andar, meditar,
   lutar, usar cada técnica.
4. **Hierarquia entre jogadores.** Cargos de organizações são ocupados por
   jogadores e conquistados por disputa.
5. **Mundo permanente.** Sem wipes: personagens, cargos e itens duram para sempre.

## 2. Decisões tomadas

| Tema | Decisão | O que isso exige |
|---|---|---|
| Foco | PvE + roleplay, com elementos de PvP | Zonas com regras diferentes (segura / PvE / PvP), duelos consentidos |
| Raça e classe | Escolhidas na criação, podem mudar depois | Raça e classe como dados; caminhos de troca (morte, evento, NPC) |
| Morte | Penalidade depende de como morreu; certas mortes mudam a classe | Registrar a **causa da morte** e reagir a ela com regras em dados |
| Progressão | Repetição | Já existe (treino por uso); falta o teto |
| Hierarquia | Cargos por disputa | Organizações persistentes, cargos e um sistema de desafio |
| Itens | Loot e balanceamento de classes | Itens, tabelas de saque e uma ferramenta de balanceamento |
| Wipes | Nunca | Saves versionados com migração, backups, controle de inflação |

## 3. Morte e transformação

A morte vira um **evento com contexto**, e regras em YAML decidem o que fazer.

Contexto registrado em cada morte:

- raça e classe de quem morreu;
- quem matou: jogador, tipo de inimigo, efeito (ex.: veneno) ou o ambiente;
- qual skill deu o golpe final e quais efeitos estavam ativos;
- onde: zona segura, PvE, PvP, evento;
- estado do personagem (ex.: estava numa liberação, estava com pouca energia).

Exemplos de regras (ilustrativos):

```yaml
# Quem morre numa zona PvE para um inimigo perde parte do XP do atributo mais treinado.
- when: death
  if: { zone: pve, killer: enemy }
  do: [ { lose_xp: { percent: 10, stat: highest } } ]

# Raça A morta por um inimigo do tipo X vira a classe "errante".
- when: death
  if: { race: raca_a, killer_enemy: tipo_x }
  do: [ { set_class: errante }, { message: "Algo mudou em você..." } ]

# Duelo consentido não tem penalidade.
- when: death
  if: { duel: true }
  do: []
```

**[a decidir]** quais penalidades existem (perda de XP, de energia máxima por um
tempo, de itens, de cargo) e quais transformações cada raça tem.

## 4. Teto de progressão **[a decidir]**

Opções, que podem ser combinadas:

- **Teto por atributo definido pela classe** — cada classe tem pontos fortes e fracos.
- **Teto total** (soma dos níveis) que sobe com as liberações — liga a progressão
  longa a marcos claros.
- **Retorno decrescente** — o XP necessário cresce cada vez mais rápido; nunca para,
  mas desacelera (já é parcialmente assim).
- **Limite diário suave** — o cansaço já cumpre parte desse papel.

Sugestão inicial: teto por atributo vindo da classe + teto total liberado pelas
liberações, com retorno decrescente perto do teto.

## 5. Liberações **[a decidir]**

Formas de conquistar, que podem ser combinadas:

- **Sincronia**: um atributo próprio treinado usando a arma/técnicas da classe;
- **Provação**: uma missão ou luta especial ao atingir os requisitos;
- **Evento de quase-morte**: chance de despertar ao sobreviver com pouca vida;
- **Aprovação de um admin/GM**, como em muitos jogos de roleplay.

Sugestão inicial: requisito de sincronia + uma provação, para não depender de
admin online e ainda ter um momento marcante.

## 6. Hierarquia e disputa **[a decidir]**

- Organizações com cargos numerados/limitados, gravados no banco.
- **Desafio formal**: um jogador desafia o ocupante de um cargo; ambos aceitam um
  duelo com regras próprias (sem penalidade de morte, local definido).
- Alternativas ou complementos: torneios periódicos, pontos de contribuição,
  indicação pelo líder.
- Precisa de regras contra abuso: tempo mínimo entre desafios, cargo vago por
  inatividade, recusa limitada.

## 7. Itens, saque e balanceamento

- Itens em YAML: tipo (equipamento, consumível, material), raridade, efeitos.
- Tabelas de saque por inimigo, com chance por item.
- Sem wipes, precisa de **escoamento** de dinheiro e itens (consertos, taxas,
  consumíveis) para não inflacionar.
- **Ferramenta de balanceamento**: um script que lê os YAML e calcula dano por
  segundo, tempo para matar e custo de energia por classe e nível — para comparar
  classes com números, antes de testar jogando.

## 8. Mundo **[a decidir]**

Mundo aberto contínuo ou mapas ligados por portais. A base técnica é a mesma nos
dois casos: dividir o mundo em áreas e mandar a cada jogador só o que está perto
dele. Dá para construir essa base agora e decidir o formato depois.

## 9. Bases, em ordem

| # | Base | Por quê agora |
|---|---|---|
| 1 | **Saves versionados + backups** | Sem wipes, todo campo novo precisa migrar personagens antigos sem perda |
| 2 | **Gatilhos e condições em YAML** | Peça central: morte contextual, transformações, liberações, missões, conquistas |
| 3 | **Raças e classes como dados** | Escolha na criação, skills e tetos por classe, caminhos de troca |
| 4 | **Morte contextual** | Causa da morte → penalidades e transformações (usa a base 2) |
| 5 | **Zonas de regras + duelos** | PvP nos lugares certos; duelos servem também para a disputa de cargos |
| 6 | **Itens, inventário e saque** | Loot e equipamentos; base para economia |
| 7 | **Ferramenta de balanceamento** | Comparar classes com números conforme o conteúdo cresce |
| 8 | **Organizações e cargos** | Hierarquia, desafios, facções |
| 9 | **Áreas do mundo + envio só do que está perto** | Escala e mundo aberto ou portais |
| 10 | **Administração** | Papéis, comandos, registro de ações |
| 11 | **Ferramentas de conteúdo** | Recarregar YAML sem reiniciar, mapas no Tiled |

**Fase 2 (concluída):** bases 1 a 4 — saves versionados com migração e backup
automático, regras em `data/rules.yaml`, raças e classes em `data/races.yaml` e
`data/classes.yaml`, e morte com contexto disparando as regras. Já dá para criar
personagem escolhendo raça, morrer de jeitos diferentes e ver consequências
diferentes. As raças, classes e regras atuais são **exemplos provisórios**.

**Base 5 (concluída):** zonas segura/PvE/PvP por mapa (`data/maps/<mapa>.yaml`)
e duelos combinados (`/duelo`), que não matam e disparam o evento `duel_end` —
base para a disputa de cargos (seção 6).

**Base 6 (concluída):** itens em `data/items.yaml` (equipamento, consumível,
material), inventário com equipamento e moedas (save v3), saque no chão com
exclusividade para quem matou, e ações de regra para dar itens e dar/tirar
moedas. Lojas ficam para quando houver NPCs.

**Base 7 (concluída):** `./balance.sh` gera `docs/balanceamento.md` com classes,
rotação, PvE, PvP e equipamentos por nível, mais alertas automáticos. Primeiros
achados com o conteúdo de exemplo: o golpe básico domina o dano no começo (as
skills de energia pesam pouco), e os inimigos atuais deixam de ser desafio a partir
do nível 10 — faltam inimigos por faixa de nível.

**Base 8 (concluída):** organizações em `data/organizations.yaml` com cargos por
nomeação ou disputa (duelo valendo o cargo), chat próprio, título acima do nome e
evento `rank_change` nas regras. As duas organizações atuais são exemplos.

**Base 9 (concluída):** vários mapas (`data/maps/*.txt` + `.yaml`) ligados por
portais, cada um com seus inimigos e saque; mapa salvo no personagem (save v4); e
cada jogador só recebe o que está no mesmo mapa e dentro do raio de visão. Novo mapa
"Bosque Sombrio" com o Devorador, que cobre a faixa de nível intermediária (perigoso
no nível 1, luta razoável no 10). A decisão "mundo aberto ou portais" segue aberta:
mundo aberto seria um mapa grande com a mesma visão por raio; portais já funcionam.

Ainda a decidir sobre a hierarquia (seção 6): limite de recusas de desafio de
cargo (hoje pode recusar sempre), cargo vago por inatividade do ocupante, e se
alguns cargos devem ser por torneio ou pontos de contribuição.

## 10. Pendências de interface

- **Trocar os comandos de chat por interface.** Hoje duelos (`/duelo`, `/aceitar`,
  `/recusar`, `/render`) e organizações (`/org`, `/entrar`, `/sair`, `/o`,
  `/assumir`, `/disputar`, `/promover`, `/rebaixar`, `/expulsar`) dependem de digitar
  comandos — decisão: isso precisa virar interface. Ideias: menu ao clicar num
  jogador (desafiar, disputar cargo, promover, expulsar), aviso com botões para
  aceitar/recusar desafios, janela da organização (cargos, ocupantes, entrar/sair,
  assumir vaga) e aba de chat da organização. Os comandos podem continuar como
  atalho, mas não como único caminho.

## 11. Perguntas em aberto

- Penalidades de morte: quais existem e quão duras?
- Transformações por morte: quais caminhos cada raça tem?
- Teto de progressão (seção 4) e liberações (seção 5).
- Formato da disputa por cargos (seção 6).
- Mundo aberto ou portais (seção 8).
- Quantos jogadores simultâneos queremos suportar.
