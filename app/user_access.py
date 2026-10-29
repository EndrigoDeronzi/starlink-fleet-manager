from template_files import template_fragment

"Perfis e permissões de empresa. Carregados do banco em cada requisição."
import re
from flask import g, session, request, redirect, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash

ROLES = {"read": "Leitura", "write": "Leitura/Gravação", "admin": "Admin"}
ACCOUNT_POSTS = {"company_switch", "logout", "user_password"}


def allowed_company_ids():
    return getattr(g, "allowed_company_ids", set())


def can_write():
    return bool(
        getattr(g, "current_user", None)
        and g.current_user["role"] in {"write", "admin"}
    )


def permission_page(html):
    """Mantém os valores visíveis para leitura, com formulários de edição desabilitados."""
    user = getattr(g, "current_user", None)
    if not user or can_write() or request.endpoint == "user_password":
        return html

    def protect_form(m):
        attrs, body = (m[1], m[2])
        if re.search(
            'action=[\\"\\x27]/(?:empresa/selecionar|logout)[\\"\\x27]', attrs
        ):
            return m[0]
        return (
            attrs
            + '<fieldset disabled style="border:0;padding:0;margin:0;min-width:0">'
            + body
            + "</fieldset></form>"
        )

    return re.sub(
        '(<form\\b[^>]*\\bmethod\\s*=\\s*[\\"\\x27]post[\\"\\x27][^>]*>)(.*?)</form>',
        protect_form,
        html,
        flags=re.I | re.S,
    )


def validate_password(password, confirmation):
    if password != confirmation:
        raise ValueError("As senhas não conferem.")
    if not 12 <= len(password) <= 256:
        raise ValueError("A senha deve ter entre 12 e 256 caracteres.")


