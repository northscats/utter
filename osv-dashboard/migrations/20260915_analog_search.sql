-- Task 117: база объектов-аналогов.  ORM db.create_all() creates the same
-- tables for fresh installations; this script is for controlled PostgreSQL
-- deployments and preserves existing rows.
BEGIN;
CREATE TABLE IF NOT EXISTS analog_passports (
 id serial PRIMARY KEY, title varchar(1000) NOT NULL, address text DEFAULT '',
 object_type varchar(32) NOT NULL DEFAULT 'котельная',
 climate_zone varchar(32) DEFAULT '', climate_subzone varchar(32) DEFAULT '',
 capacity_kw numeric(18,6), work_type varchar(32) DEFAULT '',
 archived boolean NOT NULL DEFAULT false, current_version_id integer,
 created_by integer REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), extracted_json text NOT NULL DEFAULT '{}',
 overrides_json text NOT NULL DEFAULT '{}', provenance_json text NOT NULL DEFAULT '{}',
 legacy_json text NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_analog_passport_search
 ON analog_passports(object_type, climate_zone, work_type, capacity_kw);
CREATE INDEX IF NOT EXISTS ix_analog_passport_active
 ON analog_passports(archived, updated_at);
CREATE TABLE IF NOT EXISTS analog_passport_versions (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 version_no integer NOT NULL, conclusion_number varchar(255) NOT NULL,
 approval_date date, examination_result varchar(24) NOT NULL, expert_object_name text DEFAULT '',
 source_hash varchar(64) NOT NULL, ssr_hash varchar(64) NOT NULL, schema_version varchar(40) DEFAULT '',
 price_year integer, price_quarter integer, total_cost numeric(20,6), adjusted_cost numeric(20,6),
 adjustment_factor numeric(18,8), adjustment_provenance text DEFAULT '', payload_json text DEFAULT '{}',
 imported_by integer REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_analog_passport_version UNIQUE(passport_id, version_no)
);
CREATE INDEX IF NOT EXISTS ix_analog_version_conclusion ON analog_passport_versions(conclusion_number);
CREATE TABLE IF NOT EXISTS analog_sources (
 id serial PRIMARY KEY, version_id integer NOT NULL UNIQUE REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 source_hash varchar(64) NOT NULL, ssr_hash varchar(64) NOT NULL, source_filename varchar(500) DEFAULT '',
 ssr_filename varchar(500) DEFAULT '', source_size bigint NOT NULL DEFAULT 0, ssr_size bigint NOT NULL DEFAULT 0,
 raw_conclusion_path varchar(1000) NOT NULL DEFAULT '', raw_ssr_path varchar(1000) NOT NULL DEFAULT '',
 parser_version varchar(40) DEFAULT '1', created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_analog_source_pair UNIQUE(source_hash, ssr_hash)
);
CREATE INDEX IF NOT EXISTS ix_analog_source_hash ON analog_sources(source_hash, ssr_hash);
CREATE TABLE IF NOT EXISTS analog_organization_roles (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 role varchar(32) NOT NULL, version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 name varchar(1000) NOT NULL DEFAULT '', ogrn varchar(32) DEFAULT '',
 inn varchar(32) DEFAULT '', kpp varchar(32) DEFAULT '', email varchar(320) DEFAULT '', address text DEFAULT '',
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}',
 effective_json text NOT NULL DEFAULT '{}', provenance text DEFAULT 'conclusionRaw',
 CONSTRAINT uq_analog_organization_role UNIQUE(passport_id, version_id, role)
);
CREATE TABLE IF NOT EXISTS analog_teps (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 name varchar(255) NOT NULL, unit varchar(80) DEFAULT '', value numeric(24,8), normalized_value numeric(24,8),
 normalized_unit varchar(80) DEFAULT '', value_text text DEFAULT '', provenance varchar(500) DEFAULT '',
 version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}', effective_json text NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_analog_tep_name_value ON analog_teps(name, value);
