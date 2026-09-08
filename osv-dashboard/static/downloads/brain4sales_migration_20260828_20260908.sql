-- PostgreSQL migration from baseline 64c286ad8d81569131b989ee08ac2a7eb81f5a17.
-- Schema delta: exactly 20 new tables and 37 columns on baseline tables.
-- Generated offline from database.py metadata; this script performs no refresh,
-- snapshot restore, analysis repair, credential insertion, or user/admin seeding.

BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';
SELECT pg_advisory_xact_lock(20260908, 20260828);

-- Refuse to migrate an unknown or incomplete pre-2026-08-28 installation.
DO $preflight$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'settings', 'email_rules', 'users', 'companies', 'uploads',
        'audit_log', 'ai_providers', 'ai_summaries', 'doc_catalogs',
        'doc_routing_rules', 'doc_check_rules', 'doc_packages', 'doc_files',
        'doc_file_pages', 'doc_check_results', 'doc_findings',
        'doc_mark_buttons', 'doc_file_marks', 'doc_analysis_templates',
        'daily_rows'
    ]
    LOOP
        IF to_regclass(format('public.%I', t)) IS NULL THEN
            RAISE EXCEPTION
                'Migration baseline is incomplete: required table public.% is absent',
                t;
        END IF;
    END LOOP;
END
$preflight$;

-- 37 columns added to tables that existed at the baseline.
ALTER TABLE ai_providers
    ADD COLUMN IF NOT EXISTS supports_vision boolean NOT NULL DEFAULT false;

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS internal_code varchar(80) DEFAULT '',
    ADD COLUMN IF NOT EXISTS identity_key varchar(80),
    ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS is_rso boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS curator_user_id integer,
    ADD COLUMN IF NOT EXISTS ogrn varchar(20) DEFAULT '',
    ADD COLUMN IF NOT EXISTS kpp varchar(20) DEFAULT '',
    ADD COLUMN IF NOT EXISTS opf_short varchar(120) DEFAULT '',
    ADD COLUMN IF NOT EXISTS legal_status varchar(40) DEFAULT '',
    ADD COLUMN IF NOT EXISTS registration_date date,
    ADD COLUMN IF NOT EXISTS ogrn_date date,
    ADD COLUMN IF NOT EXISTS actuality_date date,
    ADD COLUMN IF NOT EXISTS liquidation_date date,
    ADD COLUMN IF NOT EXISTS legal_address text DEFAULT '',
    ADD COLUMN IF NOT EXISTS address_quality varchar(40) DEFAULT '',
    ADD COLUMN IF NOT EXISTS address_ids_json text DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS director_position varchar(255) DEFAULT '',
    ADD COLUMN IF NOT EXISTS primary_okved varchar(32) DEFAULT '',
    ADD COLUMN IF NOT EXISTS classifications_json text DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS labels_json text DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS dadata_source varchar(40) DEFAULT '',
    ADD COLUMN IF NOT EXISTS dadata_received_at timestamptz,
    ADD COLUMN IF NOT EXISTS dadata_actuality_date date,
    ADD COLUMN IF NOT EXISTS dadata_sync_status varchar(24) DEFAULT 'never',
    ADD COLUMN IF NOT EXISTS dadata_last_error text DEFAULT '',
    ADD COLUMN IF NOT EXISTS profile_version integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

ALTER TABLE doc_file_pages
    ADD COLUMN IF NOT EXISTS image_width integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_height integer DEFAULT 0,
    ADD COLUMN IF NOT EXISTS words_json text DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS coordinate_version varchar(40) DEFAULT '';

ALTER TABLE doc_findings
    ADD COLUMN IF NOT EXISTS source varchar(20) DEFAULT '',
    ADD COLUMN IF NOT EXISTS boxes_json text DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS confidence double precision,
    ADD COLUMN IF NOT EXISTS algorithm_version varchar(40) DEFAULT '';

ALTER TABLE uploads
    ADD COLUMN IF NOT EXISTS metrics_json text DEFAULT '{}';

-- Match Python scalar defaults even when a column came from a partial upgrade.
ALTER TABLE ai_providers
    ALTER COLUMN supports_vision SET DEFAULT false;
UPDATE ai_providers SET supports_vision = false WHERE supports_vision IS NULL;
ALTER TABLE ai_providers ALTER COLUMN supports_vision SET NOT NULL;

ALTER TABLE companies
    ALTER COLUMN internal_code SET DEFAULT '',
    ALTER COLUMN status SET DEFAULT 'active',
    ALTER COLUMN is_rso SET DEFAULT true,
    ALTER COLUMN ogrn SET DEFAULT '',
    ALTER COLUMN kpp SET DEFAULT '',
    ALTER COLUMN opf_short SET DEFAULT '',
    ALTER COLUMN legal_status SET DEFAULT '',
    ALTER COLUMN legal_address SET DEFAULT '',
    ALTER COLUMN address_quality SET DEFAULT '',
    ALTER COLUMN address_ids_json SET DEFAULT '{}',
    ALTER COLUMN director_position SET DEFAULT '',
    ALTER COLUMN primary_okved SET DEFAULT '',
    ALTER COLUMN classifications_json SET DEFAULT '{}',
    ALTER COLUMN labels_json SET DEFAULT '[]',
    ALTER COLUMN dadata_source SET DEFAULT '',
    ALTER COLUMN dadata_sync_status SET DEFAULT 'never',
    ALTER COLUMN dadata_last_error SET DEFAULT '',
    ALTER COLUMN profile_version SET DEFAULT 1,
    ALTER COLUMN updated_at SET DEFAULT now();

