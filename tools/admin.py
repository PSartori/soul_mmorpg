"""Administração pelo terminal (mexe direto no banco).

Uso:
  ./admin.sh role Nome admin        dá um papel (admin, gm, moderador — veja data/roles.yaml)
  ./admin.sh role Nome nenhum       tira o papel
  ./admin.sh staff                  lista a equipe
  ./admin.sh bans                   lista os banidos (personagens, contas e IPs)
  ./admin.sh unban Nome             desbane o personagem e a conta com esse nome
  ./admin.sh unban-ip IP            desbane um endereço IP
  ./admin.sh conta Nome             mostra a conta de um personagem e os personagens dela
  ./admin.sh senha Conta            gera uma senha temporária para a conta (quem esqueceu a senha)
  ./admin.sh log [N]                últimas N ações da equipe (padrão 20)
  ./admin.sh bugs [--todos]         relatos de bug abertos (ou todos, inclusive os resolvidos)
  ./admin.sh bug-resolvido N        marca o relato N como resolvido

Serve para criar o primeiro administrador e para emergências (ex.: desbanir com o
servidor fora do ar). Mudança de papel vale a partir do próximo login da pessoa.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from server import config as C
from server.admin import BAN_KINDS, format_until, temporary_password
from server.content import Content
from server.db import Database, hash_password


def main(argv: list[str] | None = None, db_path: Path = C.DB_PATH) -> int:
    parser = argparse.ArgumentParser(description="Administração do jogo pelo terminal.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    role = sub.add_parser("role", help="dá ou tira um papel da equipe")
    role.add_argument("name")
    role.add_argument("role", help="id do papel em data/roles.yaml, ou 'nenhum'")
    sub.add_parser("staff", help="lista a equipe")
    sub.add_parser("bans", help="lista os banidos")
    unban = sub.add_parser("unban", help="desbane um personagem (e a conta com esse nome)")
    unban.add_argument("name")
    unban_ip = sub.add_parser("unban-ip", help="desbane um endereço IP")
    unban_ip.add_argument("ip")
    account = sub.add_parser("conta", help="mostra a conta de um personagem")
    account.add_argument("name")
    password = sub.add_parser("senha", help="gera uma senha temporária para a conta")
    password.add_argument("account")
    log = sub.add_parser("log", help="últimas ações da equipe")
    log.add_argument("n", type=int, nargs="?", default=20)
    bugs = sub.add_parser("bugs", help="relatos de bug")
    bugs.add_argument("--todos", action="store_true", help="inclui os já resolvidos")
    bug_close = sub.add_parser("bug-resolvido", help="marca um relato de bug como resolvido")
    bug_close.add_argument("id", type=int)
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
        found = False
        for kind, label in BAN_KINDS.items():
            for name, until, reason, by in db.list_active(kind, time.time()):
                found = True
                print(f"[{label}] {name}: {format_until(until)} — por {by}" + (f" — {reason}" if reason else ""))
        if not found:
            print("Ninguém banido.")
    elif args.cmd == "unban":
        lifted = db.lift_sanctions(args.name, "ban") + db.lift_sanctions(args.name, "account_ban")
        if lifted == 0:
            print(f"{args.name} não está banido.", file=sys.stderr)
            return 1
        db.log_admin("terminal", "desbaniu", args.name)
        print(f"{args.name} foi desbanido.")
    elif args.cmd == "unban-ip":
        if db.lift_sanctions(args.ip, "ip_ban") == 0:
            print(f"{args.ip} não está banido.", file=sys.stderr)
            return 1
        db.log_admin("terminal", "desbaniu", args.ip, "IP")
        print(f"{args.ip} foi desbanido.")
    elif args.cmd == "conta":
        acc = db.account_of(args.name)
        if acc is None:
            print(f"Não existe personagem chamado {args.name} (ou ele não tem conta).", file=sys.stderr)
            return 1
        chars = ", ".join(c.name for c in db.list_characters(acc.id))
        print(f"Conta {acc.name}: {chars}" + (f" — último IP {acc.last_ip}" if acc.last_ip else ""))
    elif args.cmd == "senha":
        acc = db.load_account(args.account)
        if acc is None:
            print(f"Não existe conta chamada {args.account}. (Use ./admin.sh conta Personagem para achar a conta.)",
                  file=sys.stderr)
            return 1
        new_password = temporary_password()
        salt = os.urandom(16)
        db.set_account_password(acc.id, hash_password(new_password, salt), salt)
        db.log_admin("terminal", "gerou senha temporária para", acc.name)
        print(f"Senha temporária da conta {acc.name}: {new_password}")
        print("Passe só para a dona da conta; ela deve trocar a senha ao entrar (botão Conta). "
              "Se o servidor estiver rodando, já vale.")
    elif args.cmd == "bugs":
        reports = db.list_bug_reports(include_closed=args.todos, limit=200)
        for r in reversed(reports):
            stamp = time.strftime("%d/%m %H:%M", time.localtime(r["at"]))
            status = "" if r["status"] == "open" else f" [resolvido por {r['closed_by']}]"
            print(f"#{r['id']} {stamp} — {r['character']} (conta {r['account'] or '?'}) em {r['map']} "
                  f"({r['x']:.0f}, {r['y']:.0f}) {r['zone']}{status}")
            print(f"    {r['text']}")
            client = r["client"]
            if client:
                extra = [client.get("view", ""), client.get("screen", ""), client.get("agent", "")]
                print(f"    cliente: {' · '.join(e for e in extra if e)}")
                for error in client.get("errors", []):
                    print(f"    erro: {error}")
        if not reports:
            print("Nenhum relato" + ("." if args.todos else " aberto."))
    elif args.cmd == "bug-resolvido":
        if not db.close_bug_report(args.id, "terminal"):
            print(f"O relato #{args.id} não existe ou já foi resolvido.", file=sys.stderr)
            return 1
        db.log_admin("terminal", "resolveu o relato de bug", f"#{args.id}")
        print(f"Relato #{args.id} marcado como resolvido.")
    elif args.cmd == "log":
        for at, actor, action, target, details in db.recent_log(args.n):
            stamp = time.strftime("%d/%m %H:%M", time.localtime(at))
            print(f"{stamp}  {actor} {action} {target}" + (f" ({details})" if details else ""))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
