"""Coletor opcional da API oficial. Modo demo nunca faz chamadas à Starlink."""

import fcntl
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
import psycopg

BASE = "https://www.starlink.com/api"


class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url, token=None, data=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    with opener.open(
        urllib.request.Request(url, data=data, headers=headers), timeout=30
    ) as response:
        return json.load(response)


def content_for(resource, token, page=None):
    suffix = "" if page is None else "?page=" + str(page)
    result = read_json(BASE + "/public/v2/" + resource + suffix, token)
    if result.get("isValid") is not True or result.get("errors"):
        raise ValueError("Resposta inválida")
    return result["content"]


def collect_pages(resource, identifier, token):
    rows = []
    seen = set()
    expected_total = None
    for page in range(1000):
        content = content_for(resource, token, page)
        if content.get("pageIndex") != page:
            raise ValueError("Página inesperada")
        total = content["totalCount"]
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError("Total mudou durante a coleta")
        batch = content["results"]
        for item in batch:
            key = item[identifier]
            if not key or key in seen:
                raise ValueError("Identificador ausente ou duplicado")
            seen.add(key)
            rows.append(item)
        if content.get("isLastPage") is True:
            break
        if not batch:
            raise ValueError("Página vazia antes do fim")
    else:
        raise ValueError("Limite de páginas excedido")
    if len(rows) != expected_total:
        raise ValueError("Coleta incompleta")
    return {
        "results": rows,
        "totalCount": expected_total,
        "pageIndex": 0,
        "limit": len(rows),
        "isLastPage": True,
    }


def save_snapshot(conn, source, email, kind, company_id, payload):
    terminals = payload["resources"]["user-terminals"]["results"]
    services = payload["resources"]["service-lines"]["results"]
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491520,))
    previous = conn.execute(
        "SELECT payload,company_id FROM starlink_imports WHERE source=%s", (source,)
    ).fetchone()
    company = conn.execute(
        "SELECT allow_cpf FROM starlink_companies WHERE id=%s", (company_id,)
    ).fetchone()
    if not company or (kind == "CPF" and (not company[0])):
        raise ValueError("Empresa/titularidade inválida")
    if previous and previous[1] != company_id:
        raise ValueError("Fonte já pertence a outra empresa")
    previous_ids = {
        t["userTerminalId"]
        for t in (
            previous[0]
            .get("resources", {})
            .get("user-terminals", {})
            .get("results", [])
            if previous
            else []
        )
    }
    conn.execute(
        "\n        INSERT INTO starlink_imports(source,payload,account_type,company_id)\n        VALUES (%s,%s::jsonb,%s,%s)\n        ON CONFLICT(source)\n        DO UPDATE SET\n            payload=EXCLUDED.payload,\n            account_type=EXCLUDED.account_type,\n            imported_at=now()\n    ",
        (source, json.dumps(payload), kind, company_id),
    )
    conn.execute(
        "\n        INSERT INTO starlink_sync_runs(source,terminals,active_services)\n        VALUES (%s,%s,%s)\n    ",
        (source, len(terminals), sum((s.get("active") is True for s in services))),
    )
    email_id = conn.execute(
        "\n        INSERT INTO starlink_original_emails(email)\n        VALUES (%s)\n        ON CONFLICT(email) DO UPDATE SET email=EXCLUDED.email\n        RETURNING id\n    ",
        (email,),
    ).fetchone()[0]
    for terminal in terminals:
        if terminal["userTerminalId"] in previous_ids:
            continue
        conn.execute(
            "\n            INSERT INTO starlink_terminal_emails(source,terminal_id,email_id)\n            VALUES (%s,%s,%s)\n            ON CONFLICT(source,terminal_id) DO NOTHING\n        ",
            (source, terminal["userTerminalId"], email_id),
        )


opener = urllib.request.build_opener(NoRedirect)


def configured_accounts(path):
    configured = json.loads(path.read_text())
    if not isinstance(configured, list) or not configured:
        raise ValueError(
            "Configure pelo menos uma integração em config/starlink-accounts.json."
        )
    sources, files = set(), set()
    for item in configured:
        source = item.get("source", "")
        filename = item.get("credentials", "")
        email = item.get("original_email", "").strip().lower()
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", source)
            or source in sources
            or not filename
            or Path(filename).name != filename
            or filename in files
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            or item.get("account_type") not in {"CNPJ", "CPF"}
            or not item.get("company_id")
        ):
            raise ValueError("Configuração inválida ou duplicada.")
        sources.add(source)
        files.add(filename)
        item["original_email"] = email
    return configured


def main():
    from datetime import datetime, timezone

    if os.environ.get("DEMO_MODE", "true").lower() == "true":
        raise SystemExit(
            "DEMO_MODE ativo: coleta externa desabilitada. Use uma base limpa para dados reais."
        )
    config_path = Path(
        os.environ.get("STARLINK_ACCOUNTS_FILE", "/run/config/starlink-accounts.json")
    )
    secret_dir = Path(os.environ.get("STARLINK_SECRETS_DIR", "/run/starlink-secrets"))
    try:
        accounts = configured_accounts(config_path)
    except (OSError, ValueError, TypeError):
        raise SystemExit("Configuração de integrações ausente ou inválida.")
    failed = False
    with open("/tmp/fleet-sync.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Uma sincronização já está em execução.")
        for item in accounts:
            stage = "credenciais"
            try:
                credentials = json.loads((secret_dir / item["credentials"]).read_text())
                stage = "autenticação"
                auth = read_json(
                    BASE + "/auth/connect/token",
                    data=urllib.parse.urlencode(
                        {
                            "grant_type": "client_credentials",
                            "client_id": credentials["client_id"],
                            "client_secret": credentials["client_secret"],
                        }
                    ).encode(),
                )
                token = auth["access_token"]
                resources = {}
                for resource, identifier in [
                    ("user-terminals", "userTerminalId"),
                    ("service-lines", "serviceLineNumber"),
                    ("billing/invoices", "invoiceId"),
                ]:
                    stage = resource
                    resources[resource] = collect_pages(resource, identifier, token)
                for resource in ["account", "billing/balance"]:
                    stage = resource
                    resources[resource] = content_for(resource, token)
                payload = {
                    "collected_at": datetime.now(timezone.utc).strftime(
                        "%Y%m%dT%H%M%S%fZ"
                    ),
                    "resources": resources,
                }
                stage = "banco"
                with psycopg.connect(
                    host=os.environ.get("POSTGRES_HOST", "db"),
                    dbname=os.environ["POSTGRES_DB"],
                    user=os.environ["POSTGRES_USER"],
                    password=os.environ["POSTGRES_PASSWORD"],
                ) as conn:
                    # Serializa diferentes containers e a importação CSV.
                    save_snapshot(
                        conn,
                        item["source"],
                        item["original_email"],
                        item["account_type"],
                        item["company_id"],
                        payload,
                    )
                print(
                    "SINCRONIZAÇÃO OK:",
                    item["source"],
                    "| empresa:",
                    item["company_id"],
                    "| antenas:",
                    len(resources["user-terminals"]["results"]),
                )
            except Exception as error:
                failed = True
                code = (
                    (" HTTP " + str(error.code))
                    if isinstance(error, urllib.error.HTTPError)
                    else ""
                )
                print(
                    "FALHA:",
                    item["source"],
                    "| etapa:",
                    stage,
                    "| tipo:",
                    type(error).__name__,
                    code,
                    "| última coleta preservada.",
                    file=sys.stderr,
                )
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
