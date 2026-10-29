import ast
import io
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "scripts"), str(ROOT / "tests")]
import psycopg
from psycopg.rows import dict_row
from db_engine import Engine, Connection
from bootstrap import initialize
from csv_import import parse_csv
import sync_starlink

PASSWORD = "demo-test-password-only"


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        cls.owner = Connection(cls.engine)
        initialize(
            cls.owner,
            "fleet_app_test",
            PASSWORD,
            "fleet_report_test",
            PASSWORD,
            "admin",
            PASSWORD,
            demo=True,
        )
        cls.patch = patch("psycopg.connect", side_effect=cls.engine.connect)
        cls.patch.start()
        os.environ.update(
            APP_SECRET="test-only-local-session-secret",
            POSTGRES_DB="test",
            POSTGRES_USER="fleet_app_test",
            POSTGRES_PASSWORD=PASSWORD,
            DEMO_MODE="true",
        )
        import app

        cls.target = app
        app.app.config["TESTING"] = True
        cls.admin = cls.owner.execute(
            "SELECT id FROM users WHERE username='admin'"
        ).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.engine.close()

    def setUp(self):
        self.client = self.target.app.test_client()
        with self.client.session_transaction() as s:
            s.update(
                uid=self.admin,
                auth_version=1,
                csrf="csrf-test",
                company_id="empresa-alfa",
            )

    def post(self, path, **data):
        with self.client.session_transaction() as s:
            cid = s.get("company_id", "empresa-alfa")
        return self.client.post(
            path, data={"csrf": "csrf-test", "company_context": cid, **data}
        )

    def create_user(self, name, role, companies):
        r = self.post(
            "/usuarios",
            action="create",
            username=name,
            display_name=name,
            role=role,
            companies=companies,
            password=PASSWORD,
            confirmation=PASSWORD,
        )
        self.assertEqual(r.status_code, 302)
        return self.owner.execute(
            "SELECT id FROM users WHERE username=%s", (name,)
        ).fetchone()[0]

    def signin(self, uid, cid=None):
        version = self.owner.execute(
            "SELECT session_version FROM users WHERE id=%s", (uid,)
        ).fetchone()[0]
        with self.client.session_transaction() as s:
            s.clear()
            s.update(uid=uid, auth_version=version, csrf="csrf-test")
            if cid:
                s["company_id"] = cid

    def test_01_demo_schema_and_views(self):
        self.assertEqual(
            self.owner.execute("SELECT count(*) FROM reporting.inventory").fetchone()[
                0
            ],
            10,
        )
        self.assertEqual(
            self.owner.execute("SELECT count(*) FROM reporting.invoices").fetchone()[0],
            6,
        )
        self.assertEqual(
            self.owner.execute(
                "SELECT count(*) FROM reporting.telemetry_samples"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.owner.execute(
                "SELECT count(*) FROM reporting.connectivity WHERE online IS NOT NULL"
            ).fetchone()[0],
            0,
        )

    def test_02_all_pages(self):
        for cid in ["empresa-alfa", "empresa-beta", "__all__"]:
            with self.client.session_transaction() as s:
                s["company_id"] = cid
            for path in [
                "/",
                "/starlink",
                "/financeiro",
                "/sites",
                "/accounts",
                "/antennas",
                "/vinculos",
                "/emails-antenas",
                "/importar-csv",
                "/usuarios",
                "/minha-senha",
            ]:
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, (cid, path))
                self.assertIn("dados fictícios", r.text)

    def test_03_dashboard_filters(self):
        self.assertNotIn('class="dashboard-more" open', self.client.get("/").text)
        self.assertIn(
            'class="dashboard-more" open',
            self.client.get("/?email=alfa.ops@example.invalid").text,
        )
        with self.client.session_transaction() as s:
            s["company_id"] = "empresa-beta"
        text = self.client.get("/").text
        self.assertIn('dashboard-kind-fixed">CNPJ', text)
        self.assertNotIn('value="CPF"', text)

    def test_04_company_data_isolation(self):
        text = self.client.get("/starlink").text
        self.assertIn("DEMO-ALFA-001", text)
        self.assertNotIn("DEMO-BETA-001", text)
        with self.client.session_transaction() as s:
            s["company_id"] = "empresa-beta"
        self.assertNotIn(
            "alfa.ops@example.invalid", self.client.get("/emails-antenas").text
        )
        self.assertNotIn("DEMO-FAT-ALFA", self.client.get("/financeiro").text)

    def test_05_read_role_enforced(self):
        uid = self.create_user("reader", "read", ["empresa-beta"])
        self.signin(uid, "empresa-beta")
        self.assertEqual(self.client.get("/starlink").status_code, 200)
        for path in [
            "/sites",
            "/accounts",
            "/antennas",
            "/importar-csv",
            "/vinculos",
            "/emails-antenas",
            "/usuarios",
        ]:
            self.assertEqual(
                self.post(path, action="create", name="Blocked").status_code, 403, path
            )
        self.assertEqual(self.client.get("/usuarios").status_code, 403)
        self.assertEqual(
            self.post("/empresa/selecionar", company_id="empresa-alfa").status_code, 400
        )
        self.post("/empresa/selecionar", company_id="__all__")
        self.assertNotIn("DEMO-ALFA-001", self.client.get("/starlink").text)

    def test_06_write_role_no_user_admin(self):
        uid = self.create_user("writer", "write", ["empresa-beta"])
        self.signin(uid, "empresa-beta")
        self.assertEqual(
            self.post("/sites", name="Obra Teste Operador").status_code, 302
        )
        self.assertEqual(self.client.get("/usuarios").status_code, 403)
        self.assertEqual(
            self.owner.execute(
                "SELECT company_id FROM sites WHERE name='Obra Teste Operador'"
            ).fetchone()[0],
            "empresa-beta",
        )

    def test_07_admin_no_escalation(self):
        uid = self.create_user("scopedadmin", "admin", ["empresa-beta"])
        self.signin(uid, "empresa-beta")
        self.assertEqual(self.client.get("/usuarios").status_code, 200)
        r = self.post(
            "/usuarios",
            action="create",
            username="forbidden-company",
            role="admin",
            companies=["empresa-alfa"],
            password=PASSWORD,
            confirmation=PASSWORD,
        )
        self.assertIn("empresa autorizada", r.text)
        self.assertEqual(
            self.post("/usuarios", action="disable", id=self.admin).status_code, 403
        )

    def test_08_disable_revokes_sessions(self):
        uid = self.create_user("disabled", "read", ["empresa-alfa"])
        other = self.target.app.test_client()
        with other.session_transaction() as s:
            s.update(uid=uid, auth_version=1, csrf="x", company_id="empresa-alfa")
        self.post("/usuarios", action="disable", id=uid)
        self.assertEqual(other.get("/").location, "/login")

    def test_09_stale_company_form(self):
        self.client.get("/")
        with self.client.session_transaction() as s:
            s["company_id"] = "empresa-beta"
        r = self.client.post(
            "/sites",
            data={
                "csrf": "csrf-test",
                "company_context": "empresa-alfa",
                "name": "Invalid",
            },
        )
        self.assertEqual(r.status_code, 409)

    def test_10_cpf_company_rule(self):
        with self.assertRaises(psycopg.IntegrityError):
            self.owner.execute(
                "INSERT INTO starlink_manual_accounts(id,email,account_type,company_id) VALUES ('invalid-cpf','invalid@example.invalid','CPF','empresa-beta')"
            )

    def test_11_csv_preview_confirmation(self):
        raw = b"kit;nome_antena;email_original;tipo_conta;obra_regional;plano;mensalidade\nDEMO-TEST-CSV;Demo;demo.csv@example.invalid;CPF;Obra Teste;Plano;219,90\n"
        r = self.post(
            "/importar-csv",
            action="preview",
            kind="CPF",
            mode="create",
            csv_file=(io.BytesIO(raw), "demo.csv"),
        )
        token = re.search('name="token" value="([^"]+)"', r.text)
        self.assertIsNotNone(token)
        self.assertEqual(
            self.post("/importar-csv", action="confirm", token=token[1]).status_code,
            302,
        )
        self.assertIn("DEMO-TEST-CSV", self.client.get("/starlink").text)
        self.assertIn(
            "já aplicada",
            self.post("/importar-csv", action="confirm", token=token[1]).text,
        )

    def test_12_csv_encoding(self):
        rows = parse_csv(
            "kit;email_original;tipo_conta;nome_antena\nDEMO-ENC;demo@example.invalid;CPF;Antena São\n".encode(
                "cp1252"
            )
        )
        self.assertEqual(rows[0]["name"], "Antena São")
        self.assertEqual(rows[0]["errors"], [])

    def test_13_readonly_db_grants(self):
        with self.engine.connect(user="fleet_report_test") as conn:
            self.assertGreater(
                conn.execute("SELECT count(*) FROM reporting.inventory").fetchone()[0],
                0,
            )
        for query in [
            "SELECT password_hash FROM users",
            "DELETE FROM reporting.telemetry_samples",
            "UPDATE reporting.integrations SET company_name='wrong'",
            "CREATE TABLE reporting.should_fail(id int)",
        ]:
            with self.assertRaises(psycopg.Error):
                with self.engine.connect(user="fleet_report_test") as conn:
                    conn.execute(query)
        with self.assertRaises(psycopg.Error):
            with self.engine.connect(user="fleet_app_test") as conn:
                conn.execute("CREATE TABLE should_fail(id int)")

    def test_14_dashboard_queries(self):
        dashboard = json.loads((ROOT / "grafana/dashboards/fleet.json").read_text())
        for p in dashboard["panels"]:
            for t in p.get("targets", []):
                query = t["rawSql"]
                for key in ["company", "kind", "email", "site", "account"]:
                    query = query.replace("${" + key + ":sqlstring}", "'__all__'")
                query = query.replace("${currency:sqlstring}", "'BRL'")
                query = re.sub(
                    r"\$__timeFilter\(([^)]+)\)",
                    r"\1 BETWEEN now()-interval '90 days' AND now()",
                    query,
                )
                query = re.sub(
                    r"\$__timeGroupAlias\(([^,]+),\$__interval\)",
                    r"date_trunc('minute',\1) AS time",
                    query,
                )
                with self.engine.connect(user="fleet_report_test") as conn:
                    result = conn.execute(query).fetchall()
                if "Conectividade · " in p["title"]:
                    self.assertIsNone(result[0][0])
        for v in dashboard["templating"]["list"]:
            query = v["query"].replace("${company:sqlstring}", "'__all__'")
            with self.engine.connect(user="fleet_report_test") as conn:
                conn.execute(query).fetchall()

    def test_15_sync_pagination(self):
        pages = [
            {
                "pageIndex": 0,
                "totalCount": 2,
                "results": [{"id": "a"}],
                "isLastPage": False,
            },
            {
                "pageIndex": 1,
                "totalCount": 2,
                "results": [{"id": "b"}],
                "isLastPage": True,
            },
        ]
        with patch.object(sync_starlink, "content_for", side_effect=pages):
            self.assertEqual(
                len(
                    sync_starlink.collect_pages("demo", "id", "not-a-token")["results"]
                ),
                2,
            )
        pages[1]["results"] = [{"id": "a"}]
        with patch.object(
            sync_starlink, "content_for", side_effect=pages
        ), self.assertRaises(ValueError):
            sync_starlink.collect_pages("demo", "id", "not-a-token")

    def test_16_publication_scanner(self):
        from check_publication import violations

        self.assertTrue(
            violations([".env", "secrets/customer.json", "backups/export.sql"])
        )
        self.assertEqual(
            violations([".env.example", "config/starlink-accounts.example.json"]), []
        )

    def test_17_connectivity_freshness(self):
        for source, tid, online, age in [
            ("demo-alfa", "demo-alfa-001", True, "1 minute"),
            ("demo-beta", "demo-beta-001", False, "1 minute"),
            ("demo-beta", "demo-beta-002", True, "10 minutes"),
        ]:
            self.owner.execute(
                "INSERT INTO reporting.telemetry_samples(company_id,source,terminal_id,collected_at,online) VALUES (%s,%s,%s,now()-(%s)::interval,%s)",
                (
                    "empresa-alfa" if source == "demo-alfa" else "empresa-beta",
                    source,
                    tid,
                    age,
                    online,
                ),
            )
        self.assertEqual(
            self.owner.execute(
                "SELECT count(*) FROM reporting.connectivity WHERE online=true"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.owner.execute(
                "SELECT count(*) FROM reporting.connectivity WHERE online=false"
            ).fetchone()[0],
            1,
        )
        self.assertIsNone(
            self.owner.execute(
                "SELECT online FROM reporting.connectivity WHERE terminal_id='demo-beta-002'"
            ).fetchone()[0]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