def register_user_access(app, db, protected, page):

    @app.before_request
    def refresh_access():
        uid = session.get("uid")
        if not uid:
            return
        with db() as conn:
            user = conn.execute(
                "SELECT id,username,display_name,role,active,is_superadmin,session_version FROM users WHERE id=%s",
                (uid,),
            ).fetchone()
            companies = conn.execute(
                "SELECT company_id FROM starlink_user_companies WHERE user_id=%s",
                (uid,),
            ).fetchall()
            if user and user["is_superadmin"]:
                companies = conn.execute(
                    "SELECT id AS company_id FROM starlink_companies"
                ).fetchall()
        if (
            not user
            or not user["active"]
            or session.get("auth_version") != user["session_version"]
        ):
            session.clear()
            return redirect("/login")
        g.current_user = user
        g.allowed_company_ids = {r["company_id"] for r in companies}
        if not g.allowed_company_ids:
            session.clear()
            return redirect("/login")
        if request.method == "POST" and request.endpoint not in ACCOUNT_POSTS:
            if user["role"] == "read":
                abort(403, "Seu perfil permite somente leitura.")
            if request.endpoint == "user_management" and user["role"] != "admin":
                abort(403, "Somente administradores podem gerenciar usuários.")

    @app.context_processor
    def user_context():
        user = getattr(g, "current_user", None)
        return {
            "access_user": user,
            "can_write": can_write(),
            "can_manage_users": bool(user and user["role"] == "admin"),
            "access_role_label": ROLES.get(user["role"], "") if user else "",
        }

    @app.route("/minha-senha", methods=["GET", "POST"])
    @protected
    def user_password():
        if request.method == "POST":
            try:
                password = request.form.get("password", "")
                validate_password(password, request.form.get("confirmation", ""))
                with db() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491521,))
                    user = conn.execute(
                        "SELECT password_hash,session_version FROM users WHERE id=%s FOR UPDATE",
                        (g.current_user["id"],),
                    ).fetchone()
                    if not check_password_hash(
                        user["password_hash"], request.form.get("current_password", "")
                    ):
                        raise ValueError("Senha atual incorreta.")
                    updated = conn.execute(
                        "UPDATE users SET password_hash=%s,session_version=session_version+1 WHERE id=%s RETURNING session_version",
                        (generate_password_hash(password), g.current_user["id"]),
                    ).fetchone()
                    conn.execute(
                        "INSERT INTO starlink_user_audit(actor_id,target_id,action) VALUES (%s,%s,'password_self')",
                        (g.current_user["id"], g.current_user["id"]),
                    )
                session["auth_version"] = updated["session_version"]
                flash("Senha alterada. As outras sessões desta conta foram encerradas.")
                return redirect("/minha-senha")
            except ValueError as error:
                flash(str(error))
        return page(PASSWORD)

    def manageable(conn, user_id, for_update=False):
        suffix = " FOR UPDATE" if for_update else ""
        user = conn.execute(
            "SELECT id,username,display_name,role,active,is_superadmin FROM users WHERE id=%s"
            + suffix,
            (user_id,),
        ).fetchone()
        if not user:
            abort(404, "Usuário não encontrado.")
        memberships = {
            r["company_id"]
            for r in conn.execute(
                "SELECT company_id FROM starlink_user_companies WHERE user_id=%s",
                (user_id,),
            ).fetchall()
        }
        if user["is_superadmin"] or (
            not g.current_user["is_superadmin"]
            and (not memberships.issubset(allowed_company_ids()))
        ):
            abort(403, "Este usuário não está no seu escopo de administração.")
        return user

    @app.route("/usuarios", methods=["GET", "POST"])
    @protected
    def user_management():
        if g.current_user["role"] != "admin":
            abort(403, "Somente administradores podem gerenciar usuários.")
        if request.method == "POST":
            try:
                action = request.form.get("action", "")
                if action not in {"create", "update", "disable", "enable"}:
                    raise ValueError("Ação inválida.")
                with db() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (73491521,))
                    actor = conn.execute(
                        "SELECT role,active,session_version,is_superadmin FROM users WHERE id=%s FOR UPDATE",
                        (g.current_user["id"],),
                    ).fetchone()
                    actual_ids = {
                        r["company_id"]
                        for r in conn.execute(
                            "SELECT company_id FROM starlink_user_companies WHERE user_id=%s",
                            (g.current_user["id"],),
                        ).fetchall()
                    }
                    if (
                        not actor
                        or not actor["active"]
                        or actor["role"] != "admin"
                        or (actor["session_version"] != session.get("auth_version"))
                    ):
                        abort(403, "Suas permissões mudaram. Entre novamente.")
                    if (
                        not actor["is_superadmin"]
                        and actual_ids != allowed_company_ids()
                    ):
                        abort(409, "Suas empresas mudaram. Atualize a página.")
                    target = None
                    if action != "create":
                        uid = int(request.form.get("id", ""))
                        if uid == g.current_user["id"]:
                            raise ValueError(
                                "Para alterar sua própria senha, utilize Minha senha. Sua conta não pode ser desativada nesta tela."
                            )
                        target = manageable(conn, uid, True)
                    if action in {"create", "update"}:
                        username = request.form.get("username", "").strip()
                        name = request.form.get("display_name", "").strip()
                        role = request.form.get("role", "")
                        requested = set(request.form.getlist("companies"))
                        if not re.fullmatch(
                            "[A-Za-z0-9][A-Za-z0-9._@+\\-]{2,99}", username
                        ):
                            raise ValueError(
                                "Usuário deve ter de 3 a 100 caracteres: letras, números, ponto, @, +, hífen ou sublinhado."
                            )
                        if len(name) > 150 or role not in ROLES:
                            raise ValueError("Informe nome e perfil válidos.")
                        if not requested or not requested.issubset(
                            allowed_company_ids()
                        ):
                            raise ValueError(
                                "Selecione pelo menos uma empresa autorizada para você."
                            )
                        password = request.form.get("password", "")
                        if action == "create" or password:
                            validate_password(
                                password, request.form.get("confirmation", "")
                            )
                        if action == "create":
                            uid = conn.execute(
                                "INSERT INTO users(username,display_name,password_hash,role) VALUES (%s,%s,%s,%s) RETURNING id",
                                (
                                    username,
                                    name or username,
                                    generate_password_hash(password),
                                    role,
                                ),
                            ).fetchone()["id"]
                        else:
                            conn.execute(
                                "UPDATE users SET username=%s,display_name=%s,role=%s,session_version=session_version+1 WHERE id=%s",
                                (username, name or username, role, uid),
                            )
                            if password:
                                conn.execute(
                                    "UPDATE users SET password_hash=%s WHERE id=%s",
                                    (generate_password_hash(password), uid),
                                )
                        conn.execute(
                            "DELETE FROM starlink_user_companies WHERE user_id=%s",
                            (uid,),
                        )
                        for cid in sorted(requested):
                            conn.execute(
                                "INSERT INTO starlink_user_companies(user_id,company_id) VALUES (%s,%s)",
                                (uid, cid),
                            )
                    else:
                        conn.execute(
                            "UPDATE users SET active=%s,session_version=session_version+1 WHERE id=%s",
                            (action == "enable", uid),
                        )
                    conn.execute(
                        "INSERT INTO starlink_user_audit(actor_id,target_id,action) VALUES (%s,%s,%s)",
                        (g.current_user["id"], uid, action),
                    )
                flash(
                    "Usuário atualizado. Alterações de acesso encerram as sessões anteriores desse usuário."
                    if action != "create"
                    else "Usuário criado."
                )
                return redirect("/usuarios")
            except ValueError as error:
                flash(str(error))
            except Exception as error:
                import psycopg

                if isinstance(error, psycopg.errors.UniqueViolation):
                    flash("Este nome de usuário já está cadastrado.")
                else:
                    raise
        with db() as conn:
            users = conn.execute(
                "SELECT id,username,display_name,role,active,is_superadmin FROM users ORDER BY lower(username)"
            ).fetchall()
            memberships = conn.execute(
                "SELECT user_id,company_id FROM starlink_user_companies"
            ).fetchall()
            options = conn.execute(
                "SELECT id,name FROM starlink_companies ORDER BY name"
            ).fetchall()
        mapping = {}
        for row in memberships:
            mapping.setdefault(row["user_id"], set()).add(row["company_id"])
        company_names = {r["id"]: r["name"] for r in options}
        visible = []
        for user in users:
            ids = mapping.get(user["id"], set())
            if not g.current_user["is_superadmin"] and (
                user["is_superadmin"] or not ids.issubset(allowed_company_ids())
            ):
                continue
            user["companies"] = ids
            user["company_names"] = [company_names[cid] for cid in sorted(ids)]
            user["editable"] = (
                not user["is_superadmin"] and user["id"] != g.current_user["id"]
            )
            visible.append(user)
        options = [o for o in options if o["id"] in allowed_company_ids()]
        return page(
            USERS, users=visible, role_labels=ROLES, user_company_options=options
        )


PASSWORD = template_fragment("user_access-1.html")
USERS = template_fragment("user_access-2.html")
