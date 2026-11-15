"""Administração pelo terminal (mexe direto no banco).

Uso:
  ./admin.sh role Nome admin        dá um papel (admin, gm, moderador — veja data/roles.yaml)
  ./admin.sh role Nome nenhum       tira o papel
  ./admin.sh staff                  lista a equipe
  ./admin.sh bans                   lista os banidos
  ./admin.sh unban Nome             desbane
  ./admin.sh log [N]                últimas N ações da equipe (padrão 20)

Serve para criar o primeiro administrador e para emergências (ex.: desbanir com o
servidor fora do ar). Mudança de papel vale a partir do próximo login da pessoa.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from server import config as C
from server.admin import format_until
from server.content import Content
from server.db import Database


def main(argv: list[str] | None = None, db_path: Path = C.DB_PATH) -> int:
    parser = argparse.ArgumentParser(description="Administração do jogo pelo terminal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    role = sub.add_parser("role", help="dá ou tira um papel da equipe")
    role.add_argument("name")
    role.add_argument("role", help="id do papel em data/roles.yaml, ou 'nenhum'")
    sub.add_parser("staff", help="lista a equipe")
    sub.add_parser("bans", help="lista os banidos")
    unban = sub.add_parser("unban", help="desbane um personagem")
    unban.add_argument("name")
    log = sub.add_parser("log", help="últimas ações da equipe")
    log.add_argument("n", type=int, nargs="?", default=20)
    args = parser.parse_args(argv)

    db = Database(db_path)
    roles = Content.load(C.DATA_DIR).roles

    if args.cmd == "role":
        record = db.load_character(args.name)
        if record is None:
            print(f"Não existe personagem chamado {args.name}.", file=sys.stderr)
            return 1
        if args.role == "nenhum":
            db.set_role(record.name, None)
            db.log_admin("terminal", "mudou o papel de", record.name, "nenhum")
            print(f"{record.name} não faz mais parte da equipe.")
        elif args.role in roles:
            db.set_role(record.name, args.role)
            db.log_admin("terminal", "mudou o papel de", record.name, args.role)
            print(f"{record.name} agora é {roles[args.role].name}. Vale a partir do próximo login.")
        else:
            print(f"Papel desconhecido: {args.role}. Válidos: {', '.join(roles)}, nenhum.", file=sys.stderr)
            return 1
    elif args.cmd == "staff":
        rows = db.list_staff()
        for name, role_id in rows:
            print(f"{name}: {roles[role_id].name if role_id in roles else role_id}")
        if not rows:
            print("Ninguém na equipe.")
    elif args.cmd == "bans":
        rows = db.list_active("ban", time.time())
        for name, until, reason, by in rows:
            print(f"{name}: {format_until(until)} — por {by}" + (f" — {reason}" if reason else ""))
        if not rows:
            print("Ninguém banido.")
    elif args.cmd == "unban":
        if db.lift_sanctions(args.name, "ban") == 0:
            print(f"{args.name} não está banido.", file=sys.stderr)
            return 1
        db.log_admin("terminal", "desbaniu", args.name)
        print(f"{args.name} foi desbanido.")
    elif args.cmd == "log":
        for at, actor, action, target, details in db.recent_log(args.n):
            stamp = time.strftime("%d/%m %H:%M", time.localtime(at))
            print(f"{stamp}  {actor} {action} {target}" + (f" ({details})" if details else ""))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
