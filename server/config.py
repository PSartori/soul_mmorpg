"""Constantes do servidor e de balanceamento geral.

Skills, efeitos e inimigos ficam em data/*.yaml; aqui fica só o que é regra
do motor. Unidades: distâncias em pixels, tempos em segundos, taxas "por segundo".
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT / "client"
DATA_DIR = ROOT / "data"
VAR_DIR = ROOT / "var"
MAPS_DIR = DATA_DIR / "maps"
START_MAP = "inicio"  # onde nascem personagens novos (data/maps/inicio.txt)
DEFAULT_MAP = MAPS_DIR / f"{START_MAP}.txt"
DB_PATH = VAR_DIR / "game.db"
BACKUP_DIR = VAR_DIR / "backups"

TICK_RATE = 20  # simulações por segundo
TILE_SIZE = 32
AUTOSAVE_SECONDS = 60.0
BACKUP_INTERVAL_HOURS = 6.0  # além de um backup ao ligar o servidor
BACKUP_KEEP = 28  # cópias guardadas (28 × 6 h = 1 semana)
VIEW_RADIUS = 900.0  # cada jogador só recebe o que está a esta distância (cobre a tela com folga)
MAX_RULE_DEPTH = 3  # regras que disparam eventos que disparam regras...: limite de encadeamento

# --- Rede -------------------------------------------------------------------
MAX_MOVE_QUEUE = 8  # comandos de movimento guardados por jogador; o excesso é descartado
MOVE_QUEUE_CATCHUP = 3  # com mais que isso na fila, executa 2 por tick para alcançar
MAX_QUEUED_ACTIONS = 4

# --- Personagem -------------------------------------------------------------
ENTITY_HALF = 10.0  # metade do lado da hitbox quadrada
RESPAWN_SECONDS = 3.0
MAX_FATIGUE = 100.0
MEDITATE_FATIGUE_LIMIT = 75.0  # só começa a meditar com cansaço abaixo disto

# --- Movimento --------------------------------------------------------------
BASE_SPEED = 120.0
SPEED_PER_AGILITY = 3.0
MAX_SPEED = 240.0
DASH_STEP = 8.0  # o dash avança em passos deste tamanho para não atravessar paredes

# --- Regeneração e cansaço --------------------------------------------------
HP_REGEN_IDLE = 0.5
HP_REGEN_REST = 3.0
ENERGY_REGEN_IDLE = 1.0
ENERGY_REGEN_MEDITATE = 4.0
ENERGY_REGEN_REST = 2.0
FATIGUE_MEDITATE = 6.0
FATIGUE_RECOVER_IDLE = 2.0
FATIGUE_RECOVER_REST = 15.0

# --- Treino (XP de atributos) -----------------------------------------------
MAX_STAT_LEVEL = 100
AGILITY_XP_PER_PX = 0.01  # 100 px andados = 1 xp
SPIRIT_XP_MEDITATE = 4.0  # por segundo meditando
VITALITY_XP_PER_DAMAGE = 0.25

# --- Inimigos (o resto fica em data/enemies.yaml) ----------------------------
ENEMY_HP_REGEN = 2.0  # por segundo, quando sem alvo
ENEMY_DETOUR_SECONDS = 0.5  # tempo contornando um obstáculo antes de tentar ir reto de novo

# --- Itens e saque ----------------------------------------------------------
INVENTORY_SIZE = 20
PICKUP_RADIUS = 24.0  # distância (centro a centro) para pegar um item do chão passando por cima
LOOT_OWNER_SECONDS = 15.0  # só quem matou pode pegar durante este tempo
LOOT_EXPIRE_SECONDS = 60.0  # depois disso o item some do chão
LOOT_SCATTER = 14.0  # espalha os itens em volta de onde o inimigo morreu
DROP_DISTANCE = 30.0  # item largado pelo jogador cai esta distância à frente dele
DROP_PICKUP_DELAY = 3.0  # quem largou só pega de volta depois disso

# --- NPCs e lojas (server/npcs.py; conteúdo em data/npcs.yaml e data/shops.yaml) ---
NPC_TALK_RANGE = 80.0  # distância (centro a centro) para começar a conversar (2,5 tiles)
NPC_LEAVE_RANGE = 128.0  # afastou-se mais que isso: a conversa (e a loja) fecha
SHOP_BUY_RATE = 0.4  # padrão do buy_rate: a loja paga 40% do valor do item
SHOP_MAX_QTY = 999  # máximo de unidades numa compra ou venda
MAX_ACTIVE_QUESTS = 10  # missões em andamento ao mesmo tempo (data/quests.yaml)

# --- Duelos (server/duels.py) -------------------------------------------------
DUEL_CHALLENGE_RANGE = 256.0  # distância máxima para desafiar/aceitar (8 tiles)
DUEL_CHALLENGE_SECONDS = 30.0  # o desafio expira
DUEL_COUNTDOWN = 3.0  # segundos entre aceitar e começar
DUEL_MAX_SECONDS = 180.0  # depois disso, empate
DUEL_LEASH = 480.0  # se os dois se afastarem mais que isso, empate (15 tiles)

# --- Organizações (server/orgs.py) ------------------------------------------
DISPUTE_COOLDOWN_SECONDS = 300.0  # quem perde uma disputa de cargo espera isto para tentar de novo

# --- Chat -------------------------------------------------------------------
CHAT_MAX_LEN = 200
