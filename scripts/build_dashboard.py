"""Gera JSON clássico importável/provisionável, sem valores de uma instalação real."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = {"type": "postgres", "uid": "fleet-postgres"}
COMPANY = (
    "('__all__' IN (${company:sqlstring}) OR company_id IN (${company:sqlstring}))"
)
FILTER = (
    COMPANY
    + " AND ('__all__' IN (${kind:sqlstring}) OR kind IN (${kind:sqlstring})) AND ('__all__' IN (${email:sqlstring}) OR email IN (${email:sqlstring}) OR ('__empty__' IN (${email:sqlstring}) AND email='')) AND ('__all__' IN (${site:sqlstring}) OR site IN (${site:sqlstring})) AND ('__all__' IN (${account:sqlstring}) OR source IN (${account:sqlstring}))"
)
SOURCES = "SELECT DISTINCT source FROM reporting.inventory WHERE " + FILTER
panels = []


def panel(title, typ, sql, x, y, w, h, description="", unit="short"):
    p = {
        "id": len(panels) + 1,
        "title": title,
        "type": typ,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "description": description,
        "datasource": DS,
        "targets": [
            {
                "refId": "A",
                "datasource": DS,
                "rawQuery": True,
                "rawSql": sql,
                "format": "time_series" if typ == "timeseries" else "table",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "noValue": (
                    "Sem coleta"
                    if "Conectividade" in title or typ == "timeseries"
                    else "—"
                ),
            },
            "overrides": [],
        },
        "options": {},
    }
    if typ == "stat":
        p["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "colorMode": "value",
            "graphMode": "none",
            "justifyMode": "auto",
        }
    if typ == "barchart":
        p["options"] = {
            "orientation": "horizontal",
            "showValue": "always",
            "xField": "Obra",
            "legend": {"showLegend": False},
        }
    if typ == "piechart":
        p["options"] = {
            "pieType": "donut",
            "displayLabels": ["value"],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "values": ["value", "percent"],
                "showLegend": True,
            },
            "reduceOptions": {"values": True, "calcs": []},
        }
    if typ == "table":
        p["options"] = {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False},
        }
    if typ == "timeseries":
        p["options"] = {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "none"},
        }
    panels.append(p)
    return p


panels.append(
    {
        "id": 1,
        "title": "Starlink Fleet Manager · DEMO",
        "type": "text",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 3},
        "options": {
            "mode": "markdown",
            "content": "**Ambiente demonstrativo — dados inteiramente fictícios.**\n\nAPI e CSV aparecem juntos. Serviço ativo é situação contratual. Online/offline exige telemetria recente; sem coletor os painéis mostram **Sem coleta**. As variáveis do Grafana filtram a visualização; não substituem as permissões por empresa da aplicação.",
        },
    }
)
for idx, (title, query, desc) in enumerate(
    [
        (
            "Antenas no filtro",
            'SELECT count(*) AS "Antenas" FROM reporting.inventory WHERE ' + FILTER,
            "Inventário atual; não é limitado pelo intervalo de datas.",
        ),
        (
            "CNPJ",
            'SELECT count(*) AS "CNPJ" FROM reporting.inventory WHERE '
            + FILTER
            + " AND kind='CNPJ'",
            "",
        ),
        (
            "CPF",
            'SELECT count(*) AS "CPF" FROM reporting.inventory WHERE '
            + FILTER
            + " AND kind='CPF'",
            "",
        ),
        (
            "Serviços ativos",
            'SELECT count(*) AS "Ativos" FROM reporting.inventory WHERE '
            + FILTER
            + " AND service_status='Ativo'",
            "Ativo não significa online.",
        ),
        (
            "Sem obra",
            'SELECT count(*) AS "Pendentes" FROM reporting.inventory WHERE '
            + FILTER
            + " AND site='Sem obra'",
            "",
        ),
        (
            "Conectividade · online",
            "SELECT CASE WHEN count(*) FILTER(WHERE c.online IS NOT NULL)=0 THEN NULL ELSE count(*) FILTER(WHERE c.online=true) END AS online FROM reporting.connectivity c JOIN reporting.inventory v USING(company_id,source,terminal_id,kit) WHERE "
            + FILTER,
            "Sem coleta fresca não é contado como offline.",
        ),
        (
            "Conectividade · offline",
            "SELECT CASE WHEN count(*) FILTER(WHERE c.online IS NOT NULL)=0 THEN NULL ELSE count(*) FILTER(WHERE c.online=false) END AS offline FROM reporting.connectivity c JOIN reporting.inventory v USING(company_id,source,terminal_id,kit) WHERE "
            + FILTER,
            "Amostras com mais de cinco minutos viram Sem coleta.",
        ),
        (
            "Integrações desatualizadas",
            'SELECT count(*) AS "Integrações" FROM reporting.integrations WHERE stale AND source IN ('
            + SOURCES
            + ")",
            "Último sucesso há mais de 30 minutos. O demo não se atualiza sozinho.",
        ),
    ]
):
    # Evita company_id ambíguo no join de conectividade.
    if "reporting.connectivity c JOIN" in query:
        query = query.replace(
            "JOIN reporting.inventory v USING(company_id,source,terminal_id,kit)",
            "JOIN (SELECT * FROM reporting.inventory WHERE "
            + FILTER
            + ") v USING(company_id,source,terminal_id,kit)",
        ).replace(" WHERE " + FILTER, " WHERE " + FILTER, 1)
        # Filtro já aplicado na subconsulta; remova o segundo WHERE externo.
        query = query.rsplit(" WHERE " + FILTER, 1)[0]
    panel(title, "stat", query, idx * 3, 3, 3, 4, desc)
panel(
    "Antenas por obra / regional",
    "barchart",
    'SELECT site AS "Obra",count(*) AS "Antenas" FROM reporting.inventory WHERE '
    + FILTER
    + " GROUP BY site ORDER BY count(*) DESC,site",
    0,
    7,
    9,
    8,
)
panel(
    "Titularidade",
    "piechart",
    'SELECT kind AS "Tipo",count(*) AS "Antenas" FROM reporting.inventory WHERE '
    + FILTER
    + " GROUP BY kind ORDER BY kind",
    9,
    7,
    6,
    8,
)
panel(
    "Atualização das integrações API",
    "table",
    'SELECT company_name AS "Empresa",source AS "Conta",terminals AS "Antenas",service_lines AS "Linhas",last_success AS "Último sucesso",CASE WHEN stale THEN \'Desatualizada\' ELSE \'Recente\' END AS "Coleta" FROM reporting.integrations WHERE source IN ('
    + SOURCES
    + ") ORDER BY company_name,source",
    15,
    7,
    9,
    8,
)
finance = "source IN (" + SOURCES + ") AND currency IN (${currency:sqlstring})"
panel(
    "Faturado no período · ${currency}",
    "stat",
    'SELECT sum(amount) AS "Valor" FROM reporting.invoices WHERE '
    + finance
    + " AND $__timeFilter(invoice_date)",
    0,
    15,
    8,
    4,
    "Valor das faturas emitidas no período; não comprova pagamento. Uma moeda por vez.",
    "currencyBRL",
)
panel(
    "Saldo devido API · ${currency}",
    "stat",
    'SELECT sum(due_amount) AS "Saldo atual" FROM reporting.balances WHERE ' + finance,
    8,
    15,
    8,
    4,
    "Snapshot atual da conta; não é mensalidade nem soma de faturas. Não é limitado pelo período.",
    "currencyBRL",
)
panel(
    "Previsão mensal manual · BRL",
    "stat",
    'SELECT sum(monthly) AS "Previsão BRL" FROM reporting.inventory WHERE '
    + FILTER
    + " AND origin<>'API'",
    16,
    15,
    8,
    4,
    "Somente valor cadastrado manualmente em BRL. Não representa emissão ou pagamento de fatura.",
    "currencyBRL",
)
for title, field, unit, x in [
    ("Latência · coleta futura", "latency_ms", "ms", 0),
    ("Perda de pacotes · coleta futura", "loss_percent", "percent", 12),
]:
    panel(
        title,
        "timeseries",
        "SELECT $__timeGroupAlias(t.collected_at,$__interval),avg(t."
        + field
        + ') AS "'
        + title.split(" · ")[0]
        + '" FROM reporting.telemetry_samples t WHERE t.source IN ('
        + SOURCES
        + ") AND $__timeFilter(t.collected_at) GROUP BY 1 ORDER BY 1",
        x,
        19,
        12,
        8,
        "Nenhum coletor de telemetria está incluído. Sem amostras, o gráfico fica vazio.",
        unit,
    )
panel(
    "Inventário consolidado",
    "table",
    'SELECT v.company_name AS "Empresa",v.kit AS "KIT",v.name AS "Nome",v.site AS "Obra / regional",v.email AS "E-mail",v.kind AS "Titular",v.origin AS "Origem",v.service_status AS "Serviço",CASE c.online WHEN true THEN \'Online\' WHEN false THEN \'Offline\' ELSE \'Sem coleta\' END AS "Conectividade",v.updated_at AS "Última atualização" FROM (SELECT * FROM reporting.inventory WHERE '
    + FILTER
    + ") v LEFT JOIN reporting.connectivity c USING(company_id,source,terminal_id,kit) ORDER BY v.company_name,v.site,v.kit",
    0,
    27,
    24,
    10,
)
panel(
    "Faturas API · período e moeda selecionados",
    "table",
    'SELECT company_name AS "Empresa",account AS "Conta",invoice_id AS "Fatura",invoice_date AS "Emissão",due_date AS "Vencimento",currency AS "Moeda",amount AS "Valor",due_amount AS "Saldo devido",status AS "Situação" FROM reporting.invoices WHERE '
    + finance
    + " AND $__timeFilter(invoice_date) ORDER BY invoice_date DESC,invoice_id",
    0,
    37,
    24,
    10,
    "E-mail, obra e titularidade selecionam contas; exibe a fatura inteira, sem rateio por antena.",
)


def variable(name, label, query, all=True, multi=True):
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "query": query,
        "definition": query,
        "refresh": 1,
        "sort": 1,
        "hide": 0,
        "multi": multi,
        "includeAll": all,
        "allValue": "__all__" if all else None,
        "current": {
            "selected": True,
            "text": "Todos" if all else "BRL",
            "value": "$__all" if all else "BRL",
        },
        "options": [],
    }


variables = [
    variable(
        "company",
        "Empresa",
        "SELECT DISTINCT company_name AS __text,company_id AS __value FROM reporting.inventory ORDER BY 1",
    ),
    variable(
        "kind",
        "Titular",
        "SELECT DISTINCT kind AS __text,kind AS __value FROM reporting.inventory WHERE "
        + COMPANY
        + " ORDER BY 1",
    ),
    variable(
        "email",
        "E-mail",
        "SELECT DISTINCT COALESCE(NULLIF(email,''),'Sem e-mail') AS __text,COALESCE(NULLIF(email,''),'__empty__') AS __value FROM reporting.inventory WHERE "
        + COMPANY
        + " ORDER BY 1",
    ),
    variable(
        "site",
        "Obra / regional",
        "SELECT DISTINCT site AS __text,site AS __value FROM reporting.inventory WHERE "
        + COMPANY
        + " ORDER BY 1",
    ),
    variable(
        "account",
        "Conta",
        "SELECT source AS __value,company_name||' · '||COALESCE(NULLIF(string_agg(DISTINCT email,', ' ORDER BY email),''),source) AS __text FROM reporting.inventory WHERE "
        + COMPANY
        + " GROUP BY company_name,source ORDER BY 2",
    ),
    variable(
        "currency",
        "Moeda",
        "SELECT DISTINCT currency AS __text,currency AS __value FROM reporting.invoices WHERE currency IS NOT NULL ORDER BY 1",
        False,
        False,
    ),
]
# Os valores monetários dos painéis usam formato genérico: a moeda selecionada fica no título.
for p in panels:
    if "${currency}" in p.get("title", ""):
        p["fieldConfig"]["defaults"]["unit"] = "locale"
dashboard = {
    "id": None,
    "uid": "starlink-fleet-demo",
    "title": "Starlink Fleet · Gestão e conectividade",
    "tags": ["demo", "starlink", "postgresql", "fleet"],
    "timezone": "browser",
    "schemaVersion": 38,
    "version": 1,
    "editable": False,
    "graphTooltip": 1,
    "refresh": "1m",
    "time": {"from": "now-90d", "to": "now"},
    "timepicker": {},
    "templating": {"list": variables},
    "annotations": {"list": []},
    "panels": panels,
    "description": "Projeto demonstrativo. Cadastros sintéticos. Telemetria futura; situação contratual não comprova conectividade.",
}
if __name__ == "__main__":
    path = ROOT / "grafana/dashboards/fleet.json"
    path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
    print("Dashboard gerado:", len(panels), "painéis")
