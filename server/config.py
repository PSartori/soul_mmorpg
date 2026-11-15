"""Constantes do servidor e de balanceamento.

Todo número "mágico" do jogo mora aqui, para facilitar ajustes sem caçar no código.
Unidades: distâncias em pixels, tempos em segundos, taxas "por segundo".
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = ROOT / "client"
DATA_DIR = ROOT / "data"
VAR_DIR = ROOT / "var"
DEFAULT_MAP = DATA_DIR / "maps" / "inicio.txt"
DB_PATH = VAR_DIR / "game.db"

TICK_RATE = 20  # simulações por segundo
TILE_SIZE = 32
AUTOSAVE_SECONDS = 60.0

# --- Personagem -------------------------------------------------------------
ENTITY_HALF = 10.0  # metade do lado da hitbox quadrada
RESPAWN_SECONDS = 3.0
MAX_FATIGUE = 100.0
MEDITATE_FATIGUE_LIMIT = 75.0  # só começa a meditar com cansaço abaixo disto

# --- Movimento --------------------------------------------------------------
BASE_SPEED = 120.0
SPEED_PER_AGILITY = 3.0
MAX_SPEED = 240.0

# --- Combate corpo a corpo --------------------------------------------------
MELEE_COOLDOWN = 0.45
MELEE_REACH = 20.0  # distância do centro do jogador ao centro da área do golpe
MELEE_HALF = 14.0  # metade do lado da área do golpe

# --- Rajada de energia (ataque à distância) ---------------------------------
BLAST_COST = 10.0
BLAST_COOLDOWN = 0.8
BLAST_SPEED = 380.0
BLAST_TTL = 0.9
BLAST_RADIUS = 5.0

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
STRENGTH_XP_PER_HIT = 3.0
SPIRIT_XP_PER_HIT = 2.0
VITALITY_XP_PER_DAMAGE = 0.25
KILL_BONUS_XP = 10.0

# --- Inimigos ---------------------------------------------------------------
ENEMY_NAME = "Espectro"
ENEMY_HP = 40.0
ENEMY_DAMAGE = 5.0
ENEMY_SPEED = 90.0
ENEMY_HP_REGEN = 2.0
ENEMY_ATTACK_RANGE = 26.0
ENEMY_ATTACK_COOLDOWN = 1.0
ENEMY_AGGRO_RADIUS = 160.0
ENEMY_LEASH = 320.0  # distância máxima de casa antes de desistir de perseguir
ENEMY_WANDER_RADIUS = 96.0
ENEMY_RESPAWN_SECONDS = 15.0

# --- Chat -------------------------------------------------------------------
CHAT_MAX_LEN = 200
