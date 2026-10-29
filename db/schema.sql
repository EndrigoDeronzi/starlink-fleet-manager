BEGIN;
CREATE TABLE IF NOT EXISTS starlink_schema_versions(version TEXT PRIMARY KEY,applied_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS starlink_companies(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,allow_cpf BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS users(
 id SERIAL PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,
 display_name TEXT NOT NULL DEFAULT '',role TEXT NOT NULL DEFAULT 'read' CHECK(role IN ('read','write','admin')),
 active BOOLEAN NOT NULL DEFAULT true,is_superadmin BOOLEAN NOT NULL DEFAULT false,session_version INTEGER NOT NULL DEFAULT 1,
 CHECK(NOT is_superadmin OR (active AND role='admin'))
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_ci ON users(lower(username));
CREATE TABLE IF NOT EXISTS starlink_user_companies(user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,company_id TEXT REFERENCES starlink_companies(id),PRIMARY KEY(user_id,company_id));
CREATE TABLE IF NOT EXISTS starlink_user_audit(id BIGSERIAL PRIMARY KEY,actor_id INTEGER REFERENCES users(id),target_id INTEGER REFERENCES users(id),action TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS accounts(id SERIAL PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('CPF','PJ')),email TEXT NOT NULL DEFAULT '',company_id TEXT NOT NULL REFERENCES starlink_companies(id));
CREATE TABLE IF NOT EXISTS sites(id SERIAL PRIMARY KEY,name TEXT NOT NULL,cost_center TEXT NOT NULL DEFAULT '',company_id TEXT NOT NULL REFERENCES starlink_companies(id));
CREATE TABLE IF NOT EXISTS antennas(id SERIAL PRIMARY KEY,kit TEXT NOT NULL UNIQUE,account_id INTEGER NOT NULL REFERENCES accounts(id),site_id INTEGER NOT NULL REFERENCES sites(id),plan TEXT NOT NULL DEFAULT '',monthly NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK(monthly>=0));
CREATE TABLE IF NOT EXISTS starlink_imports(source TEXT PRIMARY KEY,payload JSONB NOT NULL,account_type TEXT NOT NULL CHECK(account_type IN ('CPF','CNPJ')),company_id TEXT NOT NULL REFERENCES starlink_companies(id),imported_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS starlink_site_links(source TEXT,terminal_id TEXT,site_id INTEGER NOT NULL REFERENCES sites(id),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(source,terminal_id));
CREATE TABLE IF NOT EXISTS starlink_original_emails(id SERIAL PRIMARY KEY,email TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS starlink_terminal_emails(source TEXT,terminal_id TEXT,email_id INTEGER NOT NULL REFERENCES starlink_original_emails(id),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(source,terminal_id));
CREATE TABLE IF NOT EXISTS starlink_manual_accounts(id TEXT PRIMARY KEY,email TEXT NOT NULL,account_type TEXT NOT NULL CHECK(account_type IN ('CPF','CNPJ')),company_id TEXT NOT NULL REFERENCES starlink_companies(id),UNIQUE(company_id,email,account_type));
CREATE TABLE IF NOT EXISTS starlink_manual_antennas(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES starlink_manual_accounts(id),kit TEXT UNIQUE NOT NULL,name TEXT NOT NULL DEFAULT '',plan TEXT NOT NULL DEFAULT '',monthly NUMERIC(12,2) CHECK(monthly>=0),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS starlink_csv_previews(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),payload JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '20 minutes',processed BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS starlink_sync_runs(id BIGSERIAL PRIMARY KEY,source TEXT NOT NULL,completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),terminals INTEGER NOT NULL,active_services INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS starlink_imports_company_idx ON starlink_imports(company_id);
CREATE INDEX IF NOT EXISTS sites_company_idx ON sites(company_id);
CREATE INDEX IF NOT EXISTS manual_company_idx ON starlink_manual_accounts(company_id);
CREATE OR REPLACE FUNCTION starlink_validate_company() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k TEXT; allowed BOOLEAN;
BEGIN
 IF TG_OP='UPDATE' AND NEW.company_id IS DISTINCT FROM OLD.company_id THEN
  RAISE EXCEPTION 'Empresa do registro não pode ser trocada implicitamente' USING ERRCODE='23514';
 END IF;
 k:=CASE WHEN TG_TABLE_NAME='accounts' THEN to_jsonb(NEW)->>'kind' ELSE to_jsonb(NEW)->>'account_type' END;
 SELECT allow_cpf INTO allowed FROM starlink_companies WHERE id=NEW.company_id;
 IF k='CPF' AND allowed IS DISTINCT FROM true THEN
  RAISE EXCEPTION 'Esta empresa permite somente CNPJ' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION starlink_source_company(s TEXT) RETURNS TEXT LANGUAGE sql STABLE AS $$
 SELECT company_id FROM starlink_imports WHERE source=s
 UNION ALL SELECT company_id FROM starlink_manual_accounts WHERE 'manual-csv:'||id=s
 LIMIT 1
$$;
CREATE OR REPLACE FUNCTION starlink_validate_site_link() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner TEXT; unit_owner TEXT;
BEGIN
 owner:=starlink_source_company(NEW.source);
 SELECT company_id INTO unit_owner FROM sites WHERE id=NEW.site_id;
 IF owner IS NULL OR owner IS DISTINCT FROM unit_owner THEN
  RAISE EXCEPTION 'Antena e obra devem pertencer à mesma empresa' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION starlink_validate_legacy_antenna() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a TEXT; s TEXT;
BEGIN
 SELECT company_id INTO a FROM accounts WHERE id=NEW.account_id;
 SELECT company_id INTO s FROM sites WHERE id=NEW.site_id;
 IF a IS NULL OR a IS DISTINCT FROM s THEN
  RAISE EXCEPTION 'Conta e obra devem pertencer à mesma empresa' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION starlink_validate_manual_move() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a TEXT; b TEXT;
BEGIN
 IF NEW.account_id IS DISTINCT FROM OLD.account_id THEN
  SELECT company_id INTO a FROM starlink_manual_accounts WHERE id=OLD.account_id;
  SELECT company_id INTO b FROM starlink_manual_accounts WHERE id=NEW.account_id;
  IF a IS DISTINCT FROM b THEN RAISE EXCEPTION 'Transferência entre empresas exige revisão específica' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['starlink_imports','starlink_manual_accounts','accounts','sites'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS validate_company ON %I',t);
  EXECUTE format('CREATE TRIGGER validate_company BEFORE INSERT OR UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION starlink_validate_company()',t);
 END LOOP;
END $$;
DROP TRIGGER IF EXISTS validate_company_site ON starlink_site_links;
CREATE TRIGGER validate_company_site BEFORE INSERT OR UPDATE ON starlink_site_links FOR EACH ROW EXECUTE FUNCTION starlink_validate_site_link();
DROP TRIGGER IF EXISTS validate_company_antenna ON antennas;
CREATE TRIGGER validate_company_antenna BEFORE INSERT OR UPDATE ON antennas FOR EACH ROW EXECUTE FUNCTION starlink_validate_legacy_antenna();
DROP TRIGGER IF EXISTS validate_company_move ON starlink_manual_antennas;
CREATE TRIGGER validate_company_move BEFORE UPDATE ON starlink_manual_antennas FOR EACH ROW EXECUTE FUNCTION starlink_validate_manual_move();
INSERT INTO starlink_schema_versions(version) VALUES ('schema-v1') ON CONFLICT DO NOTHING;
COMMIT;