UPDATE companies
SET legal_address = address
WHERE coalesce(legal_address, '') = '' AND coalesce(address, '') <> '';
UPDATE companies SET status = 'active' WHERE status IS NULL OR status = '';
UPDATE companies
SET internal_code = id::varchar(80)
WHERE coalesce(btrim(internal_code), '') = '';
UPDATE companies
SET is_rso = coalesce(is_rso, true),
    ogrn = coalesce(ogrn, ''),
    kpp = coalesce(kpp, ''),
    opf_short = coalesce(opf_short, ''),
    legal_status = coalesce(legal_status, ''),
    legal_address = coalesce(legal_address, ''),
    address_quality = coalesce(address_quality, ''),
    address_ids_json = coalesce(address_ids_json, '{}'),
    director_position = coalesce(director_position, ''),
    primary_okved = coalesce(primary_okved, ''),
    classifications_json = coalesce(classifications_json, '{}'),
    labels_json = coalesce(labels_json, '[]'),
    dadata_source = coalesce(dadata_source, ''),
    dadata_sync_status = coalesce(nullif(dadata_sync_status, ''), 'never'),
    dadata_last_error = coalesce(dadata_last_error, ''),
    profile_version = coalesce(profile_version, 1),
    updated_at = coalesce(updated_at, created_at, now());

ALTER TABLE companies
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN is_rso SET NOT NULL,
    ALTER COLUMN profile_version SET NOT NULL;

ALTER TABLE doc_file_pages
    ALTER COLUMN image_width SET DEFAULT 0,
    ALTER COLUMN image_height SET DEFAULT 0,
    ALTER COLUMN words_json SET DEFAULT '[]',
    ALTER COLUMN coordinate_version SET DEFAULT '';
ALTER TABLE doc_findings
    ALTER COLUMN source SET DEFAULT '',
    ALTER COLUMN boxes_json SET DEFAULT '[]',
    ALTER COLUMN algorithm_version SET DEFAULT '';
ALTER TABLE uploads ALTER COLUMN metrics_json SET DEFAULT '{}';

