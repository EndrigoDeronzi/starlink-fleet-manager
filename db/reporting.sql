BEGIN;
CREATE SCHEMA IF NOT EXISTS reporting;
-- Visões sem credenciais, hashes de senha, tokens ou dados de usuários.
CREATE OR REPLACE VIEW reporting.inventory AS
SELECT i.company_id,c.name AS company_name,i.source,t->>'userTerminalId' AS terminal_id,
 COALESCE(t->>'kitSerialNumber',t->>'userTerminalId') AS kit,
 COALESCE(NULLIF(btrim(s->>'nickname'),''),NULLIF(btrim(t->>'nickname'),''),'Sem nome') AS name,
 COALESCE(si.name,NULLIF(btrim(s->>'nickname'),''),NULLIF(btrim(t->>'nickname'),''),'Sem obra') AS site,
 COALESCE(e.email,'') AS email,i.account_type AS kind,'API'::text AS origin,
 CASE WHEN s->>'active'='true' THEN 'Ativo' WHEN s->>'active'='false' THEN 'Inativo' ELSE 'Sem informação' END AS service_status,
 NULL::text AS plan,NULL::numeric AS monthly,i.imported_at AS updated_at
FROM starlink_imports i JOIN starlink_companies c ON c.id=i.company_id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(i.payload->'resources'->'user-terminals'->'results','[]'::jsonb)) t
 LEFT JOIN LATERAL (SELECT value AS s FROM jsonb_array_elements(COALESCE(i.payload->'resources'->'service-lines'->'results','[]'::jsonb))
  WHERE value->>'serviceLineNumber'=t->>'serviceLineNumber' LIMIT 1) sl ON true
 LEFT JOIN starlink_site_links l ON l.source=i.source AND l.terminal_id=t->>'userTerminalId'
 LEFT JOIN sites si ON si.id=l.site_id
 LEFT JOIN starlink_terminal_emails te ON te.source=i.source AND te.terminal_id=t->>'userTerminalId'
 LEFT JOIN starlink_original_emails e ON e.id=te.email_id
UNION ALL
SELECT a.company_id,c.name,'manual-csv:'||a.id,'manual:'||m.id,m.kit,COALESCE(NULLIF(m.name,''),m.kit),
 COALESCE(si.name,'Sem obra'),COALESCE(e.email,''),a.account_type,'CSV','Sem informação',m.plan,m.monthly,m.updated_at
FROM starlink_manual_antennas m JOIN starlink_manual_accounts a ON a.id=m.account_id
 JOIN starlink_companies c ON c.id=a.company_id
 LEFT JOIN starlink_site_links l ON l.source='manual-csv:'||a.id AND l.terminal_id='manual:'||m.id
 LEFT JOIN sites si ON si.id=l.site_id
 LEFT JOIN starlink_terminal_emails te ON te.source='manual-csv:'||a.id AND te.terminal_id='manual:'||m.id
 LEFT JOIN starlink_original_emails e ON e.id=te.email_id
UNION ALL
SELECT a.company_id,c.name,'legacy:'||a.id,'legacy:'||m.id,m.kit,m.kit,si.name,a.email,
 CASE a.kind WHEN 'PJ' THEN 'CNPJ' ELSE a.kind END,'LEGADO','Sem informação',m.plan,m.monthly,NULL::timestamptz
FROM antennas m JOIN accounts a ON a.id=m.account_id JOIN sites si ON si.id=m.site_id JOIN starlink_companies c ON c.id=a.company_id;
CREATE OR REPLACE VIEW reporting.invoices AS
SELECT i.company_id,c.name AS company_name,i.source,
 COALESCE(i.payload->'resources'->'account'->>'accountName',i.source) AS account,
 f->>'invoiceId' AS invoice_id,f->>'description' AS description,f->>'currency' AS currency,
 NULLIF(f->>'amount','')::numeric AS amount,NULLIF(f->>'dueAmount','')::numeric AS due_amount,
 NULLIF(f->>'invoiceDate','')::timestamptz AS invoice_date,NULLIF(f->>'dueDate','')::timestamptz AS due_date,
 f->>'status' AS status,i.imported_at AS updated_at
FROM starlink_imports i JOIN starlink_companies c ON c.id=i.company_id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(i.payload->'resources'->'billing/invoices'->'results','[]'::jsonb)) f;
CREATE OR REPLACE VIEW reporting.balances AS
SELECT i.company_id,c.name AS company_name,i.source,b->>'currency' AS currency,
 NULLIF(b->>'dueAmount','')::numeric AS due_amount,i.imported_at AS updated_at
FROM starlink_imports i JOIN starlink_companies c ON c.id=i.company_id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(i.payload->'resources'->'billing/balance'->'balances','[]'::jsonb)) b;
CREATE OR REPLACE VIEW reporting.integrations AS
SELECT i.company_id,c.name AS company_name,i.source,i.imported_at AS last_success,
 jsonb_array_length(COALESCE(i.payload->'resources'->'user-terminals'->'results','[]'::jsonb)) AS terminals,
 jsonb_array_length(COALESCE(i.payload->'resources'->'service-lines'->'results','[]'::jsonb)) AS service_lines,
 (now()-i.imported_at>interval '30 minutes') AS stale
FROM starlink_imports i JOIN starlink_companies c ON c.id=i.company_id;
-- Sem coletor de telemetria nesta versão. Tabela vazia significa SEM COLETA.
CREATE TABLE IF NOT EXISTS reporting.telemetry_samples(
 company_id TEXT NOT NULL REFERENCES starlink_companies(id),source TEXT NOT NULL,terminal_id TEXT NOT NULL,
 collected_at TIMESTAMPTZ NOT NULL,online BOOLEAN,
 latency_ms DOUBLE PRECISION CHECK(latency_ms>=0),loss_percent DOUBLE PRECISION CHECK(loss_percent BETWEEN 0 AND 100),
 download_bps DOUBLE PRECISION CHECK(download_bps>=0),upload_bps DOUBLE PRECISION CHECK(upload_bps>=0),
 PRIMARY KEY(source,terminal_id,collected_at)
);
CREATE OR REPLACE VIEW reporting.connectivity AS
SELECT v.company_id,v.source,v.terminal_id,v.kit,
 CASE WHEN t.collected_at>now()-interval '5 minutes' THEN t.online ELSE NULL END AS online,
 CASE WHEN t.collected_at>now()-interval '5 minutes' THEN t.latency_ms ELSE NULL END AS latency_ms,
 CASE WHEN t.collected_at>now()-interval '5 minutes' THEN t.loss_percent ELSE NULL END AS loss_percent,t.collected_at
FROM reporting.inventory v LEFT JOIN LATERAL(
 SELECT * FROM reporting.telemetry_samples t WHERE t.company_id=v.company_id AND t.source=v.source AND t.terminal_id=v.terminal_id
 ORDER BY t.collected_at DESC LIMIT 1
) t ON true;
COMMIT;
