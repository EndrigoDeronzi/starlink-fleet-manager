from template_files import template_fragment
from user_access import permission_page, register_user_access
from company_scope import guard_page, scope_fleet, register_companies
import os
import secrets
from datetime import timedelta
from functools import wraps
import psycopg
from psycopg.rows import dict_row
from flask import (
    Flask,
    request,
    session,
    redirect,
    url_for,
    render_template_string,
    flash,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ["APP_SECRET"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=1024 * 1024,
)
app.config.update(
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()
    == "true"
)


@app.context_processor
def deployment_context():
    return {"demo_mode": os.environ.get("DEMO_MODE", "true").lower() == "true"}


def db():
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
    )


def protected(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


@app.before_request
def csrf_guard():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(32)
    if request.method == "POST":
        if not secrets.compare_digest(session["csrf"], request.form.get("csrf", "")):
            return ("Sessão expirada. Atualize a página e tente novamente.", 400)


BASE = template_fragment("app-1.html")


def page(content, **kwargs):
    return permission_page(
        guard_page(render_template_string(BASE + content + "</main></html>", **kwargs))
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE lower(username)=lower(%s)",
                (request.form.get("username", ""),),
            ).fetchone()
        if (
            user
            and user["active"]
            and check_password_hash(
                user["password_hash"], request.form.get("password", "")
            )
        ):
            session.clear()
            session["uid"] = user["id"]
            session["auth_version"] = user["session_version"]
            session["csrf"] = secrets.token_hex(32)
            session.permanent = True
            return redirect("/")
        flash("Usuário ou senha inválidos.")
    return page(template_fragment("app-2.html"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


def money(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
import re


def display_date(value, collected=False):
    if not value:
        return "Não informada"
    try:
        if collected:
            dt = datetime.strptime(value, "%Y%m%dT%H%M%S%fZ").replace(
                tzinfo=timezone.utc
            )
            return dt.astimezone(ZoneInfo("America/Sao_Paulo")).strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        return datetime.fromisoformat(str(value)[:10]).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return str(value)


def load_fleet():
    with db() as conn:
        imports = conn.execute(
            "SELECT source,payload,account_type FROM starlink_imports ORDER BY source"
        ).fetchall()
        links = conn.execute(
            "SELECT l.source,l.terminal_id,l.site_id,s.name\n            FROM starlink_site_links l JOIN sites s ON s.id=l.site_id"
        ).fetchall()
        email_links = conn.execute(
            "SELECT l.source,l.terminal_id,e.email\n            FROM starlink_terminal_emails l JOIN starlink_original_emails e ON e.id=l.email_id"
        ).fetchall()
        sites_list = conn.execute("SELECT id,name FROM sites ORDER BY name").fetchall()
        emails = conn.execute(
            "SELECT email FROM starlink_original_emails ORDER BY email"
        ).fetchall()
    link_map = {(x["source"], x["terminal_id"]): x for x in links}
    email_map = {(x["source"], x["terminal_id"]): x["email"] for x in email_links}
    rows, accounts_list, invoices, balances = ([], [], [], [])
    for record in imports:
        source = record["source"]
        payload = record["payload"]
        resources = payload.get("resources", {})
        account = resources.get("account", {})
        label = account.get("accountName") or source
        collected = payload.get("collected_at")
        try:
            dt = datetime.strptime(collected, "%Y%m%dT%H%M%S%fZ").replace(
                tzinfo=timezone.utc
            )
            stale = (datetime.now(timezone.utc) - dt).total_seconds() > 1800
        except (ValueError, TypeError):
            stale = True
        acc = {
            "source": source,
            "label": label,
            "number": account.get("accountNumber") or source,
            "collected": display_date(collected, True),
            "stale": stale,
            "kind": record.get("account_type") or "Não informado",
            "emails": set(),
            "finance": "billing/invoices" in resources,
        }
        services = {
            s["serviceLineNumber"]: s
            for s in resources.get("service-lines", {}).get("results", [])
        }
        for terminal in resources.get("user-terminals", {}).get("results", []):
            tid = terminal["userTerminalId"]
            key = (source, tid)
            service = services.get(terminal.get("serviceLineNumber"), {})
            kit = terminal.get("kitSerialNumber") or tid
            nickname = str(
                service.get("nickname") or terminal.get("nickname") or ""
            ).strip()
            link = link_map.get(key)
            usable = bool(nickname) and nickname.casefold() != kit.casefold()
            if link:
                unit, unit_key, origin = (
                    link["name"],
                    "manual:" + str(link["site_id"]),
                    "Manual",
                )
            elif usable:
                unit, unit_key, origin = (nickname, "starlink:" + nickname, "Starlink")
            else:
                unit, unit_key, origin = (
                    "Obra/regional não definida",
                    "pending",
                    "Pendente",
                )
            email = email_map.get(key, "")
            if email:
                acc["emails"].add(email)
            active = service.get("active")
            rows.append(
                {
                    "source": source,
                    "terminal": tid,
                    "account": label,
                    "number": acc["number"],
                    "kind": acc["kind"],
                    "kit": kit,
                    "name": nickname or "Sem nome",
                    "serial": terminal.get("dishSerialNumber") or "Não informado",
                    "line": terminal.get("serviceLineNumber") or "Sem vínculo",
                    "unit": unit,
                    "unit_key": unit_key,
                    "origin": origin,
                    "site_id": link["site_id"] if link else None,
                    "email": email,
                    "status": (
                        "Ativo"
                        if active is True
                        else "Inativo" if active is False else "Sem informação"
                    ),
                }
            )
        for item in resources.get("billing/invoices", {}).get("results", []):
            invoices.append(
                {**item, "source": source, "account": label, "number": acc["number"]}
            )
        for item in resources.get("billing/balance", {}).get("balances", []):
            balances.append({**item, "source": source, "account": label})
        accounts_list.append(acc)
    return {
        "rows": rows,
        "accounts": accounts_list,
        "sites": sites_list,
        "emails": [e["email"] for e in emails],
        "invoices": invoices,
        "balances": balances,
    }


def filter_rows(rows):
    q = request.args.get("q", "").strip().casefold()
    email = request.args.get("email", "").strip().lower()
    source = request.args.get("source", "")
    unit = request.args.get("unit", "")
    status = request.args.get("status", "")
    return [
        r
        for r in rows
        if (not source or r["source"] == source)
        and (
            not email
            or (email == "__empty__" and (not r["email"]))
            or r["email"] == email
        )
        and (not unit or r["unit_key"] == unit)
        and (not status or r["status"] == status)
        and (not q or q in " ".join((str(v) for v in r.values())).casefold())
    ]


FLEET_FILTERS = template_fragment("app-6.html")


def fleet_page(content, fleet, **kwargs):
    units = {r["unit_key"]: r["unit"] + " · " + r["origin"] for r in fleet["rows"]}
    return page(
        content,
        fleet=fleet,
        unit_options=sorted(units.items(), key=lambda x: x[1].casefold()),
        **kwargs,
    )


DASHBOARD_FILTERS_V3 = template_fragment("app-7.html")


@app.get("/")
@protected
def dashboard():
    fleet = load_fleet()
    rows = filter_rows(fleet["rows"])
    groups = {}
    for r in rows:
        group = groups.setdefault(
            r["unit_key"],
            {
                "key": r["unit_key"],
                "unit": r["unit"],
                "origin": r["origin"],
                "emails": set(),
                "count": 0,
            },
        )
        group["count"] += 1
        if r["email"]:
            group["emails"].add(r["email"])
    return fleet_page(
        "\n<section class=\"panel\" style=\"background:#008651;color:white\"><h1>Suas Starlinks em uma só visão.</h1><p>Contas {{'CNPJ' if company_current and not company_current.allow_cpf else 'CNPJ e CPF'}} · inventário por obra e e-mail.</p></section>\n"
        + DASHBOARD_FILTERS_V3
        + template_fragment("app-8.html"),
        fleet,
        rows=rows,
        groups=sorted(
            groups.values(), key=lambda g: (-g["count"], g["unit"].casefold())
        ),
        active=sum((r["status"] == "Ativo" for r in rows)),
        pending=sum((r["origin"] == "Pendente" for r in rows)),
        account_count=len({r["source"] for r in rows}),
    )


@app.get("/starlink")
@protected
def starlink_api_inventory():
    fleet = load_fleet()
    rows = sorted(
        filter_rows(fleet["rows"]), key=lambda r: (r["unit"], r["email"], r["kit"])
    )
    return fleet_page(
        '<h1>Antenas · visão geral</h1><p class="muted">Todas as contas por padrão. Serviço ativo não significa antena online.</p>'
        + FLEET_FILTERS
        + template_fragment("app-9.html"),
        fleet,
        rows=rows,
    )


@app.route("/vinculos", methods=["GET", "POST"])
@protected
def starlink_site_assignments():
    fleet = load_fleet()
    if request.method == "POST":
        source, tid = (
            request.form.get("source", ""),
            request.form.get("terminal_id", ""),
        )
        site = request.form.get("site_id", "")
        if not any(
            (r["source"] == source and r["terminal"] == tid for r in fleet["rows"])
        ):
            return ("Antena inválida.", 400)
        if site and site not in {str(s["id"]) for s in fleet["sites"]}:
            return ("Unidade inválida.", 400)
        with db() as conn:
            if site:
                conn.execute(
                    "INSERT INTO starlink_site_links(source,terminal_id,site_id)\n                    VALUES (%s,%s,%s) ON CONFLICT(source,terminal_id)\n                    DO UPDATE SET site_id=EXCLUDED.site_id,updated_at=now()",
                    (source, tid, int(site)),
                )
            else:
                conn.execute(
                    "DELETE FROM starlink_site_links WHERE source=%s AND terminal_id=%s",
                    (source, tid),
                )
        flash("Vínculo da antena atualizado. As demais antenas foram preservadas.")
        return redirect(url_for("starlink_site_assignments"))
    rows = sorted(filter_rows(fleet["rows"]), key=lambda r: (r["unit"], r["kit"]))
    return fleet_page(
        '<h1>Obras das antenas</h1><p>Associação manual tem prioridade sobre o nome da Starlink.</p><p><a href="/sites">Cadastrar ou alterar obras e regionais →</a></p>'
        + FLEET_FILTERS
        + template_fragment("app-10.html"),
        fleet,
        rows=rows,
    )


@app.route("/emails-antenas", methods=["GET", "POST"])
@protected
def starlink_antenna_emails():
    fleet = load_fleet()
    if request.method == "POST":
        action = request.form.get("action", "")
        source, tid = (
            request.form.get("source", ""),
            request.form.get("terminal_id", ""),
        )
        email = request.form.get("email", "").strip().lower()
        identities = {(r["source"], r["terminal"]) for r in fleet["rows"]}
        if action not in {"save", "remove", "fill_empty"}:
            return ("Ação inválida.", 400)
        if action != "fill_empty" and (source, tid) not in identities:
            return ("Antena inválida.", 400)
        if action == "fill_empty" and source not in {
            a["source"] for a in fleet["accounts"]
        }:
            return ("Selecione uma conta de destino para o preenchimento em lote.", 400)
        if action != "remove" and (
            len(email) > 254 or not re.fullmatch("[^@\\s]+@[^@\\s]+\\.[^@\\s]+", email)
        ):
            return ("E-mail inválido.", 400)
        with db() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491520,))
            if action == "remove":
                conn.execute(
                    "DELETE FROM starlink_terminal_emails WHERE source=%s AND terminal_id=%s",
                    (source, tid),
                )
                count = 1
            else:
                email_id = conn.execute(
                    "INSERT INTO starlink_original_emails(email) VALUES (%s)\n                    ON CONFLICT(email) DO UPDATE SET email=EXCLUDED.email RETURNING id",
                    (email,),
                ).fetchone()["id"]
                targets = (
                    [(source, tid)]
                    if action == "save"
                    else [(s, t) for s, t in sorted(identities) if s == source]
                )
                count = 0
                for s, t in targets:
                    conflict = (
                        "DO UPDATE SET email_id=EXCLUDED.email_id,updated_at=now()"
                        if action == "save"
                        else "DO NOTHING"
                    )
                    result = conn.execute(
                        "INSERT INTO starlink_terminal_emails(source,terminal_id,email_id)\n                        VALUES (%s,%s,%s) ON CONFLICT(source,terminal_id) "
                        + conflict,
                        (s, t, email_id),
                    )
                    count += result.rowcount
        flash(
            f"Operação concluída: {count} vínculo(s). As demais antenas foram preservadas."
        )
        return redirect(url_for("starlink_antenna_emails"))
    rows = sorted(
        filter_rows(fleet["rows"]), key=lambda r: (r["email"], r["name"], r["kit"])
    )
    return fleet_page(
        '<h1>E-mails das antenas</h1><p class="muted">E-mail original por antena · acesso de gestão separado.</p>'
        + FLEET_FILTERS
        + template_fragment("app-11.html"),
        fleet,
        rows=rows,
    )


@app.get("/financeiro")
@protected
def starlink_finance():
    fleet = load_fleet()
    source = request.args.get("source", "")
    email = request.args.get("email", "").strip().lower()
    q = request.args.get("q", "").strip().casefold()
    accounts_list = _csv_filter_accounts(
        [
            a
            for a in fleet["accounts"]
            if (not source or a["source"] == source)
            and (not email or email in a["emails"])
        ]
    )
    sources = {a["source"] for a in accounts_list}
    invoices = [
        i
        for i in fleet["invoices"]
        if i["source"] in sources
        and (
            not q
            or q
            in " ".join(
                (
                    str(i.get(k) or "")
                    for k in ("invoiceId", "description", "status", "account")
                )
            ).casefold()
        )
    ]
    invoices.sort(key=lambda i: str(i.get("invoiceDate") or ""), reverse=True)
    balances = [b for b in fleet["balances"] if b["source"] in sources]

    def amount(value):
        if value is None:
            return "Não informado"
        try:
            return money(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError):
            return "Não informado"

    return page(
        template_fragment("app-12.html"),
        fleet=fleet,
        accounts=accounts_list,
        invoices=invoices,
        balances=balances,
        amount=amount,
        date=display_date,
    )


from csv_import import register_csv, extend_fleet

_api_fleet_before_csv = load_fleet
_filter_rows_before_csv = filter_rows


def load_fleet():
    return extend_fleet(_api_fleet_before_csv(), db)


def filter_rows(rows):
    kind = request.args.get("kind", "").strip().upper()
    return [r for r in _filter_rows_before_csv(rows) if not kind or r["kind"] == kind]


def _csv_filter_accounts(accounts_list):
    kind = request.args.get("kind", "").strip().upper()
    return [a for a in accounts_list if not kind or a["kind"] == kind]


register_csv(app, lambda: db(), protected, page)
_fleet_before_companies = load_fleet


def load_fleet():
    return scope_fleet(_fleet_before_companies(), db)


register_user_access(app, db, protected, page)
register_companies(app, db, protected, page)