-- New table 01/20: module assignment (never populated automatically here).
CREATE TABLE IF NOT EXISTS user_module_access (
    id serial,
    user_id integer NOT NULL,
    module_key varchar(80) NOT NULL,
    created_at timestamptz DEFAULT now(),
    CONSTRAINT pk_user_module_access PRIMARY KEY (id),
    CONSTRAINT uq_user_module_access_user_key UNIQUE (user_id, module_key),
    CONSTRAINT fk_user_module_access_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- New tables 02-08/20: canonical company directory.
CREATE TABLE IF NOT EXISTS company_memberships (
    id serial,
    user_id integer NOT NULL,
    company_id integer NOT NULL,
    participation_type varchar(40) NOT NULL DEFAULT 'employee',
    is_primary boolean NOT NULL DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT pk_company_memberships PRIMARY KEY (id),
    CONSTRAINT uq_company_membership_user_company
        UNIQUE (user_id, company_id),
    CONSTRAINT fk_company_memberships_user
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_memberships_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_contacts (
    id serial,
    company_id integer NOT NULL,
    contact_type varchar(40) NOT NULL DEFAULT 'other',
    label varchar(120) DEFAULT '',
    name varchar(255) DEFAULT '',
    value varchar(500) NOT NULL DEFAULT '',
    is_internal boolean NOT NULL DEFAULT true,
    source varchar(40) DEFAULT 'manual',
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT pk_company_contacts PRIMARY KEY (id),
    CONSTRAINT fk_company_contacts_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_dadata_profiles (
    id serial,
    company_id integer NOT NULL,
    normalized_json text DEFAULT '{}',
    extended_json text DEFAULT '{}',
    source varchar(40) DEFAULT 'DaData',
    received_at timestamptz,
    actuality_date date,
    accepted_at timestamptz,
    accepted_by integer,
    version integer NOT NULL DEFAULT 1,
    CONSTRAINT pk_company_dadata_profiles PRIMARY KEY (id),
    CONSTRAINT uq_company_dadata_profiles_company UNIQUE (company_id),
    CONSTRAINT fk_company_dadata_profiles_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_dadata_profiles_accepter
        FOREIGN KEY (accepted_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS company_dadata_snapshots (
    id serial,
    company_id integer NOT NULL,
    query_inn varchar(20) DEFAULT '',
    query_ogrn varchar(20) DEFAULT '',
    received_at timestamptz DEFAULT now(),
    actuality_date date,
    response_hash varchar(64) DEFAULT '',
    status varchar(24) NOT NULL DEFAULT 'success',
    normalized_json text DEFAULT '{}',
    response_json text DEFAULT '{}',
    error_text text DEFAULT '',
    requested_by integer,
    CONSTRAINT pk_company_dadata_snapshots PRIMARY KEY (id),
    CONSTRAINT fk_company_dadata_snapshots_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_company_dadata_snapshots_requester
        FOREIGN KEY (requested_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS company_okveds (
    id serial,
    company_id integer NOT NULL,
    code varchar(32) NOT NULL,
    name varchar(500) DEFAULT '',
    is_main boolean NOT NULL DEFAULT false,
    source varchar(40) DEFAULT 'DaData',
    CONSTRAINT pk_company_okveds PRIMARY KEY (id),
    CONSTRAINT uq_company_okved_code UNIQUE (company_id, code),
    CONSTRAINT fk_company_okveds_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_licenses (
    id serial,
    company_id integer NOT NULL,
    number varchar(255) DEFAULT '',
    activity text DEFAULT '',
    status varchar(80) DEFAULT '',
    details_json text DEFAULT '{}',
    source varchar(40) DEFAULT 'DaData',
    CONSTRAINT pk_company_licenses PRIMARY KEY (id),
    CONSTRAINT fk_company_licenses_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_finances (
    id serial,
    company_id integer NOT NULL,
    year integer NOT NULL,
    revenue double precision,
    expense double precision,
    profit double precision,
    details_json text DEFAULT '{}',
    source varchar(40) DEFAULT 'DaData',
    CONSTRAINT pk_company_finances PRIMARY KEY (id),
    CONSTRAINT uq_company_finance_year UNIQUE (company_id, year),
    CONSTRAINT fk_company_finances_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

-- New table 09/20: named rating configurations.
CREATE TABLE IF NOT EXISTS rating_profiles (
    id serial,
    owner_id integer NOT NULL,
    name varchar(120) NOT NULL,
    config_json text NOT NULL DEFAULT '{}',
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT pk_rating_profiles PRIMARY KEY (id),
    CONSTRAINT uq_rating_profile_owner_name UNIQUE (owner_id, name),
    CONSTRAINT fk_rating_profiles_owner
        FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
);

-- New tables 10-14/20: annual historical analytics.
CREATE TABLE IF NOT EXISTS historical_imports (
    id serial,
    company_id integer NOT NULL,
    uploaded_by integer NOT NULL,
    original_filename varchar(500) NOT NULL,
    stored_path varchar(1000) NOT NULL,
    file_hash varchar(64) NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'preview',
    is_partial boolean NOT NULL DEFAULT false,
    partial_confirmed boolean NOT NULL DEFAULT false,
    preview_json text NOT NULL DEFAULT '{}',
    validation_json text NOT NULL DEFAULT '{}',
    error_text text DEFAULT '',
    created_at timestamptz DEFAULT now(),
    confirmed_at timestamptz,
    CONSTRAINT pk_historical_imports PRIMARY KEY (id),
    CONSTRAINT fk_historical_imports_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_historical_imports_author
        FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS historical_year_versions (
    id serial,
    import_id integer NOT NULL,
    company_id integer NOT NULL,
    year integer NOT NULL,
    version_no integer NOT NULL,
    is_active boolean NOT NULL DEFAULT false,
    activated_at timestamptz,
    activated_by integer,
    created_at timestamptz DEFAULT now(),
    CONSTRAINT pk_historical_year_versions PRIMARY KEY (id),
    CONSTRAINT uq_historical_year_version
        UNIQUE (company_id, year, version_no),
    CONSTRAINT fk_historical_year_versions_import
        FOREIGN KEY (import_id) REFERENCES historical_imports(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_historical_year_versions_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_historical_year_versions_activator
        FOREIGN KEY (activated_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS historical_metric_values (
    id serial,
    year_version_id integer NOT NULL,
    section_key varchar(100) NOT NULL,
    section_label varchar(255) NOT NULL,
    metric_key varchar(240) NOT NULL,
    metric_label text NOT NULL,
    series_key varchar(120) NOT NULL DEFAULT 'value',
    period_label varchar(255) NOT NULL DEFAULT '',
    value_numeric double precision,
    value_text text,
    unit varchar(40) NOT NULL DEFAULT 'ratio',
    is_missing boolean NOT NULL DEFAULT false,
    is_upper_bound boolean NOT NULL DEFAULT false,
    CONSTRAINT pk_historical_metric_values PRIMARY KEY (id),
    CONSTRAINT uq_historical_metric_version_key
        UNIQUE (year_version_id, section_key, metric_key, series_key),
    CONSTRAINT fk_historical_metric_values_version
        FOREIGN KEY (year_version_id) REFERENCES historical_year_versions(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS historical_raw_values (
    id serial,
    metric_id integer NOT NULL,
    raw_text text NOT NULL DEFAULT '',
    sheet_name varchar(255) NOT NULL DEFAULT '',
    cell_ref varchar(32) NOT NULL DEFAULT '',
    CONSTRAINT pk_historical_raw_values PRIMARY KEY (id),
    CONSTRAINT uq_historical_raw_values_metric UNIQUE (metric_id),
    CONSTRAINT fk_historical_raw_values_metric
        FOREIGN KEY (metric_id) REFERENCES historical_metric_values(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS historical_mappings (
    id serial,
    company_id integer NOT NULL,
    kind varchar(20) NOT NULL,
    source_label text NOT NULL,
    source_normalized varchar(500) NOT NULL,
    canonical_key varchar(240) NOT NULL,
    canonical_label text NOT NULL,
    confirmed_by integer,
    confirmed_at timestamptz DEFAULT now(),
    CONSTRAINT pk_historical_mappings PRIMARY KEY (id),
    CONSTRAINT uq_historical_mapping_company_source
        UNIQUE (company_id, kind, source_normalized),
    CONSTRAINT fk_historical_mappings_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
    CONSTRAINT fk_historical_mappings_confirmer
        FOREIGN KEY (confirmed_by) REFERENCES users(id)
);

-- New tables 15-17/20: protected 1C JSON intake.
CREATE TABLE IF NOT EXISTS one_c_channels (
    id serial,
    company_id integer NOT NULL,
    login varchar(120) NOT NULL,
    password_hash text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    allow_http boolean NOT NULL DEFAULT false,
    created_at timestamptz DEFAULT now(),
    last_received_at timestamptz,
    last_receipt_id varchar(64),
    last_raw_json text,
    last_row_count integer NOT NULL DEFAULT 0,
    CONSTRAINT pk_one_c_channels PRIMARY KEY (id),
    CONSTRAINT uq_one_c_channels_company UNIQUE (company_id),
    CONSTRAINT uq_one_c_channels_login UNIQUE (login),
    CONSTRAINT uq_one_c_channels_last_receipt UNIQUE (last_receipt_id),
    CONSTRAINT fk_one_c_channels_company
        FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS one_c_attempts (
    id serial,
    channel_id integer,
    received_at timestamptz DEFAULT now(),
    result varchar(24) NOT NULL,
    receipt_id varchar(64),
    byte_size integer NOT NULL DEFAULT 0,
    row_count integer,
    transport varchar(8) NOT NULL,
    error varchar(300) DEFAULT '',
    CONSTRAINT pk_one_c_attempts PRIMARY KEY (id),
    CONSTRAINT fk_one_c_attempts_channel
        FOREIGN KEY (channel_id) REFERENCES one_c_channels(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS one_c_idempotency (
    id serial,
    channel_id integer NOT NULL,
    key varchar(200) NOT NULL,
    body_hash varchar(64) NOT NULL,
    receipt_id varchar(64) NOT NULL,
    created_at timestamptz DEFAULT now(),
    CONSTRAINT pk_one_c_idempotency PRIMARY KEY (id),
    CONSTRAINT uq_one_c_idempotency_channel_key UNIQUE (channel_id, key),
    CONSTRAINT fk_one_c_idempotency_channel
        FOREIGN KEY (channel_id) REFERENCES one_c_channels(id)
        ON DELETE CASCADE
);

-- New tables 18-20/20: reproducible document-analysis runs.
CREATE TABLE IF NOT EXISTS doc_catalog_analysis_runs (
    id serial,
    package_id integer NOT NULL,
    catalog_id integer,
    catalog_name_snapshot varchar(255) DEFAULT '',
    created_by integer NOT NULL,
    mode varchar(20) NOT NULL,
    user_prompt text DEFAULT '',
    template_id integer,
    template_name varchar(255) DEFAULT '',
    template_prompt text DEFAULT '',
    file_ids_json text DEFAULT '[]',
    skipped_json text DEFAULT '[]',
    options_json text DEFAULT '{}',
    summary_json text DEFAULT '{}',
    errors_json text DEFAULT '[]',
    status varchar(20) NOT NULL DEFAULT 'queued',
    files_total integer NOT NULL DEFAULT 0,
    files_done integer NOT NULL DEFAULT 0,
    files_cancelled integer NOT NULL DEFAULT 0,
    error_text text DEFAULT '',
    cancel_requested_by integer,
    cancel_requested_at timestamptz,
    cancelled_at timestamptz,
    created_at timestamptz DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT pk_doc_catalog_analysis_runs PRIMARY KEY (id),
    CONSTRAINT fk_doc_catalog_runs_package
        FOREIGN KEY (package_id) REFERENCES doc_packages(id),
    CONSTRAINT fk_doc_catalog_runs_catalog
        FOREIGN KEY (catalog_id) REFERENCES doc_catalogs(id) ON DELETE SET NULL,
    CONSTRAINT fk_doc_catalog_runs_author
        FOREIGN KEY (created_by) REFERENCES users(id),
    CONSTRAINT fk_doc_catalog_runs_template
        FOREIGN KEY (template_id) REFERENCES doc_analysis_templates(id),
    CONSTRAINT fk_doc_catalog_runs_cancel_user
        FOREIGN KEY (cancel_requested_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS doc_analysis_runs (
    id serial,
    package_id integer NOT NULL,
    catalog_run_id integer,
    file_id integer NOT NULL,
    created_by integer NOT NULL,
    mode varchar(20) NOT NULL,
    scope varchar(30) NOT NULL DEFAULT 'current_file',
    user_prompt text DEFAULT '',
    template_id integer,
    template_name varchar(255) DEFAULT '',
    template_prompt text DEFAULT '',
    options_json text DEFAULT '{}',
    result_json text DEFAULT '{}',
    status varchar(20) NOT NULL DEFAULT 'queued',
    error_text text DEFAULT '',
    created_at timestamptz DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT pk_doc_analysis_runs PRIMARY KEY (id),
    CONSTRAINT fk_doc_analysis_runs_package
        FOREIGN KEY (package_id) REFERENCES doc_packages(id),
    CONSTRAINT fk_doc_analysis_runs_catalog_run
        FOREIGN KEY (catalog_run_id) REFERENCES doc_catalog_analysis_runs(id),
    CONSTRAINT fk_doc_analysis_runs_file
        FOREIGN KEY (file_id) REFERENCES doc_files(id),
    CONSTRAINT fk_doc_analysis_runs_author
        FOREIGN KEY (created_by) REFERENCES users(id),
    CONSTRAINT fk_doc_analysis_runs_template
        FOREIGN KEY (template_id) REFERENCES doc_analysis_templates(id)
);

CREATE TABLE IF NOT EXISTS doc_free_analysis_results (
    id serial,
    run_id integer NOT NULL,
    file_id integer NOT NULL,
    content text DEFAULT '',
    created_at timestamptz DEFAULT now(),
    CONSTRAINT pk_doc_free_analysis_results PRIMARY KEY (id),
    CONSTRAINT uq_doc_free_analysis_results_run UNIQUE (run_id),
    CONSTRAINT fk_doc_free_results_run
        FOREIGN KEY (run_id) REFERENCES doc_analysis_runs(id),
    CONSTRAINT fk_doc_free_results_file
        FOREIGN KEY (file_id) REFERENCES doc_files(id)
);

-- Repair columns known to have been appended after initial versions of new
-- tables. This makes reruns safe on installations upgraded between commits.
ALTER TABLE doc_catalog_analysis_runs
    ADD COLUMN IF NOT EXISTS catalog_name_snapshot varchar(255) DEFAULT '',
    ADD COLUMN IF NOT EXISTS skipped_json text DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS files_cancelled integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cancel_requested_by integer,
    ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz,
    ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;
ALTER TABLE doc_catalog_analysis_runs ALTER COLUMN catalog_id DROP NOT NULL;

ALTER TABLE doc_analysis_runs
    ADD COLUMN IF NOT EXISTS catalog_run_id integer,
    ADD COLUMN IF NOT EXISTS options_json text DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS result_json text DEFAULT '{}';

ALTER TABLE historical_metric_values
    ADD COLUMN IF NOT EXISTS series_key varchar(120) NOT NULL DEFAULT 'value',
    ADD COLUMN IF NOT EXISTS period_label varchar(255) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS unit varchar(40) NOT NULL DEFAULT 'ratio',
    ADD COLUMN IF NOT EXISTS is_upper_bound boolean NOT NULL DEFAULT false;

-- Add every FK structurally: an equivalent differently named FK is accepted.
DO $foreign_keys$
DECLARE
    r record;
    local_att smallint;
    remote_att smallint;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
          ('companies','curator_user_id','users','id','a','fk_companies_curator'),
          ('user_module_access','user_id','users','id','c','fk_user_module_access_user'),
          ('company_memberships','user_id','users','id','c','fk_company_memberships_user'),
          ('company_memberships','company_id','companies','id','c','fk_company_memberships_company'),
          ('company_contacts','company_id','companies','id','c','fk_company_contacts_company'),
          ('company_dadata_profiles','company_id','companies','id','c','fk_company_dadata_profiles_company'),
          ('company_dadata_profiles','accepted_by','users','id','a','fk_company_dadata_profiles_accepter'),
          ('company_dadata_snapshots','company_id','companies','id','c','fk_company_dadata_snapshots_company'),
          ('company_dadata_snapshots','requested_by','users','id','a','fk_company_dadata_snapshots_requester'),
          ('company_okveds','company_id','companies','id','c','fk_company_okveds_company'),
          ('company_licenses','company_id','companies','id','c','fk_company_licenses_company'),
          ('company_finances','company_id','companies','id','c','fk_company_finances_company'),
          ('rating_profiles','owner_id','users','id','c','fk_rating_profiles_owner'),
          ('historical_imports','company_id','companies','id','c','fk_historical_imports_company'),
          ('historical_imports','uploaded_by','users','id','r','fk_historical_imports_author'),
          ('historical_year_versions','import_id','historical_imports','id','c','fk_historical_year_versions_import'),
          ('historical_year_versions','company_id','companies','id','c','fk_historical_year_versions_company'),
          ('historical_year_versions','activated_by','users','id','a','fk_historical_year_versions_activator'),
          ('historical_metric_values','year_version_id','historical_year_versions','id','c','fk_historical_metric_values_version'),
          ('historical_raw_values','metric_id','historical_metric_values','id','c','fk_historical_raw_values_metric'),
          ('historical_mappings','company_id','companies','id','c','fk_historical_mappings_company'),
          ('historical_mappings','confirmed_by','users','id','a','fk_historical_mappings_confirmer'),
          ('one_c_channels','company_id','companies','id','c','fk_one_c_channels_company'),
          ('one_c_attempts','channel_id','one_c_channels','id','c','fk_one_c_attempts_channel'),
          ('one_c_idempotency','channel_id','one_c_channels','id','c','fk_one_c_idempotency_channel'),
          ('doc_catalog_analysis_runs','package_id','doc_packages','id','a','fk_doc_catalog_runs_package'),
          ('doc_catalog_analysis_runs','catalog_id','doc_catalogs','id','n','fk_doc_catalog_runs_catalog'),
          ('doc_catalog_analysis_runs','created_by','users','id','a','fk_doc_catalog_runs_author'),
          ('doc_catalog_analysis_runs','template_id','doc_analysis_templates','id','a','fk_doc_catalog_runs_template'),
          ('doc_catalog_analysis_runs','cancel_requested_by','users','id','a','fk_doc_catalog_runs_cancel_user'),
          ('doc_analysis_runs','package_id','doc_packages','id','a','fk_doc_analysis_runs_package'),
          ('doc_analysis_runs','catalog_run_id','doc_catalog_analysis_runs','id','a','fk_doc_analysis_runs_catalog_run'),
          ('doc_analysis_runs','file_id','doc_files','id','a','fk_doc_analysis_runs_file'),
          ('doc_analysis_runs','created_by','users','id','a','fk_doc_analysis_runs_author'),
          ('doc_analysis_runs','template_id','doc_analysis_templates','id','a','fk_doc_analysis_runs_template'),
          ('doc_free_analysis_results','run_id','doc_analysis_runs','id','a','fk_doc_free_results_run'),
          ('doc_free_analysis_results','file_id','doc_files','id','a','fk_doc_free_results_file')
        ) AS x(child_table, child_col, parent_table, parent_col,
               delete_code, constraint_name)
    LOOP
        SELECT attnum INTO STRICT local_att
        FROM pg_catalog.pg_attribute
        WHERE attrelid = format('public.%I', r.child_table)::regclass
          AND attname = r.child_col AND NOT attisdropped;
        SELECT attnum INTO STRICT remote_att
        FROM pg_catalog.pg_attribute
        WHERE attrelid = format('public.%I', r.parent_table)::regclass
          AND attname = r.parent_col AND NOT attisdropped;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_constraint c
            WHERE c.contype = 'f'
              AND c.conrelid = format('public.%I', r.child_table)::regclass
              AND c.confrelid = format('public.%I', r.parent_table)::regclass
              AND c.conkey = ARRAY[local_att]::smallint[]
              AND c.confkey = ARRAY[remote_att]::smallint[]
              AND c.confdeltype = r.delete_code::"char"
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (%I) '
                'REFERENCES public.%I(%I)%s',
                r.child_table, r.constraint_name, r.child_col,
                r.parent_table, r.parent_col,
                CASE r.delete_code
                    WHEN 'c' THEN ' ON DELETE CASCADE'
                    WHEN 'r' THEN ' ON DELETE RESTRICT'
                    WHEN 'n' THEN ' ON DELETE SET NULL'
                    ELSE ''
                END);
        END IF;
    END LOOP;
END
$foreign_keys$;

-- Add declared non-partial uniques structurally. Duplicate data intentionally
-- aborts the transaction; this migration never deletes or merges user rows.
DO $unique_constraints$
DECLARE
    r record;
    wanted smallint[];
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
          ('user_module_access','user_id,module_key','uq_user_module_access_user_key'),
          ('company_memberships','user_id,company_id','uq_company_membership_user_company'),
          ('company_dadata_profiles','company_id','uq_company_dadata_profiles_company'),
          ('company_okveds','company_id,code','uq_company_okved_code'),
          ('company_finances','company_id,year','uq_company_finance_year'),
          ('rating_profiles','owner_id,name','uq_rating_profile_owner_name'),
          ('historical_year_versions','company_id,year,version_no','uq_historical_year_version'),
          ('historical_metric_values','year_version_id,section_key,metric_key,series_key','uq_historical_metric_version_key'),
          ('historical_raw_values','metric_id','uq_historical_raw_values_metric'),
          ('historical_mappings','company_id,kind,source_normalized','uq_historical_mapping_company_source'),
          ('one_c_channels','company_id','uq_one_c_channels_company'),
          ('one_c_channels','login','uq_one_c_channels_login'),
          ('one_c_channels','last_receipt_id','uq_one_c_channels_last_receipt'),
          ('one_c_idempotency','channel_id,key','uq_one_c_idempotency_channel_key'),
          ('doc_free_analysis_results','run_id','uq_doc_free_analysis_results_run')
        ) AS x(table_name, column_csv, constraint_name)
    LOOP
        SELECT array_agg(a.attnum ORDER BY u.ord)::smallint[] INTO wanted
        FROM unnest(string_to_array(r.column_csv, ',')) WITH ORDINALITY u(col, ord)
        JOIN pg_catalog.pg_attribute a
          ON a.attrelid = format('public.%I', r.table_name)::regclass
         AND a.attname = u.col AND NOT a.attisdropped;

        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint c
            WHERE c.conrelid = format('public.%I', r.table_name)::regclass
              AND c.contype = 'u' AND c.conkey = wanted
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_index i
            WHERE i.indrelid = format('public.%I', r.table_name)::regclass
              AND i.indisunique AND i.indpred IS NULL
              AND i.indnkeyatts = cardinality(wanted)
              AND i.indkey::text = array_to_string(wanted, ' ')
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (%s)',
                r.table_name, r.constraint_name,
                (SELECT string_agg(format('%I', u.col), ', ' ORDER BY u.ord)
                 FROM unnest(string_to_array(r.column_csv, ','))
                      WITH ORDINALITY u(col, ord)));
        END IF;
    END LOOP;
END
$unique_constraints$;

-- Declared ordinary indexes.
CREATE INDEX IF NOT EXISTS ix_user_module_access_user_id
    ON user_module_access(user_id);
CREATE INDEX IF NOT EXISTS ix_user_module_access_module_key
    ON user_module_access(module_key);
CREATE INDEX IF NOT EXISTS ix_company_memberships_user_id
    ON company_memberships(user_id);
CREATE INDEX IF NOT EXISTS ix_company_memberships_company_id
    ON company_memberships(company_id);
CREATE INDEX IF NOT EXISTS ix_company_contacts_company_id
    ON company_contacts(company_id);
CREATE INDEX IF NOT EXISTS ix_company_dadata_snapshots_company_id
    ON company_dadata_snapshots(company_id);
CREATE INDEX IF NOT EXISTS ix_company_dadata_snapshots_received_at
    ON company_dadata_snapshots(received_at);
CREATE INDEX IF NOT EXISTS ix_company_okveds_company_id
    ON company_okveds(company_id);
CREATE INDEX IF NOT EXISTS ix_company_licenses_company_id
    ON company_licenses(company_id);
CREATE INDEX IF NOT EXISTS ix_company_finances_company_id
    ON company_finances(company_id);
CREATE INDEX IF NOT EXISTS ix_rating_profiles_owner_id
    ON rating_profiles(owner_id);
CREATE INDEX IF NOT EXISTS ix_historical_imports_company_id
    ON historical_imports(company_id);
CREATE INDEX IF NOT EXISTS ix_historical_imports_file_hash
    ON historical_imports(file_hash);
CREATE INDEX IF NOT EXISTS ix_historical_year_versions_import_id
    ON historical_year_versions(import_id);
CREATE INDEX IF NOT EXISTS ix_historical_year_versions_company_id
    ON historical_year_versions(company_id);
CREATE INDEX IF NOT EXISTS ix_historical_year_versions_year
    ON historical_year_versions(year);
CREATE INDEX IF NOT EXISTS ix_historical_year_versions_is_active
    ON historical_year_versions(is_active);
CREATE INDEX IF NOT EXISTS ix_historical_metric_values_year_version_id
    ON historical_metric_values(year_version_id);
CREATE INDEX IF NOT EXISTS ix_historical_metric_values_section_key
    ON historical_metric_values(section_key);
CREATE INDEX IF NOT EXISTS ix_historical_metric_values_metric_key
    ON historical_metric_values(metric_key);
CREATE INDEX IF NOT EXISTS ix_historical_mappings_company_id
    ON historical_mappings(company_id);
CREATE INDEX IF NOT EXISTS ix_one_c_attempts_channel_id
    ON one_c_attempts(channel_id);
CREATE INDEX IF NOT EXISTS ix_one_c_attempts_received_at
    ON one_c_attempts(received_at);
CREATE INDEX IF NOT EXISTS ix_one_c_idempotency_channel_id
    ON one_c_idempotency(channel_id);
CREATE INDEX IF NOT EXISTS ix_one_c_idempotency_created_at
    ON one_c_idempotency(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_c_channels_company_id
    ON one_c_channels(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_c_channels_login
    ON one_c_channels(login);

-- The two application invariants represented by partial unique indexes.
-- Existing duplicates are not hidden or deleted: CREATE UNIQUE INDEX aborts.
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_identity_key
    ON companies(identity_key)
    WHERE identity_key IS NOT NULL AND identity_key <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_historical_active_year
    ON historical_year_versions(company_id, year)
    WHERE is_active;

-- Backfill missing canonical identity only. Existing nonblank keys, including
-- legacy/false identities, are never rewritten. OGRN wins over INN; КПП is
-- appended only at valid length. Lowest company id wins a candidate duplicate,
-- and keys already assigned to any row are excluded.
WITH cleaned AS (
    SELECT id,
           regexp_replace(coalesce(inn, ''), '\D', '', 'g') AS inn_digits,
           regexp_replace(coalesce(ogrn, ''), '\D', '', 'g') AS ogrn_digits,
           regexp_replace(coalesce(kpp, ''), '\D', '', 'g') AS kpp_digits
    FROM companies
    WHERE coalesce(btrim(identity_key), '') = ''
), candidates AS (
    SELECT id,
           CASE
             WHEN length(ogrn_digits) IN (13, 15) THEN
               'ogrn:' || ogrn_digits
             WHEN length(inn_digits) IN (10, 12) THEN
               'inn:' || inn_digits
           END ||
           CASE WHEN length(kpp_digits) = 9
                THEN ':kpp:' || kpp_digits ELSE '' END AS candidate_key
    FROM cleaned
    WHERE length(ogrn_digits) IN (13, 15)
       OR length(inn_digits) IN (10, 12)
), ranked AS (
    SELECT id, candidate_key,
           row_number() OVER (PARTITION BY candidate_key ORDER BY id) AS rn
    FROM candidates
), winners AS (
    SELECT r.id, r.candidate_key
    FROM ranked r
    WHERE r.rn = 1
      AND NOT EXISTS (
          SELECT 1 FROM companies assigned
          WHERE coalesce(btrim(assigned.identity_key), '') <> ''
            AND assigned.identity_key = r.candidate_key
      )
)
UPDATE companies c
SET identity_key = w.candidate_key
FROM winners w
WHERE c.id = w.id AND coalesce(btrim(c.identity_key), '') = '';

-- Preserve invalid/raw classifications text. Only parseable JSON objects whose
-- organization_types is missing or an empty array are changed. Invalid JSON is
-- explicitly warned about and skipped rather than replaced with {}.
DO $classifications$
DECLARE
    r record;
    parsed jsonb;
BEGIN
    FOR r IN
        SELECT id, classifications_json
        FROM companies
        WHERE is_rso IS TRUE
    LOOP
        BEGIN
            parsed := r.classifications_json::jsonb;
        EXCEPTION WHEN invalid_text_representation
                    OR untranslatable_character
                    OR numeric_value_out_of_range THEN
            RAISE WARNING
                'companies.id=%: invalid classifications_json preserved', r.id;
            CONTINUE;
        END;
        IF jsonb_typeof(parsed) = 'object'
           AND (
             NOT (parsed ? 'organization_types')
             OR CASE
               WHEN jsonb_typeof(parsed->'organization_types') = 'array'
               THEN jsonb_array_length(parsed->'organization_types') = 0
               ELSE false
             END
           )
        THEN
            UPDATE companies
            SET classifications_json =
                jsonb_set(parsed, '{organization_types}', '["РСО"]'::jsonb)::text
            WHERE id = r.id;
        END IF;
    END LOOP;
END
$classifications$;

-- Retain catalog names after catalogs can be deleted.
UPDATE doc_catalog_analysis_runs r
SET catalog_name_snapshot = c.name
FROM doc_catalogs c
WHERE r.catalog_id = c.id
  AND coalesce(r.catalog_name_snapshot, '') = '';

-- ONE-TIME LEGACY MEMBERSHIP BACKFILL ONLY.
-- This copies existing analytics grants and company creators into membership
-- scope. It DOES NOT create module grants, admins, users, or passwords.
DO $memberships$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settings
        WHERE key = 'company_membership_backfill_v1' AND value = 'done'
    ) THEN
        INSERT INTO company_memberships
            (user_id, company_id, participation_type, is_primary,
             created_at, updated_at)
        SELECT g.user_id, c.id, 'legacy_access', false, now(), now()
        FROM user_module_access g
        CROSS JOIN companies c
        WHERE g.module_key = 'analytics'
        ON CONFLICT (user_id, company_id) DO NOTHING;

        INSERT INTO company_memberships
            (user_id, company_id, participation_type, is_primary,
             created_at, updated_at)
        SELECT c.created_by, c.id, 'creator', false, now(), now()
        FROM companies c
        WHERE c.created_by IS NOT NULL
        ON CONFLICT (user_id, company_id) DO NOTHING;

        INSERT INTO settings(key, value)
        VALUES ('company_membership_backfill_v1', 'done')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
    END IF;
END
$memberships$;

COMMIT;

-- IMPORTANT: no module access was granted by this migration. Before app use,
-- ordinary users require explicit module assignments by an administrator.
SELECT
  'WARNING: ordinary users need explicit module assignments before app use; '
  'this migration intentionally granted no module access.' AS migration_warning;