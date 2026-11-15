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

SAVE_VERSION = 4


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


MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
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
