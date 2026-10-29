from template_files import template_fragment

"Importação CSV manual, isolada das coletas oficiais da API."
import csv
import io
import json
import re
import secrets
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import request, session, redirect, url_for, flash, Response
from company_scope import require_company, validate_kind

FIELDS = (
    "kit",
    "nome_antena",
    "email_original",
    "tipo_conta",
    "obra_regional",
    "plano",
    "mensalidade",
)
REQUIRED = {"kit", "email_original", "tipo_conta"}
MAX_ROWS = 2000
MAX_BYTES = 800000


def parse_csv(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("Envie um CSV não vazio de até 800 KB.")
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1252")
    except UnicodeDecodeError:
        raise ValueError(
            "Codificação não reconhecida. Envie o CSV salvo pelo Excel ou em UTF-8."
        ) from None
    if "\x00" in text:
        raise ValueError("Arquivo inválido: conteúdo binário encontrado.")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiters = (";", ",", "\t")
    delimiter = max(delimiters, key=first.count)
    reader = csv.DictReader(
        io.StringIO(text.lstrip("\r\n")), delimiter=delimiter, strict=True
    )
    headers = [str(h or "").strip().lower() for h in reader.fieldnames or []]
    if (
        len(headers) != len(set(headers))
        or not REQUIRED.issubset(headers)
        or set(headers) - set(FIELDS)
    ):
        raise ValueError(
            "Cabeçalho inválido. Use o modelo; KIT, e-mail e tipo são obrigatórios."
        )
    reader.fieldnames = headers
    rows = []
    seen = set()
    try:
        for record in reader:
            if not any((str(v or "").strip() for v in record.values())):
                continue
            if len(rows) >= MAX_ROWS:
                raise ValueError("Limite de 2.000 antenas por arquivo.")
            errors = []
            if None in record or any((v is None for v in record.values())):
                errors.append("Quantidade de colunas diferente do cabeçalho.")
            values = {k: str(record.get(k) or "").strip() for k in FIELDS}
            kit = values["kit"].upper()
            email = values["email_original"].lower()
            kind = values["tipo_conta"].upper()
            kind = "CNPJ" if kind == "PJ" else kind
            if not re.fullmatch("[A-Z0-9][A-Z0-9._-]{2,149}", kit):
                errors.append("KIT inválido (3 a 150 caracteres, sem espaços).")
            if kit in seen:
                errors.append("KIT repetido neste CSV.")
            seen.add(kit)
            if len(email) > 254 or not re.fullmatch(
                "[^@\\s]+@[^@\\s]+\\.[^@\\s]+", email
            ):
                errors.append("E-mail inválido.")
            if kind not in {"CPF", "CNPJ"}:
                errors.append("Tipo deve ser CPF, CNPJ ou PJ.")
            for field in ("nome_antena", "obra_regional", "plano"):
                if len(values[field]) > 150:
                    errors.append(field + ": máximo de 150 caracteres.")
            monthly = None
            if values["mensalidade"]:
                number = values["mensalidade"]
                if "," in number:
                    valid = re.fullmatch(
                        "(?:\\d+|\\d{1,3}(?:\\.\\d{3})+),\\d{1,2}", number
                    )
                    number = number.replace(".", "").replace(",", ".")
                else:
                    valid = re.fullmatch("\\d+(?:\\.\\d{1,2})?", number)
                try:
                    amount = Decimal(number)
                    if (
                        not valid
                        or not amount.is_finite()
                        or (not 0 <= amount <= Decimal("9999999999.99"))
                    ):
                        raise InvalidOperation()
                    monthly = str(amount.quantize(Decimal("0.01")))
                except InvalidOperation:
                    errors.append(
                        "Mensalidade inválida. Exemplo: 236,00; deixe vazio se desconhecida."
                    )
            rows.append(
                {
                    "line": reader.line_num,
                    "kit": kit,
                    "name": values["nome_antena"],
                    "email": email,
                    "kind": kind,
                    "unit": values["obra_regional"],
                    "plan": values["plano"],
                    "monthly": monthly,
                    "errors": errors,
                    "action": "Novo",
                }
            )
    except csv.Error:
        raise ValueError("CSV malformado. Confira delimitadores e aspas.") from None
    if not rows:
        raise ValueError("O arquivo não contém antenas.")
    return rows


def inspect_conflicts(conn, rows, mode, company_id):
    api_kits = set()
    api = conn.execute("SELECT payload FROM starlink_imports").fetchall()
    for record in api:
        for t in (
            record["payload"]
            .get("resources", {})
            .get("user-terminals", {})
            .get("results", [])
        ):
            kit = str(t.get("kitSerialNumber") or "").strip().upper()
            if kit:
                api_kits.add(kit)
    legacy = conn.execute("SELECT kit FROM antennas").fetchall()
    legacy_kits = {str(r["kit"]).strip().upper() for r in legacy}
    manual = conn.execute(
        "SELECT m.id,m.kit,a.company_id FROM starlink_manual_antennas m JOIN starlink_manual_accounts a ON a.id=m.account_id"
    ).fetchall()
    by_kit = {r["kit"]: r["id"] for r in manual}
    owners = {r["kit"]: r["company_id"] for r in manual}
    for r in rows:
        r["action"] = "Atualizar" if r["kit"] in by_kit else "Novo"
        if r["kit"] in owners and owners[r["kit"]] != company_id:
            r["errors"].append(
                "KIT já vinculado a outra empresa. A importação não transfere antenas."
            )
        if r["kit"] in api_kits:
            r["errors"].append(
                "KIT já coletado pela API. Nenhum dado da API será sobrescrito."
            )
        if r["kit"] in legacy_kits:
            r["errors"].append(
                "KIT já existe no cadastro manual antigo. Revise antes de importar."
            )
        if r["kit"] in by_kit and mode != "upsert":
            r["errors"].append(
                "KIT já cadastrado. Escolha atualizar existentes para alterá-lo."
            )
    return by_kit


def apply_rows(conn, rows, existing, company):
    for row in rows:
        validate_kind(row["kind"], company)
    created = updated = 0
    for r in rows:
        proposed_account = secrets.token_hex(16)
        acc = conn.execute(
            "INSERT INTO starlink_manual_accounts(id,email,account_type,company_id)\n            VALUES (%s,%s,%s,%s) ON CONFLICT(company_id,email,account_type) DO UPDATE SET email=EXCLUDED.email\n            RETURNING id",
            (proposed_account, r["email"], r["kind"], company["id"]),
        ).fetchone()["id"]
        source = "manual-csv:" + acc
        old = conn.execute(
            "SELECT id,account_id FROM starlink_manual_antennas WHERE kit=%s",
            (r["kit"],),
        ).fetchone()
        if old:
            owner = conn.execute(
                "SELECT company_id FROM starlink_manual_accounts WHERE id=%s",
                (old["account_id"],),
            ).fetchone()
            if owner["company_id"] != company["id"]:
                raise ValueError("A importação não transfere antenas entre empresas.")
            tid = "manual:" + old["id"]
            old_source = "manual-csv:" + old["account_id"]
            if source != old_source:
                for table in ("starlink_site_links", "starlink_terminal_emails"):
                    conn.execute(
                        "UPDATE "
                        + table
                        + " SET source=%s WHERE source=%s AND terminal_id=%s",
                        (source, old_source, tid),
                    )
            conn.execute(
                "UPDATE starlink_manual_antennas SET account_id=%s,name=%s,plan=%s,monthly=%s,updated_at=now()\n                WHERE id=%s",
                (acc, r["name"], r["plan"], r["monthly"], old["id"]),
            )
            updated += 1
        else:
            uid = secrets.token_hex(16)
            tid = "manual:" + uid
            conn.execute(
                "INSERT INTO starlink_manual_antennas(id,account_id,kit,name,plan,monthly)\n                VALUES (%s,%s,%s,%s,%s,%s)",
                (uid, acc, r["kit"], r["name"], r["plan"], r["monthly"]),
            )
            created += 1
        email_id = conn.execute(
            "INSERT INTO starlink_original_emails(email) VALUES (%s)\n            ON CONFLICT(email) DO UPDATE SET email=EXCLUDED.email RETURNING id",
            (r["email"],),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO starlink_terminal_emails(source,terminal_id,email_id) VALUES (%s,%s,%s)\n            ON CONFLICT(source,terminal_id) DO UPDATE SET email_id=EXCLUDED.email_id,updated_at=now()",
            (source, tid, email_id),
        )
        if r["unit"]:
            site = conn.execute(
                "SELECT id FROM sites WHERE lower(trim(name))=lower(%s) AND company_id=%s ORDER BY id LIMIT 1",
                (r["unit"], company["id"]),
            ).fetchone()
            if not site:
                site = conn.execute(
                    "INSERT INTO sites(name,cost_center,company_id) VALUES (%s,'',%s) RETURNING id",
                    (r["unit"], company["id"]),
                ).fetchone()
            conn.execute(
                "INSERT INTO starlink_site_links(source,terminal_id,site_id) VALUES (%s,%s,%s)\n                ON CONFLICT(source,terminal_id) DO UPDATE SET site_id=EXCLUDED.site_id,updated_at=now()",
                (source, tid, site["id"]),
            )
    return (created, updated)


def extend_fleet(fleet, db):
    with db() as conn:
        antennas = conn.execute(
            "SELECT m.*,a.email AS account_email,a.account_type,\n            l.site_id,s.name AS site_name,e.email AS linked_email\n            FROM starlink_manual_antennas m JOIN starlink_manual_accounts a ON a.id=m.account_id\n            LEFT JOIN starlink_site_links l ON l.source=('manual-csv:' || a.id) AND l.terminal_id=('manual:' || m.id)\n            LEFT JOIN sites s ON s.id=l.site_id\n            LEFT JOIN starlink_terminal_emails te ON te.source=('manual-csv:' || a.id) AND te.terminal_id=('manual:' || m.id)\n            LEFT JOIN starlink_original_emails e ON e.id=te.email_id\n            ORDER BY m.kit"
        ).fetchall()
    groups = {}
    for r in fleet["rows"]:
        r["data_origin"] = "API Starlink"
        r["monthly"] = None
    for a in fleet["accounts"]:
        a["manual"] = False
    for r in antennas:
        source = "manual-csv:" + r["account_id"]
        acc = groups.setdefault(
            source,
            {
                "source": source,
                "label": r["account_email"],
                "number": "Cadastro manual",
                "kind": r["account_type"],
                "collected": "Cadastro manual · sem coleta API",
                "stale": False,
                "emails": set(),
                "manual": True,
                "finance": False,
            },
        )
        if r["linked_email"]:
            acc["emails"].add(r["linked_email"])
        fleet["rows"].append(
            {
                "source": source,
                "terminal": "manual:" + r["id"],
                "account": r["account_email"],
                "number": "Cadastro manual",
                "kind": r["account_type"],
                "kit": r["kit"],
                "name": r["name"] or r["kit"],
                "serial": "Não informado",
                "line": "Não se aplica · manual",
                "unit": r["site_name"] or "Obra/regional não definida",
                "unit_key": (
                    "manual:" + str(r["site_id"]) if r["site_id"] else "pending"
                ),
                "origin": "Manual" if r["site_id"] else "Pendente",
                "site_id": r["site_id"],
                "email": r["linked_email"] or "",
                "status": "Sem informação",
                "data_origin": "Manual · CSV",
                "monthly": r["monthly"],
                "plan": r["plan"],
            }
        )
    fleet["accounts"].extend(groups.values())
    fleet["accounts"].sort(key=lambda a: (a["label"].casefold(), a["source"]))
    fleet["emails"] = sorted(
        set(fleet["emails"]) | {r["email"] for r in fleet["rows"] if r["email"]}
    )
    return fleet


UPLOAD = template_fragment("csv_import-1.html")


def register_csv(app, db, protected, page):

    @app.get("/importar-csv/modelo.csv")
    @protected
    def manual_csv_model():
        return Response(
            "\ufeff" + ";".join(FIELDS) + "\r\n",
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=starlink-modelo-manual.csv"
            },
        )

    @app.route("/importar-csv", methods=["GET", "POST"])
    @protected
    def manual_csv_import():
        rows, token, errors = ([], None, None)
        if request.method == "POST":
            company = require_company()
            action = request.form.get("action")
            if action == "preview":
                try:
                    file = request.files.get("csv_file")
                    if not file or not file.filename.lower().endswith(".csv"):
                        raise ValueError("Selecione um arquivo .csv.")
                    mode = request.form.get("mode", "create")
                    if mode not in {"create", "upsert"}:
                        raise ValueError("Modo de importação inválido.")
                    rows = parse_csv(file.read(MAX_BYTES + 1))
                    kind = request.form.get("kind", "")
                    if kind not in {"", "CPF", "CNPJ"}:
                        raise ValueError("Tipo de titular inválido.")
                    for row in rows:
                        try:
                            validate_kind(row["kind"], company)
                        except ValueError as error:
                            row["errors"].append(str(error))
                    if kind:
                        for row in rows:
                            if row["kind"] != kind:
                                row["errors"].append(
                                    "Tipo diferente do seletor " + kind + "."
                                )
                    with db() as conn:
                        inspect_conflicts(conn, rows, mode, company["id"])
                        conn.execute(
                            "DELETE FROM starlink_csv_previews WHERE expires_at < now() - interval '1 day'"
                        )
                        if not any((r["errors"] for r in rows)):
                            token = secrets.token_urlsafe(32)
                            conn.execute(
                                "INSERT INTO starlink_csv_previews(token,user_id,payload) VALUES (%s,%s,%s::jsonb)",
                                (
                                    token,
                                    session["uid"],
                                    json.dumps(
                                        {
                                            "rows": rows,
                                            "mode": mode,
                                            "company_id": company["id"],
                                        }
                                    ),
                                ),
                            )
                except ValueError as error:
                    errors = str(error)
            elif action == "confirm":
                try:
                    with db() as conn:
                        conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491520,))
                        preview = conn.execute(
                            "SELECT payload FROM starlink_csv_previews\n                            WHERE token=%s AND user_id=%s AND processed=false AND expires_at>now() FOR UPDATE",
                            (request.form.get("token", ""), session["uid"]),
                        ).fetchone()
                        if not preview:
                            raise ValueError(
                                "Prévia expirada, já aplicada ou inválida. Valide o arquivo novamente."
                            )
                        if preview["payload"].get("company_id") != company["id"]:
                            raise ValueError(
                                "A prévia pertence a outra empresa. Selecione o destino e valide novamente."
                            )
                        rows = preview["payload"]["rows"]
                        for row in rows:
                            validate_kind(row["kind"], company)
                        existing = inspect_conflicts(
                            conn, rows, preview["payload"]["mode"], company["id"]
                        )
                        if any((r["errors"] for r in rows)):
                            raise ValueError(
                                "O cadastro mudou desde a prévia. Confira os erros e valide novamente."
                            )
                        created, updated = apply_rows(conn, rows, existing, company)
                        conn.execute(
                            "UPDATE starlink_csv_previews SET processed=true WHERE token=%s",
                            (request.form["token"],),
                        )
                    flash(
                        f"Importação concluída: {created} novas antenas e {updated} atualizações. Origem: Manual · CSV."
                    )
                    return redirect(url_for("starlink_api_inventory"))
                except ValueError as error:
                    errors = str(error)
                except Exception:
                    app.logger.exception("Falha transacional na importação CSV")
                    errors = "Não foi possível concluir. A transação foi desfeita; nenhuma linha desta importação foi aplicada."
            else:
                errors = "Ação inválida."
        return page(
            UPLOAD,
            rows=rows,
            token=token,
            errors=errors,
            new_count=sum((r["action"] == "Novo" for r in rows)),
            update_count=sum((r["action"] == "Atualizar" for r in rows)),
            error_count=sum((bool(r["errors"]) for r in rows)),
        )
