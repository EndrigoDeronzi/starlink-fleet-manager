from template_files import template_fragment

"Seleção limitada às empresas autorizadas para o usuário autenticado."
import re
from decimal import Decimal, InvalidOperation
from flask import g, session, request, redirect, flash, abort
from markupsafe import escape
from user_access import allowed_company_ids

ALL = "__all__"


def current_company():
    return g.starlink_company


def require_company():
    company = current_company()
    if company is None:
        abort(400, "Selecione uma empresa para cadastrar ou alterar dados.")
    return company


def validate_kind(kind, company):
    if kind not in {"CPF", "CNPJ", "PJ"}:
        raise ValueError("Tipo de titular inválido.")
    if kind == "CPF" and (not company["allow_cpf"]):
        raise ValueError("Esta empresa permite somente CNPJ.")


def scope_fleet(fleet, db):
    with db() as conn:
        owners = {
            r["source"]: r["company_id"]
            for r in conn.execute(
                "SELECT source,company_id FROM starlink_imports"
            ).fetchall()
        }
        owners.update(
            {
                "manual-csv:" + r["id"]: r["company_id"]
                for r in conn.execute(
                    "SELECT id,company_id FROM starlink_manual_accounts"
                ).fetchall()
            }
        )
        companies = {
            r["id"]: r["name"]
            for r in conn.execute("SELECT id,name FROM starlink_companies").fetchall()
        }
        sites = conn.execute(
            "SELECT id,name,company_id FROM sites ORDER BY name"
        ).fetchall()
    company = current_company()
    cid = company["id"] if company else None
    for key in ("rows", "accounts", "invoices", "balances"):
        selected = []
        for row in fleet[key]:
            owner = owners.get(row["source"])
            if (
                owner not in companies
                or owner not in allowed_company_ids()
                or (cid and owner != cid)
            ):
                continue
            row["company_id"], row["company_name"] = (owner, companies[owner])
            if not cid:
                label_key = "label" if key == "accounts" else "account"
                row[label_key] += " · " + companies[owner]
            if key == "rows" and row["unit_key"].startswith("starlink:"):
                row["unit_key"] = owner + ":" + row["unit_key"]
            selected.append(row)
        fleet[key] = selected
    fleet["sites"] = [
        s
        for s in sites
        if s["company_id"] in allowed_company_ids()
        and (not cid or s["company_id"] == cid)
    ]
    fleet["emails"] = sorted({r["email"] for r in fleet["rows"] if r["email"]})
    return fleet


def guard_page(html):
    if not session.get("uid"):
        return html
    company = current_company()
    context = company["id"] if company else ALL
    token = (
        '<input type="hidden" name="company_context" value="'
        + str(escape(context))
        + '">'
    )
    return re.sub(
        '(<form\\b[^>]*\\bmethod\\s*=\\s*[\\"\\x27]post[\\"\\x27][^>]*>)',
        lambda m: m[0] + token,
        html,
        flags=re.I,
    )


