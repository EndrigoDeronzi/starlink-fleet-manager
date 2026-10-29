"""PostgreSQL efêmero para testes. Não lê ou modifica a instalação do usuário."""

import json
import os
import re
import subprocess
from pathlib import Path
import psycopg
from psycopg.rows import dict_row


class Cursor:
    def __init__(self, result, dicts):
        self.rows = result.get("rows", [])
        if not dicts:
            self.rows = [tuple(r.values()) for r in self.rows]
        self.rowcount = result.get("affectedRows", len(self.rows)) or len(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN")
        return self

    def __exit__(self, kind, value, tb):
        self.conn.execute("ROLLBACK" if kind else "COMMIT")


class Connection:
    def __init__(self, engine, dicts=False, role=None):
        self.engine = engine
        self.dicts = dicts
        self.role = role

    def __enter__(self):
        if self.role:
            self.execute(
                psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(self.role))
            )
        self.execute("BEGIN")
        return self

    def __exit__(self, kind, value, tb):
        self.execute("ROLLBACK" if kind else "COMMIT")
        self.execute("RESET ROLE")

    def transaction(self):
        return Transaction(self)

    def execute(self, text, args=()):
        if hasattr(text, "as_string"):
            text = text.as_string()
        return Cursor(self.engine.query(text, args), self.dicts)


class Engine:
    def __init__(self):
        package = os.environ.get("PGLITE_MODULE", "@electric-sql/pglite")
        self.proc = subprocess.Popen(
            ["node", str(Path(__file__).with_name("pglite_bridge.mjs")), package],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def query(self, text, args=()):
        indexes = iter(range(1, 10000))
        text = re.sub(r"%s", lambda _: "$" + str(next(indexes)), text)
        script = not args and (
            text.strip().startswith("BEGIN;") or text.strip().startswith("--")
        )
        self.proc.stdin.write(
            json.dumps(
                {"sql": text, "params": list(args), "script": script}, default=str
            )
            + "\n"
        )
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("PostgreSQL de teste encerrou inesperadamente")
        result = json.loads(line)
        if not result["ok"]:
            cls = (
                psycopg.errors.lookup(result["code"])
                if result.get("code")
                else psycopg.DatabaseError
            )
            raise cls(result["error"])
        data = result["result"]
        if isinstance(data, list):
            data = data[-1] if data else {}
        return data

    def connect(self, **kw):
        role = kw.get("user")
        role = None if role in {None, "postgres"} else role
        return Connection(self, dicts=kw.get("row_factory") == dict_row, role=role)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=20)
