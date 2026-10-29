"""Dados inventados, identificados como DEMO. Não consulta serviços externos."""

import json
from datetime import datetime, timezone, timedelta


def seed_demo(conn):
    if conn.execute(
        "SELECT 1 FROM starlink_schema_versions WHERE version='demo-v1'"
    ).fetchone():
        return
    now = datetime.now(timezone.utc)
    for company, count in [("alfa", 3), ("beta", 4)]:
        cid = "empresa-" + company
        source = "demo-" + company
        email = company + ".ops@example.invalid"
        terminals = []
        services = []
        site = conn.execute(
            "INSERT INTO sites(name,company_id) VALUES (%s,%s) RETURNING id",
            ("Obra Demo " + company.upper(), cid),
        ).fetchone()[0]
        for number in range(1, count + 1):
            tid = f"demo-{company}-{number:03d}"
            line = f"DEMO-SL-{company.upper()}-{number:03d}"
            terminals.append(
                {
                    "userTerminalId": tid,
                    "kitSerialNumber": f"DEMO-{company.upper()}-{number:03d}",
                    "dishSerialNumber": f"DEMO-SERIE-{company.upper()}-{number:03d}",
                    "serviceLineNumber": line,
                }
            )
            services.append(
                {
                    "serviceLineNumber": line,
                    "nickname": "Obra Demo " + company.upper(),
                    "active": number != count,
                }
            )
        invoices = []
        for month in range(3):
            date = now - timedelta(days=month * 30)
            invoices.append(
                {
                    "invoiceId": f"DEMO-FAT-{company.upper()}-{month+1}",
                    "description": "Fatura fictícia de demonstração",
                    "currency": "BRL",
                    "amount": round(count * 210.50, 2),
                    "dueAmount": round(count * 210.50, 2) if month == 0 else 0,
                    "invoiceDate": date.isoformat(),
                    "dueDate": (date + timedelta(days=10)).isoformat(),
                    "status": "Open" if month == 0 else "Paid",
                }
            )
        payload = {
            "collected_at": now.strftime("%Y%m%dT%H%M%S%fZ"),
            "demo": True,
            "resources": {
                "account": {
                    "accountName": "Conta Demo " + company.upper(),
                    "accountNumber": "DEMO-ACCOUNT-" + company.upper(),
                },
                "user-terminals": {"results": terminals},
                "service-lines": {"results": services},
                "billing/invoices": {"results": invoices},
                "billing/balance": {
                    "balances": [
                        {"currency": "BRL", "dueAmount": round(count * 210.50, 2)}
                    ]
                },
            },
        }
        conn.execute(
            "INSERT INTO starlink_imports(source,payload,account_type,company_id) VALUES (%s,%s::jsonb,%s,%s)",
            (source, json.dumps(payload), "CNPJ", cid),
        )
        email_id = conn.execute(
            "INSERT INTO starlink_original_emails(email) VALUES (%s) RETURNING id",
            (email,),
        ).fetchone()[0]
        for terminal in terminals:
            tid = terminal["userTerminalId"]
            conn.execute(
                "INSERT INTO starlink_site_links(source,terminal_id,site_id) VALUES (%s,%s,%s)",
                (source, tid, site),
            )
            conn.execute(
                "INSERT INTO starlink_terminal_emails(source,terminal_id,email_id) VALUES (%s,%s,%s)",
                (source, tid, email_id),
            )
    for number, company, kind, monthly in [
        (1, "alfa", "CPF", "199.90"),
        (2, "alfa", "CPF", "249.90"),
        (3, "beta", "CNPJ", "310.00"),
    ]:
        aid = f"demo-manual-{number}"
        mid = f"demo-terminal-{number}"
        email = f"manual{number}@example.invalid"
        source = "manual-csv:" + aid
        conn.execute(
            "INSERT INTO starlink_manual_accounts(id,email,account_type,company_id) VALUES (%s,%s,%s,%s)",
            (aid, email, kind, "empresa-" + company),
        )
        conn.execute(
            "INSERT INTO starlink_manual_antennas(id,account_id,kit,name,plan,monthly) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                mid,
                aid,
                f"DEMO-MANUAL-{number:03d}",
                "Antena Manual Demo " + str(number),
                "Plano informado · demonstração",
                monthly,
            ),
        )
        email_id = conn.execute(
            "INSERT INTO starlink_original_emails(email) VALUES (%s) RETURNING id",
            (email,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO starlink_terminal_emails(source,terminal_id,email_id) VALUES (%s,%s,%s)",
            (source, "manual:" + mid, email_id),
        )
    conn.execute("INSERT INTO starlink_schema_versions(version) VALUES ('demo-v1')")
