"""Bootstrap idempotente; executado somente pelo serviço init com o dono do banco."""

import os
from pathlib import Path
import psycopg
from psycopg import sql
from werkzeug.security import generate_password_hash
from seed_demo import seed_demo

ROOT = Path(__file__).resolve().parent.parent


def initialize(
    conn,
    app_user,
    app_password,
    report_user,
    report_password,
    admin_username,
    admin_password,
    demo=True,
):
    if len(admin_password) < 12:
        raise ValueError("Senha inicial deve ter ao menos 12 caracteres.")
    conn.execute((ROOT / "db/schema.sql").read_text())
    conn.execute((ROOT / "db/reporting.sql").read_text())
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(73491520)")
        for cid, name, allow in [
            ("empresa-alfa", "Empresa Alfa", True),
            ("empresa-beta", "Empresa Beta", False),
        ]:
            conn.execute(
                "INSERT INTO starlink_companies(id,name,allow_cpf) VALUES (%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                (cid, name, allow),
            )
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            uid = conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,is_superadmin) VALUES (%s,%s,%s,%s,true) RETURNING id",
                (
                    admin_username,
                    "Administrador",
                    generate_password_hash(admin_password),
                    "admin",
                ),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO starlink_user_companies(user_id,company_id) SELECT %s,id FROM starlink_companies",
                (uid,),
            )
        has_demo = bool(
            conn.execute(
                "SELECT 1 FROM starlink_schema_versions WHERE version='demo-v1'"
            ).fetchone()
        )
        if not demo and has_demo:
            raise ValueError(
                "Base contém demonstração. Crie uma base separada antes de usar dados reais."
            )
        if demo:
            seed_demo(conn)
        for role, password in [
            (app_user, app_password),
            (report_user, report_password),
        ]:
            if not role or len(role) > 63:
                raise ValueError("Nome de papel inválido.")
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
        database = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(
            sql.SQL("REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )
        for role in (app_user, report_user):
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(app_user)
            )
        )
        conn.execute(
            sql.SQL(
                "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(sql.Identifier(app_user))
        )
        conn.execute(
            sql.SQL(
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(sql.Identifier(app_user))
        )
        conn.execute("REVOKE ALL ON SCHEMA reporting FROM PUBLIC")
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA reporting TO {}").format(
                sql.Identifier(report_user)
            )
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO {}").format(
                sql.Identifier(report_user)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(
                sql.Identifier(report_user)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET statement_timeout=10000").format(
                sql.Identifier(report_user)
            )
        )


def main():
    with psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "db"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    ) as conn:
        owner = conn.execute("SELECT current_user").fetchone()[0]
        if len({owner, os.environ["APP_DB_USER"], os.environ["GRAFANA_DB_USER"]}) != 3:
            raise ValueError("Dono, aplicação e Grafana devem usar papéis distintos.")
        initialize(
            conn,
            os.environ["APP_DB_USER"],
            os.environ["APP_DB_PASSWORD"],
            os.environ["GRAFANA_DB_USER"],
            os.environ["GRAFANA_DB_PASSWORD"],
            os.environ.get("INITIAL_ADMIN_USERNAME", "admin"),
            os.environ["INITIAL_ADMIN_PASSWORD"],
            os.environ.get("DEMO_MODE", "true").lower() == "true",
        )
    print(
        "Bootstrap concluído. Dados demo habilitados."
        if os.environ.get("DEMO_MODE", "true").lower() == "true"
        else "Bootstrap concluído."
    )


if __name__ == "__main__":
    main()