CREATE TABLE IF NOT EXISTS analog_documents (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 parent_id integer REFERENCES analog_documents(id), ordinal varchar(40) DEFAULT '', parent_ordinal varchar(40) DEFAULT '', name varchar(1000) NOT NULL DEFAULT '',
 file_format varchar(80) DEFAULT '', checksum varchar(255) DEFAULT '', note text DEFAULT '',
 signature_name varchar(1000) DEFAULT '', signature_checksum varchar(255) DEFAULT '',
 node_kind varchar(16) NOT NULL DEFAULT 'document',
 version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}', effective_json text NOT NULL DEFAULT '{}',
 provenance text DEFAULT 'conclusionRaw/Documents'
);
CREATE TABLE IF NOT EXISTS analog_cost_rows (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 ordinal varchar(40) DEFAULT '', name varchar(1000) NOT NULL DEFAULT '', submitted_cost numeric(20,6),
 approved_cost numeric(20,6), change_cost numeric(20,6), price_level varchar(120) DEFAULT '',
 vat varchar(80) DEFAULT '', footnote text DEFAULT '', change_reason text DEFAULT '', provenance text DEFAULT '',
 version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}', effective_json text NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS analog_ssr_chapters (
 id serial PRIMARY KEY, version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 number integer NOT NULL, name text DEFAULT '', building numeric(20,6) DEFAULT 0, mounting numeric(20,6) DEFAULT 0,
 equipment numeric(20,6) DEFAULT 0, other numeric(20,6) DEFAULT 0, total numeric(20,6) DEFAULT 0, provenance text DEFAULT '',
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}', effective_json text NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS analog_ssr_items (
 id serial PRIMARY KEY, version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 chapter_number integer NOT NULL, item_type varchar(32) DEFAULT '', ordinal varchar(40) DEFAULT '', name text DEFAULT '',
 reason text DEFAULT '', total numeric(20,6) DEFAULT 0, details_json text DEFAULT '{}', signature_name varchar(1000) DEFAULT '',
 extracted_json text NOT NULL DEFAULT '{}', override_json text NOT NULL DEFAULT '{}', effective_json text NOT NULL DEFAULT '{}',
 provenance text DEFAULT 'ССР 3.01'
);
CREATE TABLE IF NOT EXISTS analog_diagnostics (
 id serial PRIMARY KEY, version_id integer REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 import_id integer, severity varchar(16) NOT NULL DEFAULT 'warning', code varchar(80) NOT NULL,
 message text NOT NULL, path varchar(500) DEFAULT '', created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_analog_diagnostics_version ON analog_diagnostics(version_id);
CREATE TABLE IF NOT EXISTS analog_ssr_signatures (
 id serial PRIMARY KEY, version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 role varchar(120) DEFAULT '', name varchar(1000) DEFAULT '', position varchar(500) DEFAULT '',
 department varchar(500) DEFAULT '', provenance text DEFAULT 'ССР 3.01/Signatures'
);
CREATE TABLE IF NOT EXISTS analog_network_attributes (
 id serial PRIMARY KEY, passport_id integer NOT NULL REFERENCES analog_passports(id) ON DELETE CASCADE,
 key varchar(120) NOT NULL, version_id integer NOT NULL REFERENCES analog_passport_versions(id) ON DELETE CASCADE,
 value text DEFAULT '', value_number numeric(24,8), unit varchar(80) DEFAULT '',
 provenance varchar(500) DEFAULT 'JSON v4', CONSTRAINT uq_analog_network_attribute UNIQUE(passport_id,version_id,key)
);
CREATE TABLE IF NOT EXISTS analog_imports (
 id serial PRIMARY KEY, source_hash varchar(64) NOT NULL, ssr_hash varchar(64) NOT NULL,
 source_filename varchar(500) DEFAULT '', ssr_filename varchar(500) DEFAULT '', raw_conclusion_path varchar(1000) DEFAULT '',
 raw_ssr_path varchar(1000) DEFAULT '', target_passport_id integer REFERENCES analog_passports(id) ON DELETE SET NULL,
 status varchar(24) NOT NULL DEFAULT 'preview',
 preview_json text NOT NULL DEFAULT '{}', diagnostics_json text NOT NULL DEFAULT '[]',
 created_by integer REFERENCES users(id), created_at timestamptz DEFAULT now(), confirmed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_analog_import_hash ON analog_imports(source_hash, ssr_hash);
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS cadastral_number varchar(64) DEFAULT '';
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS natural_conditions text DEFAULT '';
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS construction_object text DEFAULT '';
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS cadastral_number varchar(64) DEFAULT '';
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS natural_conditions text DEFAULT '';
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS construction_object text DEFAULT '';
ALTER TABLE analog_imports ADD COLUMN IF NOT EXISTS target_passport_id integer
 REFERENCES analog_passports(id) ON DELETE SET NULL;
ALTER TABLE analog_network_attributes ADD COLUMN IF NOT EXISTS version_id integer
 REFERENCES analog_passport_versions(id) ON DELETE CASCADE;
UPDATE analog_network_attributes a SET version_id = p.current_version_id
 FROM analog_passports p
 WHERE a.passport_id = p.id AND a.version_id IS NULL;
DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM analog_network_attributes WHERE version_id IS NULL) THEN
  ALTER TABLE analog_network_attributes ALTER COLUMN version_id SET NOT NULL;
 END IF;
END $$;
ALTER TABLE analog_documents ADD COLUMN IF NOT EXISTS parent_id integer
 REFERENCES analog_documents(id) ON DELETE SET NULL;
ALTER TABLE analog_documents ADD COLUMN IF NOT EXISTS node_kind varchar(16) NOT NULL DEFAULT 'document';
ALTER TABLE analog_network_attributes DROP CONSTRAINT IF EXISTS uq_analog_network_attribute;
DROP INDEX IF EXISTS uq_analog_network_attribute;
CREATE UNIQUE INDEX IF NOT EXISTS uq_analog_network_attribute_version
 ON analog_network_attributes(passport_id, version_id, key);
CREATE INDEX IF NOT EXISTS ix_analog_import_target ON analog_imports(target_passport_id);
CREATE INDEX IF NOT EXISTS ix_analog_document_parent ON analog_documents(parent_id);
CREATE INDEX IF NOT EXISTS ix_analog_network_version_key
 ON analog_network_attributes(version_id, key);
COMMIT;