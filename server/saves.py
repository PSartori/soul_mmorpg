"""Versões do formato de save dos personagens.

O jogo não tem wipes: um personagem criado hoje precisa continuar funcionando
depois de qualquer mudança. Cada save guarda o número da versão ("v"); ao
carregar, as migrações levam o save, passo a passo, até a versão atual.

Para mudar o formato:
  1. aumente SAVE_VERSION;
  2. escreva _vN_to_vN+1(data) e registre em MIGRATIONS;
  3. adicione um teste com um save no formato antigo.
Nunca altere uma migração já publicada: saves antigos dependem dela.
"""
from __future__ import annotations

from collections.abc import Callable

SAVE_VERSION = 7


class SaveVersionError(ValueError):
    pass


def _v1_to_v2(data: dict) -> dict:
    """v2: raça, classe e marcas de progressão.

    Raça/classe ficam vazias; o servidor preenche com a raça padrão
    (data/player.yaml) e a classe inicial dela.
    """
    data.setdefault("race", None)
    data.setdefault("class", None)
    data.setdefault("flags", [])
    return data


def _v2_to_v3(data: dict) -> dict:
    """v3: inventário (espaços, equipamento e moedas), começando vazio."""
    data.setdefault("inventory", {"slots": [], "equipment": {}, "gold": 0})
    return data


def _v3_to_v4(data: dict) -> dict:
    """v4: em qual mapa o personagem está. Vazio = o mapa inicial (o único que existia)."""
    data.setdefault("map", None)
    return data


def _v4_to_v5(data: dict) -> dict:
    """v5: missões em andamento (progresso) e concluídas, começando vazias."""
    data.setdefault("quests", {"active": {}, "done": {}})
    return data


# Barra de cada classe na versão 5 (antes das skills aprendidas): naquela época a classe
# dava todas estas skills. Fica escrito aqui, e não lido de classes.yaml, porque o
# conteúdo muda e a migração precisa dar sempre o mesmo resultado.
_V5_CLASS_SKILLS = {
    "aprendiz": ["golpe", "rajada", "passo", "onda", "foco"],
    "iniciado": ["golpe", "rajada", "flecha_gelida", "leque", "onda", "foco"],
    "assombracao": ["golpe", "dardo", "passo", "leque"],
}


def _v5_to_v6(data: dict) -> dict:
    """v6: skills conhecidas e barra de atalhos escolhida pelo jogador.

    Quem já existia continua sabendo as skills que a classe dava e com a mesma
    barra. Classe desconhecida: vazio, e o servidor dá as skills iniciais da classe.
    """
    skills = list(_V5_CLASS_SKILLS.get(data.get("class") or "", []))
    data.setdefault("skills", {"known": skills, "bar": skills})
    return data


def _v6_to_v7(data: dict) -> dict:
    """v7: perfil (descrição do personagem) e lista de ignorados, começando vazios."""
    data.setdefault("social", {"profile": "", "ignore": []})
    return data


MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
    4: _v4_to_v5,
    5: _v5_to_v6,
    6: _v6_to_v7,
}


def migrate(data: dict) -> dict:
    """Devolve uma cópia do save na versão atual. Saves sem "v" são da versão 1."""
    data = dict(data)
    version = data.get("v", 1)
    if not isinstance(version, int) or version < 1:
        raise SaveVersionError(f"versão de save inválida: {version!r}")
    if version > SAVE_VERSION:
        raise SaveVersionError(f"save na versão {version}, mais nova que a do servidor ({SAVE_VERSION})")
    while version < SAVE_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["v"] = version
    return data
