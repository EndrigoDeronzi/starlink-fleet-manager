"""Verificação ponta a ponta do Compose; usa somente o ambiente demo local."""

import base64
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env_file():
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in (ROOT / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def wait(url):
    for attempt in range(60):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    raise SystemExit("Serviço não ficou disponível: " + url)


def main():
    env = env_file()
    web = "http://127.0.0.1:" + env.get("WEB_PORT", "8080")
    grafana = "http://127.0.0.1:" + env.get("GRAFANA_PORT", "3000")
    wait(web + "/login")
    wait(grafana + "/api/health")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    with opener.open(web + "/login") as r:
        html = r.read().decode()
    token = re.search(r'name="csrf" value="([^"]+)"', html)[1]
    request = urllib.request.Request(
        web + "/login",
        data=urllib.parse.urlencode(
            {
                "csrf": token,
                "username": env.get("INITIAL_ADMIN_USERNAME", "admin"),
                "password": env["INITIAL_ADMIN_PASSWORD"],
            }
        ).encode(),
    )
    with opener.open(request) as r:
        html = r.read().decode()
        assert "DEMONSTRAÇÃO" in html and "dashboard-filter-main" in html
    credentials = base64.b64encode(
        (
            env.get("GRAFANA_ADMIN_USER", "admin") + ":" + env["GRAFANA_ADMIN_PASSWORD"]
        ).encode()
    ).decode()

    def api(path, data=None):
        request = urllib.request.Request(
            grafana + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Basic " + credentials,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as r:
            return json.load(r)

    dashboard = api("/api/dashboards/uid/starlink-fleet-demo")
    assert len(dashboard["dashboard"]["panels"]) == 19, "Painéis não provisionados"
    result = api(
        "/api/ds/query",
        {
            "from": "now-90d",
            "to": "now",
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": "fleet-postgres", "type": "postgres"},
                    "rawSql": "SELECT count(*) AS antennas FROM reporting.inventory",
                    "format": "table",
                }
            ],
        },
    )
    assert not result["results"]["A"].get("error"), "Consulta do Grafana falhou"
    assert (
        result["results"]["A"]["frames"][0]["data"]["values"][0][0] == 10
    ), "Demo inesperado"
    print("Smoke OK: login, dashboard web, provisioning Grafana e consulta PostgreSQL.")


if __name__ == "__main__":
    main()