def register_companies(app, db, protected, page):

    @app.before_request
    def company_request_scope():
        if not session.get("uid"):
            return
        with db() as conn:
            options = conn.execute(
                "SELECT id,name,allow_cpf FROM starlink_companies ORDER BY CASE WHEN id='empresa-alfa' THEN 0 ELSE 1 END,name"
            ).fetchall()
        options = [c for c in options if c["id"] in allowed_company_ids()]
        if not options:
            abort(403, "Nenhuma empresa autorizada.")
        choices = {c["id"]: c for c in options}
        selected = session.get("company_id")
        if selected is None:
            selected = options[0]["id"]
            session["company_id"] = selected
        elif selected not in choices and selected != ALL:
            if request.method == "POST":
                abort(403, "Você não tem acesso a esta empresa.")
            session["company_id"] = options[0]["id"]
            return redirect("/")
        g.starlink_company = choices.get(selected)
        g.starlink_companies = options
        if request.method == "POST" and request.endpoint not in {
            "company_switch",
            "logout",
        }:
            if request.form.get("company_context") != selected:
                abort(
                    409,
                    "A empresa selecionada mudou ou o formulário é antigo. Atualize a página antes de salvar.",
                )
            if request.endpoint not in {"user_management", "user_password"}:
                require_company()

    @app.context_processor
    def company_context():
        return {
            "company_current": getattr(g, "starlink_company", None),
            "company_options": getattr(g, "starlink_companies", []),
        }

    @app.post("/empresa/selecionar")
    @protected
    def company_switch():
        selected = request.form.get("company_id", "")
        valid = {c["id"] for c in g.starlink_companies} | {ALL}
        if selected not in valid:
            abort(400, "Empresa inválida.")
        session["company_id"] = selected
        return redirect("/")

    def company_filter(alias=""):
        company = current_company()
        return (
            (" WHERE " + alias + "company_id=%s", (company["id"],))
            if company
            else (
                " WHERE " + alias + "company_id=ANY(%s)",
                (sorted(allowed_company_ids()),),
            )
        )

    @app.route("/accounts", methods=["GET", "POST"], endpoint="accounts")
    @protected
    def accounts_view():
        if request.method == "POST":
            company = require_company()
            name, kind = (
                request.form.get("name", "").strip(),
                request.form.get("kind", ""),
            )
            try:
                validate_kind(kind, company)
                if not name or len(name) > 150:
                    raise ValueError("Informe um nome válido de até 150 caracteres.")
                with db() as conn:
                    conn.execute(
                        "INSERT INTO accounts(name,kind,email,company_id) VALUES (%s,%s,%s,%s)",
                        (
                            name,
                            "PJ" if kind == "CNPJ" else kind,
                            request.form.get("email", "").strip(),
                            company["id"],
                        ),
                    )
                flash("Conta cadastrada em " + company["name"] + ".")
            except ValueError as error:
                flash(str(error))
            return redirect("/accounts")
        where, args = company_filter("a.")
        with db() as conn:
            rows = conn.execute(
                "SELECT a.*,c.name AS company_name FROM accounts a JOIN starlink_companies c ON c.id=a.company_id"
                + where
                + " ORDER BY a.name",
                args,
            ).fetchall()
        return page(ACCOUNTS, rows=rows)

    @app.route("/sites", methods=["GET", "POST"], endpoint="sites")
    @protected
    def sites_view():
        if request.method == "POST":
            company = require_company()
            action = request.form.get("action", "create")
            name = request.form.get("name", "").strip()
            try:
                if action not in {"create", "update", "delete"} or (
                    action != "delete" and (not name or len(name) > 150)
                ):
                    raise ValueError("Informe ação e nome válidos.")
                with db() as conn:
                    if action == "create":
                        conn.execute(
                            "INSERT INTO sites(name,cost_center,company_id) VALUES (%s,'',%s)",
                            (name, company["id"]),
                        )
                    else:
                        sid = int(request.form.get("id", ""))
                        if action == "update":
                            result = conn.execute(
                                "UPDATE sites SET name=%s WHERE id=%s AND company_id=%s",
                                (name, sid, company["id"]),
                            )
                        else:
                            used = conn.execute(
                                "SELECT 1 FROM starlink_site_links WHERE site_id=%s UNION ALL SELECT 1 FROM antennas WHERE site_id=%s LIMIT 1",
                                (sid, sid),
                            ).fetchone()
                            if used:
                                raise ValueError(
                                    "Esta unidade possui antenas vinculadas. Reassocie antes de excluir."
                                )
                            result = conn.execute(
                                "DELETE FROM sites WHERE id=%s AND company_id=%s",
                                (sid, company["id"]),
                            )
                        if result.rowcount != 1:
                            raise ValueError(
                                "Unidade não encontrada na empresa selecionada."
                            )
                flash("Obra/regional atualizada em " + company["name"] + ".")
            except ValueError as error:
                flash(str(error))
            return redirect("/sites")
        where, args = company_filter("s.")
        with db() as conn:
            rows = conn.execute(
                "SELECT s.*,c.name AS company_name FROM sites s JOIN starlink_companies c ON c.id=s.company_id"
                + where
                + " ORDER BY s.name",
                args,
            ).fetchall()
        return page(SITES, rows=rows)

    @app.route("/antennas", methods=["GET", "POST"], endpoint="antennas")
    @protected
    def antennas_view():
        if request.method == "POST":
            company = require_company()
            try:
                aid, sid = (
                    int(request.form["account_id"]),
                    int(request.form["site_id"]),
                )
                kit = request.form.get("kit", "").strip().upper()
                value = Decimal(request.form.get("monthly", "0").replace(",", "."))
                if (
                    not re.fullmatch("[A-Z0-9][A-Z0-9._-]{2,149}", kit)
                    or not value.is_finite()
                    or (not 0 <= value <= Decimal("9999999999.99"))
                ):
                    raise ValueError("Confira KIT e mensalidade.")
                with db() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491520,))
                    a = conn.execute(
                        "SELECT id FROM accounts WHERE id=%s AND company_id=%s",
                        (aid, company["id"]),
                    ).fetchone()
                    s = conn.execute(
                        "SELECT id FROM sites WHERE id=%s AND company_id=%s",
                        (sid, company["id"]),
                    ).fetchone()
                    if not a or not s:
                        raise ValueError(
                            "Conta e obra devem pertencer à empresa selecionada."
                        )
                    duplicate = conn.execute(
                        "SELECT 1 FROM starlink_manual_antennas WHERE upper(kit)=%s UNION ALL SELECT 1 FROM starlink_imports i CROSS JOIN LATERAL jsonb_array_elements(COALESCE(i.payload->'resources'->'user-terminals'->'results','[]'::jsonb)) t WHERE upper(t->>'kitSerialNumber')=%s LIMIT 1",
                        (kit, kit),
                    ).fetchone()
                    if duplicate:
                        raise ValueError(
                            "KIT já cadastrado em outra origem. Confira o inventário."
                        )
                    conn.execute(
                        "INSERT INTO antennas(kit,account_id,site_id,plan,monthly) VALUES (%s,%s,%s,%s,%s)",
                        (kit, aid, sid, request.form.get("plan", "").strip(), value),
                    )
                flash("Antena cadastrada.")
            except (ValueError, InvalidOperation, KeyError) as error:
                flash(str(error) or "Confira os campos.")
            except Exception as error:
                import psycopg

                if not isinstance(error, psycopg.IntegrityError):
                    raise
                flash("Não foi possível cadastrar. Confira duplicidades e vínculos.")
            return redirect("/antennas")
        where, args = company_filter("a.")
        site_where, site_args = company_filter()
        with db() as conn:
            accounts = conn.execute(
                "SELECT a.* FROM accounts a" + where + " ORDER BY a.name", args
            ).fetchall()
            sites = conn.execute(
                "SELECT * FROM sites" + site_where + " ORDER BY name", site_args
            ).fetchall()
            rows = conn.execute(
                "SELECT t.*,a.name AS account_name,s.name AS site_name,c.name AS company_name FROM antennas t JOIN accounts a ON a.id=t.account_id JOIN sites s ON s.id=t.site_id JOIN starlink_companies c ON c.id=a.company_id"
                + where
                + " ORDER BY t.id DESC",
                args,
            ).fetchall()
        return page(ANTENNAS, rows=rows, accounts=accounts, sites=sites)


ACCOUNTS = template_fragment("company_scope-1.html")
SITES = template_fragment("company_scope-2.html")
ANTENNAS = template_fragment("company_scope-3.html")
