-- psql: запускать с ON_ERROR_STOP=1; ошибка автоматически откатывает всю транзакцию.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';
SELECT pg_advisory_xact_lock(20260922, 20260901);

-- Строгая проверка: только исходная схема 01.09.2026, без частичного апгрейда.
DO $preflight$
DECLARE t text; c text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ai_providers', 'ai_summaries', 'audit_log', 'companies', 'daily_rows', 'doc_analysis_runs', 'doc_analysis_templates', 'doc_catalog_analysis_runs', 'doc_catalogs', 'doc_check_results', 'doc_check_rules', 'doc_file_marks', 'doc_file_pages', 'doc_files', 'doc_findings', 'doc_free_analysis_results', 'doc_mark_buttons', 'doc_packages', 'doc_routing_rules', 'email_rules', 'settings', 'uploads', 'user_module_access', 'users'] LOOP
    IF to_regclass(format('public.%I', t)) IS NULL THEN
      RAISE EXCEPTION 'Отсутствует базовая таблица public.%', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY ARRAY['analog_climate_references', 'analog_cost_index_references', 'analog_cost_rows', 'analog_diagnostics', 'analog_documents', 'analog_imports', 'analog_network_attributes', 'analog_organization_roles', 'analog_passport_versions', 'analog_passports', 'analog_sources', 'analog_ssr_chapters', 'analog_ssr_items', 'analog_ssr_signatures', 'analog_teps', 'company_contacts', 'company_dadata_profiles', 'company_dadata_snapshots', 'company_finances', 'company_licenses', 'company_memberships', 'company_okveds', 'consumer_collection_accruals', 'consumer_collection_norms', 'consumer_payment_allocations', 'cost_analytic_mappings', 'dadata_diagnostic_events', 'daily_reconciliation_alert_deliveries', 'daily_reconciliation_alerts', 'debtor_analytic_mappings', 'historical_imports', 'historical_mappings', 'historical_metric_values', 'historical_raw_values', 'historical_year_versions', 'limit_bot_dialogs', 'limit_bot_outbound', 'limit_changes', 'limit_comparison_resolutions', 'limit_comparisons', 'limit_datasets', 'limit_deliveries', 'limit_delivery_attempts', 'limit_delivery_parts', 'limit_max_events', 'limit_moderation_requests', 'limit_objects', 'limit_recipients', 'limit_row_snapshots', 'limit_subscriptions', 'limit_version_tombstones', 'limit_versions', 'metric_layouts', 'one_c_attempts', 'one_c_channels', 'one_c_idempotency', 'osv_file_ai_summaries', 'rating_profiles', 'revenue_analytic_mappings'] LOOP
    IF to_regclass(format('public.%I', t)) IS NOT NULL THEN
      RAISE EXCEPTION 'Объект target-only уже существует: public.%', t;
    END IF;
  END LOOP;
  FOR t, c IN SELECT * FROM (VALUES ('ai_summaries','analysis_start'), ('ai_summaries','analysis_end'), ('ai_summaries','source_revision'), ('companies','internal_code'), ('companies','identity_key'), ('companies','status'), ('companies','is_rso'), ('companies','curator_user_id'), ('companies','ogrn'), ('companies','kpp'), ('companies','opf_short'), ('companies','legal_status'), ('companies','registration_date'), ('companies','ogrn_date'), ('companies','actuality_date'), ('companies','liquidation_date'), ('companies','legal_address'), ('companies','address_quality'), ('companies','address_ids_json'), ('companies','director_position'), ('companies','primary_okved'), ('companies','classifications_json'), ('companies','labels_json'), ('companies','dadata_source'), ('companies','dadata_received_at'), ('companies','dadata_actuality_date'), ('companies','dadata_sync_status'), ('companies','dadata_last_error'), ('companies','profile_version'), ('companies','osv_source_revision'), ('companies','updated_at'), ('daily_rows','source_type'), ('uploads','metrics_json'), ('uploads','source_manifest_json'), ('users','password_changed_at'), ('users','password_change_required'), ('users','password_version')) AS x(t,c) LOOP
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=t AND column_name=c) THEN
      RAISE EXCEPTION 'Колонка partial migration уже существует: public.%.%', t, c;
    END IF;
  END LOOP;
END $preflight$;

-- Служебная схема хранит только ID строк membership, добавленных этим UP.
CREATE SCHEMA _migration_20260922;
CREATE TABLE _migration_20260922.membership_ids (id integer PRIMARY KEY);
ALTER TABLE "ai_summaries" ADD COLUMN "analysis_start" DATE;
ALTER TABLE "ai_summaries" ADD COLUMN "analysis_end" DATE;
ALTER TABLE "ai_summaries" ADD COLUMN "source_revision" VARCHAR(64) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "internal_code" VARCHAR(80) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "identity_key" VARCHAR(80);
ALTER TABLE "companies" ADD COLUMN "status" VARCHAR(20) DEFAULT 'active' NOT NULL;
ALTER TABLE "companies" ADD COLUMN "is_rso" BOOLEAN DEFAULT true NOT NULL;
ALTER TABLE "companies" ADD COLUMN "curator_user_id" INTEGER;
ALTER TABLE "companies" ADD COLUMN "ogrn" VARCHAR(20) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "kpp" VARCHAR(20) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "opf_short" VARCHAR(120) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "legal_status" VARCHAR(40) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "registration_date" DATE;
ALTER TABLE "companies" ADD COLUMN "ogrn_date" DATE;
ALTER TABLE "companies" ADD COLUMN "actuality_date" DATE;
ALTER TABLE "companies" ADD COLUMN "liquidation_date" DATE;
ALTER TABLE "companies" ADD COLUMN "legal_address" TEXT DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "address_quality" VARCHAR(40) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "address_ids_json" TEXT DEFAULT '{}';
ALTER TABLE "companies" ADD COLUMN "director_position" VARCHAR(255) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "primary_okved" VARCHAR(32) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "classifications_json" TEXT DEFAULT '{}';
ALTER TABLE "companies" ADD COLUMN "labels_json" TEXT DEFAULT '[]';
ALTER TABLE "companies" ADD COLUMN "dadata_source" VARCHAR(40) DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "dadata_received_at" TIMESTAMP WITH TIME ZONE;
ALTER TABLE "companies" ADD COLUMN "dadata_actuality_date" DATE;
ALTER TABLE "companies" ADD COLUMN "dadata_sync_status" VARCHAR(24) DEFAULT 'never';
ALTER TABLE "companies" ADD COLUMN "dadata_last_error" TEXT DEFAULT '';
ALTER TABLE "companies" ADD COLUMN "profile_version" INTEGER DEFAULT 1 NOT NULL;
ALTER TABLE "companies" ADD COLUMN "osv_source_revision" VARCHAR(80) DEFAULT '' NOT NULL;
ALTER TABLE "companies" ADD COLUMN "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT now();
ALTER TABLE "daily_rows" ADD COLUMN "source_type" VARCHAR(40) DEFAULT 'unknown' NOT NULL;
ALTER TABLE "uploads" ADD COLUMN "metrics_json" TEXT DEFAULT '{}';
-- Старые загрузки не имеют доказуемого manifest: сохраняем честный NULL.
-- ORM задаёт [] только новым строкам на уровне приложения.
ALTER TABLE "uploads" ADD COLUMN "source_manifest_json" TEXT;
ALTER TABLE "users" ADD COLUMN "password_changed_at" TIMESTAMP WITH TIME ZONE;
ALTER TABLE "users" ADD COLUMN "password_change_required" BOOLEAN DEFAULT false NOT NULL;
ALTER TABLE "users" ADD COLUMN "password_version" INTEGER DEFAULT 1 NOT NULL;

-- Все 59 target-only таблиц из текущей SQLAlchemy metadata.

CREATE TABLE analog_climate_references (
	id SERIAL NOT NULL, 
	address_pattern VARCHAR(500) NOT NULL, 
	climate_zone VARCHAR(32) NOT NULL, 
	climate_subzone VARCHAR(32), 
	source TEXT NOT NULL, 
	reference_version VARCHAR(80) NOT NULL, 
	reference_date DATE NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE TABLE analog_cost_index_references (
	id SERIAL NOT NULL, 
	effective_date DATE NOT NULL, 
	price_level VARCHAR(120) NOT NULL, 
	factor NUMERIC(18, 8) NOT NULL, 
	source TEXT NOT NULL, 
	reference_version VARCHAR(80) NOT NULL, 
	reference_date DATE NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analog_cost_index_version UNIQUE (effective_date, price_level, reference_version)
)

;

CREATE TABLE dadata_diagnostic_events (
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempt_id VARCHAR(32) NOT NULL, 
	stage VARCHAR(32) NOT NULL, 
	duration_ms INTEGER NOT NULL, 
	retry_no INTEGER NOT NULL, 
	http_status INTEGER, 
	category VARCHAR(32), 
	outcome VARCHAR(24), 
	PRIMARY KEY (id)
)

;

CREATE TABLE limit_max_events (
	id SERIAL NOT NULL, 
	event_id VARCHAR(160) NOT NULL, 
	payload_json TEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	processing_started_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id)
)

;

CREATE TABLE limit_recipients (
	id SERIAL NOT NULL, 
	max_user_id VARCHAR(120) NOT NULL, 
	organization_inn VARCHAR(12), 
	surname_initials VARCHAR(255), 
	phone VARCHAR(40), 
	phone_verified BOOLEAN NOT NULL, 
	consent_granted_at TIMESTAMP WITH TIME ZONE, 
	access_status VARCHAR(24) NOT NULL, 
	stopped_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_recipient_max_user UNIQUE (max_user_id)
)

;

CREATE TABLE limit_version_tombstones (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	dataset_id INTEGER NOT NULL, 
	source_path VARCHAR(1000) NOT NULL, 
	source_sha256 VARCHAR(64) NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_version_tombstone_version UNIQUE (version_id)
)

;

CREATE TABLE analog_passports (
	id SERIAL NOT NULL, 
	title VARCHAR(1000) NOT NULL, 
	address TEXT, 
	object_type VARCHAR(32) NOT NULL, 
	climate_zone VARCHAR(32), 
	climate_subzone VARCHAR(32), 
	climate_source TEXT, 
	climate_reference_version VARCHAR(80), 
	climate_reference_date DATE, 
	cadastral_number VARCHAR(64), 
	natural_conditions TEXT, 
	construction_object TEXT, 
	capacity_kw NUMERIC(18, 6), 
	work_type VARCHAR(32), 
	archived BOOLEAN NOT NULL, 
	current_version_id INTEGER, 
	created_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	extracted_json TEXT NOT NULL, 
	overrides_json TEXT NOT NULL, 
	provenance_json TEXT NOT NULL, 
	legacy_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;

CREATE TABLE limit_datasets (
	id SERIAL NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	scope TEXT, 
	owner_id INTEGER NOT NULL, 
	current_version_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
)

;

CREATE TABLE rating_profiles (
	id SERIAL NOT NULL, 
	owner_id INTEGER NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	config_json TEXT NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_rating_profile_owner_name UNIQUE (owner_id, name), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_imports (
	id SERIAL NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	ssr_hash VARCHAR(64) NOT NULL, 
	source_filename VARCHAR(500), 
	ssr_filename VARCHAR(500), 
	raw_conclusion_path VARCHAR(1000), 
	raw_ssr_path VARCHAR(1000), 
	target_passport_id INTEGER, 
	status VARCHAR(24) NOT NULL, 
	preview_json TEXT NOT NULL, 
	diagnostics_json TEXT NOT NULL, 
	created_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(target_passport_id) REFERENCES analog_passports (id) ON DELETE SET NULL, 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;

CREATE TABLE analog_passport_versions (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	version_no INTEGER NOT NULL, 
	conclusion_number VARCHAR(255) NOT NULL, 
	approval_date DATE, 
	examination_result VARCHAR(24) NOT NULL, 
	expert_object_name TEXT, 
	cadastral_number VARCHAR(64), 
	natural_conditions TEXT, 
	construction_object TEXT, 
	source_hash VARCHAR(64) NOT NULL, 
	ssr_hash VARCHAR(64) NOT NULL, 
	schema_version VARCHAR(40), 
	price_year INTEGER, 
	price_quarter INTEGER, 
	total_cost NUMERIC(20, 6), 
	adjusted_cost NUMERIC(20, 6), 
	adjustment_factor NUMERIC(18, 8), 
	adjustment_provenance TEXT, 
	adjustment_reference_version VARCHAR(80), 
	adjustment_reference_date DATE, 
	adjustment_source TEXT, 
	adjustment_price_level VARCHAR(120), 
	payload_json TEXT, 
	imported_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analog_passport_version UNIQUE (passport_id, version_no), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(imported_by) REFERENCES users (id)
)

;

CREATE TABLE company_contacts (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	contact_type VARCHAR(40) NOT NULL, 
	label VARCHAR(120), 
	name VARCHAR(255), 
	value VARCHAR(500) NOT NULL, 
	is_internal BOOLEAN NOT NULL, 
	source VARCHAR(40), 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE company_dadata_profiles (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	normalized_json TEXT, 
	extended_json TEXT, 
	source VARCHAR(40), 
	received_at TIMESTAMP WITH TIME ZONE, 
	actuality_date DATE, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	accepted_by INTEGER, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (company_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(accepted_by) REFERENCES users (id)
)

;

CREATE TABLE company_dadata_snapshots (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	query_inn VARCHAR(20), 
	query_ogrn VARCHAR(20), 
	received_at TIMESTAMP WITH TIME ZONE, 
	actuality_date DATE, 
	response_hash VARCHAR(64), 
	status VARCHAR(24) NOT NULL, 
	normalized_json TEXT, 
	response_json TEXT, 
	error_text TEXT, 
	requested_by INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(requested_by) REFERENCES users (id)
)

;

CREATE TABLE company_finances (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	year INTEGER NOT NULL, 
	revenue FLOAT, 
	expense FLOAT, 
	profit FLOAT, 
	details_json TEXT, 
	source VARCHAR(40), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_company_finance_year UNIQUE (company_id, year), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE company_licenses (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	number VARCHAR(255), 
	activity TEXT, 
	status VARCHAR(80), 
	details_json TEXT, 
	source VARCHAR(40), 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE company_memberships (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	company_id INTEGER NOT NULL, 
	participation_type VARCHAR(40) NOT NULL, 
	is_primary BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_company_membership_user_company UNIQUE (user_id, company_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE company_okveds (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(500), 
	is_main BOOLEAN NOT NULL, 
	source VARCHAR(40), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_company_okved_code UNIQUE (company_id, code), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE consumer_collection_accruals (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	accrual_date DATE NOT NULL, 
	accrual_document VARCHAR(500) NOT NULL, 
	account VARCHAR(32) NOT NULL, 
	analytic_key VARCHAR(64) NOT NULL, 
	entry_type VARCHAR(20) NOT NULL, 
	accrued FLOAT NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	source_version VARCHAR(120) NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_consumer_collection_accrual_source UNIQUE (company_id, accrual_document), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE consumer_collection_norms (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	plan_percent FLOAT NOT NULL, 
	approved BOOLEAN NOT NULL, 
	approved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_to DATE NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	source_version VARCHAR(120) NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_consumer_collection_norm_source UNIQUE (company_id, category, valid_from, valid_to, source_name, source_version), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE consumer_payment_allocations (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	payment_date DATE NOT NULL, 
	payment_document VARCHAR(500) NOT NULL, 
	accrual_document VARCHAR(500) NOT NULL, 
	entry_type VARCHAR(20) NOT NULL, 
	allocated_amount FLOAT NOT NULL, 
	source_name VARCHAR(200) NOT NULL, 
	source_version VARCHAR(120) NOT NULL, 
	imported_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_consumer_payment_allocation_source UNIQUE (company_id, payment_document, accrual_document), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE cost_analytic_mappings (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	account VARCHAR(2) NOT NULL, 
	analytic_key VARCHAR(64) NOT NULL, 
	analytic_label VARCHAR(1000) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	target_account VARCHAR(5) NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_to DATE NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_cost_mapping_company_account_key_period UNIQUE (company_id, account, analytic_key, valid_from, valid_to), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
)

;

CREATE TABLE daily_reconciliation_alerts (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	account VARCHAR(80) NOT NULL, 
	side VARCHAR(12) NOT NULL, 
	period_start DATE NOT NULL, 
	period_end DATE NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	generation INTEGER NOT NULL, 
	difference_cents BIGINT NOT NULL, 
	source_file VARCHAR(500), 
	opened_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_reconciliation_alert_identity UNIQUE (company_id, account, side, period_start, period_end), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE debtor_analytic_mappings (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	account VARCHAR(32) NOT NULL, 
	analytic_key VARCHAR(64) NOT NULL, 
	analytic_label VARCHAR(1000) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	effective_from DATE NOT NULL, 
	effective_to DATE NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_debtor_mapping_company_account_key_period UNIQUE (company_id, account, analytic_key, effective_from, effective_to), 
	CONSTRAINT ck_debtor_mapping_account CHECK ((account = '62.01' OR account LIKE '76.%%')), 
	CONSTRAINT ck_debtor_mapping_period CHECK (effective_from <= effective_to), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
)

;

CREATE TABLE historical_imports (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	uploaded_by INTEGER NOT NULL, 
	original_filename VARCHAR(500) NOT NULL, 
	stored_path VARCHAR(1000) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	is_partial BOOLEAN NOT NULL, 
	partial_confirmed BOOLEAN NOT NULL, 
	preview_json TEXT NOT NULL, 
	validation_json TEXT NOT NULL, 
	error_text TEXT, 
	created_at TIMESTAMP WITH TIME ZONE, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(uploaded_by) REFERENCES users (id) ON DELETE RESTRICT
)

;

CREATE TABLE historical_mappings (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	source_label TEXT NOT NULL, 
	source_normalized VARCHAR(500) NOT NULL, 
	canonical_key VARCHAR(240) NOT NULL, 
	canonical_label TEXT NOT NULL, 
	confirmed_by INTEGER, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_historical_mapping_company_source UNIQUE (company_id, kind, source_normalized), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(confirmed_by) REFERENCES users (id)
)

;

CREATE TABLE limit_bot_dialogs (
	id SERIAL NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	dataset_id INTEGER, 
	step VARCHAR(32) NOT NULL, 
	values_json TEXT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (recipient_id), 
	FOREIGN KEY(recipient_id) REFERENCES limit_recipients (id) ON DELETE CASCADE, 
	FOREIGN KEY(dataset_id) REFERENCES limit_datasets (id)
)

;

CREATE TABLE limit_moderation_requests (
	id SERIAL NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	dataset_id INTEGER NOT NULL, 
	initial_uin VARCHAR(120) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	group_chat_id VARCHAR(120), 
	moderation_message_id VARCHAR(120), 
	decided_by_max_user_id VARCHAR(120), 
	decided_at TIMESTAMP WITH TIME ZONE, 
	decision_reason TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(recipient_id) REFERENCES limit_recipients (id) ON DELETE CASCADE, 
	FOREIGN KEY(dataset_id) REFERENCES limit_datasets (id)
)

;

CREATE TABLE limit_subscriptions (
	id SERIAL NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	dataset_id INTEGER NOT NULL, 
	uin VARCHAR(120) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_subscription_recipient_dataset_uin UNIQUE (recipient_id, dataset_id, uin), 
	FOREIGN KEY(recipient_id) REFERENCES limit_recipients (id) ON DELETE CASCADE, 
	FOREIGN KEY(dataset_id) REFERENCES limit_datasets (id) ON DELETE CASCADE
)

;

CREATE TABLE limit_versions (
	id SERIAL NOT NULL, 
	dataset_id INTEGER NOT NULL, 
	previous_version_id INTEGER, 
	uploaded_by INTEGER NOT NULL, 
	source_date DATE NOT NULL, 
	uploaded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	accepted_by INTEGER, 
	status VARCHAR(24) NOT NULL, 
	source_name VARCHAR(500) NOT NULL, 
	source_path VARCHAR(1000) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	parser_version VARCHAR(80), 
	snapshot_json TEXT, 
	warnings_json TEXT, 
	error_text TEXT, 
	is_unchanged BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_version_dataset_sha UNIQUE (dataset_id, sha256), 
	FOREIGN KEY(dataset_id) REFERENCES limit_datasets (id) ON DELETE CASCADE, 
	FOREIGN KEY(previous_version_id) REFERENCES limit_versions (id) ON DELETE SET NULL, 
	FOREIGN KEY(uploaded_by) REFERENCES users (id), 
	FOREIGN KEY(accepted_by) REFERENCES users (id)
)

;

CREATE TABLE metric_layouts (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	company_id INTEGER NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	metric_id VARCHAR(128) NOT NULL, 
	layout_json TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_metric_layout_user_company_mode_metric UNIQUE (user_id, company_id, mode, metric_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE one_c_channels (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	login VARCHAR(120) NOT NULL, 
	password_hash TEXT NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	allow_http BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	last_received_at TIMESTAMP WITH TIME ZONE, 
	last_receipt_id VARCHAR(64), 
	last_raw_json TEXT, 
	last_row_count INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	UNIQUE (last_receipt_id)
)

;

CREATE TABLE osv_file_ai_summaries (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	source_sha256 VARCHAR(64) NOT NULL, 
	relative_path TEXT NOT NULL, 
	filename VARCHAR(500) NOT NULL, 
	account VARCHAR(32), 
	period_label VARCHAR(200), 
	report_start DATE, 
	report_end DATE, 
	status VARCHAR(20) NOT NULL, 
	content TEXT, 
	error_text TEXT, 
	provider_name VARCHAR(100), 
	model_name VARCHAR(200), 
	prompt_tokens INTEGER, 
	completion_tokens INTEGER, 
	queued_at TIMESTAMP WITH TIME ZONE, 
	generated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_osv_file_ai_company_source UNIQUE (company_id, source_id), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
)

;

CREATE TABLE revenue_analytic_mappings (
	id SERIAL NOT NULL, 
	company_id INTEGER NOT NULL, 
	account VARCHAR(5) NOT NULL, 
	analytic_key VARCHAR(64) NOT NULL, 
	analytic_label VARCHAR(1000) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	updated_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_revenue_mapping_company_account_key UNIQUE (company_id, account, analytic_key), 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
)

;

CREATE TABLE analog_cost_rows (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	ordinal VARCHAR(40), 
	name VARCHAR(1000) NOT NULL, 
	submitted_cost NUMERIC(20, 6), 
	approved_cost NUMERIC(20, 6), 
	change_cost NUMERIC(20, 6), 
	price_level VARCHAR(120), 
	vat VARCHAR(80), 
	footnote TEXT, 
	change_reason TEXT, 
	provenance TEXT, 
	version_id INTEGER NOT NULL, 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_diagnostics (
	id SERIAL NOT NULL, 
	version_id INTEGER, 
	import_id INTEGER, 
	severity VARCHAR(16) NOT NULL, 
	code VARCHAR(80) NOT NULL, 
	message TEXT NOT NULL, 
	path VARCHAR(500), 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_documents (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	parent_id INTEGER, 
	ordinal VARCHAR(40), 
	parent_ordinal VARCHAR(40), 
	node_kind VARCHAR(16) NOT NULL, 
	name VARCHAR(1000) NOT NULL, 
	file_format VARCHAR(80), 
	checksum VARCHAR(255), 
	note TEXT, 
	signature_name VARCHAR(1000), 
	signature_checksum VARCHAR(255), 
	version_id INTEGER NOT NULL, 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	provenance TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(parent_id) REFERENCES analog_documents (id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_network_attributes (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	version_id INTEGER NOT NULL, 
	key VARCHAR(120) NOT NULL, 
	value TEXT, 
	value_number NUMERIC(24, 8), 
	unit VARCHAR(80), 
	provenance VARCHAR(500), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analog_network_attribute_version UNIQUE (passport_id, version_id, key), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_organization_roles (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	name VARCHAR(1000) NOT NULL, 
	ogrn VARCHAR(32), 
	inn VARCHAR(32), 
	kpp VARCHAR(32), 
	email VARCHAR(320), 
	address TEXT, 
	version_id INTEGER NOT NULL, 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	provenance TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analog_organization_role UNIQUE (passport_id, version_id, role), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_sources (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	ssr_hash VARCHAR(64) NOT NULL, 
	source_filename VARCHAR(500), 
	ssr_filename VARCHAR(500), 
	source_size BIGINT NOT NULL, 
	ssr_size BIGINT NOT NULL, 
	raw_conclusion_path VARCHAR(1000) NOT NULL, 
	raw_ssr_path VARCHAR(1000) NOT NULL, 
	parser_version VARCHAR(40), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_analog_source_pair UNIQUE (source_hash, ssr_hash), 
	UNIQUE (version_id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_ssr_chapters (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	number INTEGER NOT NULL, 
	name TEXT, 
	building NUMERIC(20, 6), 
	mounting NUMERIC(20, 6), 
	equipment NUMERIC(20, 6), 
	other NUMERIC(20, 6), 
	total NUMERIC(20, 6), 
	provenance TEXT, 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_ssr_items (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	chapter_number INTEGER NOT NULL, 
	item_type VARCHAR(32), 
	ordinal VARCHAR(40), 
	name TEXT, 
	reason TEXT, 
	total NUMERIC(20, 6), 
	details_json TEXT, 
	signature_name VARCHAR(1000), 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	provenance TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_ssr_signatures (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	role VARCHAR(120), 
	name VARCHAR(1000), 
	position VARCHAR(500), 
	department VARCHAR(500), 
	provenance TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE analog_teps (
	id SERIAL NOT NULL, 
	passport_id INTEGER NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	unit VARCHAR(80), 
	value NUMERIC(24, 8), 
	normalized_value NUMERIC(24, 8), 
	normalized_unit VARCHAR(80), 
	value_text TEXT, 
	provenance VARCHAR(500), 
	version_id INTEGER NOT NULL, 
	extracted_json TEXT NOT NULL, 
	override_json TEXT NOT NULL, 
	effective_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(passport_id) REFERENCES analog_passports (id) ON DELETE CASCADE, 
	FOREIGN KEY(version_id) REFERENCES analog_passport_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE historical_year_versions (
	id SERIAL NOT NULL, 
	import_id INTEGER NOT NULL, 
	company_id INTEGER NOT NULL, 
	year INTEGER NOT NULL, 
	version_no INTEGER NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	activated_at TIMESTAMP WITH TIME ZONE, 
	activated_by INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_historical_year_version UNIQUE (company_id, year, version_no), 
	FOREIGN KEY(import_id) REFERENCES historical_imports (id) ON DELETE CASCADE, 
	FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
	FOREIGN KEY(activated_by) REFERENCES users (id)
)

;

CREATE TABLE limit_bot_outbound (
	id SERIAL NOT NULL, 
	target_id VARCHAR(120) NOT NULL, 
	content TEXT NOT NULL, 
	keyboard_json TEXT, 
	status VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	moderation_request_id INTEGER, 
	provider_message_id VARCHAR(255), 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(moderation_request_id) REFERENCES limit_moderation_requests (id) ON DELETE SET NULL
)

;

CREATE TABLE limit_comparisons (
	id SERIAL NOT NULL, 
	old_version_id INTEGER NOT NULL, 
	new_version_id INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	summary_json TEXT, 
	issues_json TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_comparison_pair UNIQUE (old_version_id, new_version_id), 
	FOREIGN KEY(old_version_id) REFERENCES limit_versions (id) ON DELETE CASCADE, 
	FOREIGN KEY(new_version_id) REFERENCES limit_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE limit_objects (
	id SERIAL NOT NULL, 
	dataset_id INTEGER NOT NULL, 
	uin VARCHAR(120) NOT NULL, 
	title TEXT, 
	address TEXT, 
	last_seen_version_id INTEGER, 
	is_present BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_object_dataset_uin UNIQUE (dataset_id, uin), 
	FOREIGN KEY(dataset_id) REFERENCES limit_datasets (id) ON DELETE CASCADE, 
	FOREIGN KEY(last_seen_version_id) REFERENCES limit_versions (id)
)

;

CREATE TABLE one_c_attempts (
	id SERIAL NOT NULL, 
	channel_id INTEGER, 
	received_at TIMESTAMP WITH TIME ZONE, 
	result VARCHAR(24) NOT NULL, 
	receipt_id VARCHAR(64), 
	byte_size INTEGER NOT NULL, 
	row_count INTEGER, 
	transport VARCHAR(8) NOT NULL, 
	error VARCHAR(300), 
	PRIMARY KEY (id), 
	FOREIGN KEY(channel_id) REFERENCES one_c_channels (id) ON DELETE CASCADE
)

;

CREATE TABLE one_c_idempotency (
	id SERIAL NOT NULL, 
	channel_id INTEGER NOT NULL, 
	key VARCHAR(200) NOT NULL, 
	body_hash VARCHAR(64) NOT NULL, 
	receipt_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_one_c_idempotency_channel_key UNIQUE (channel_id, key), 
	FOREIGN KEY(channel_id) REFERENCES one_c_channels (id) ON DELETE CASCADE
)

;

CREATE TABLE daily_reconciliation_alert_deliveries (
	id SERIAL NOT NULL, 
	alert_id INTEGER NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	generation INTEGER NOT NULL, 
	transition VARCHAR(16) NOT NULL, 
	outbound_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_reconciliation_alert_delivery UNIQUE (alert_id, recipient_id, generation, transition), 
	FOREIGN KEY(alert_id) REFERENCES daily_reconciliation_alerts (id) ON DELETE CASCADE, 
	FOREIGN KEY(recipient_id) REFERENCES limit_recipients (id) ON DELETE CASCADE, 
	FOREIGN KEY(outbound_id) REFERENCES limit_bot_outbound (id) ON DELETE SET NULL
)

;

CREATE TABLE historical_metric_values (
	id SERIAL NOT NULL, 
	year_version_id INTEGER NOT NULL, 
	section_key VARCHAR(100) NOT NULL, 
	section_label VARCHAR(255) NOT NULL, 
	metric_key VARCHAR(240) NOT NULL, 
	metric_label TEXT NOT NULL, 
	series_key VARCHAR(120) NOT NULL, 
	period_label VARCHAR(255) NOT NULL, 
	value_numeric FLOAT, 
	value_text TEXT, 
	unit VARCHAR(40) NOT NULL, 
	is_missing BOOLEAN NOT NULL, 
	is_upper_bound BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_historical_metric_version_key UNIQUE (year_version_id, section_key, metric_key, series_key), 
	FOREIGN KEY(year_version_id) REFERENCES historical_year_versions (id) ON DELETE CASCADE
)

;

CREATE TABLE limit_changes (
	id SERIAL NOT NULL, 
	comparison_id INTEGER NOT NULL, 
	object_id INTEGER, 
	uin VARCHAR(120) NOT NULL, 
	category VARCHAR(80) NOT NULL, 
	field_key VARCHAR(255) NOT NULL, 
	field_label TEXT, 
	old_json TEXT, 
	new_json TEXT, 
	absolute_delta NUMERIC(24, 6), 
	relative_delta NUMERIC(24, 8), 
	source_json TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(comparison_id) REFERENCES limit_comparisons (id) ON DELETE CASCADE, 
	FOREIGN KEY(object_id) REFERENCES limit_objects (id)
)

;

CREATE TABLE limit_comparison_resolutions (
	id SERIAL NOT NULL, 
	comparison_id INTEGER NOT NULL, 
	issue_key VARCHAR(255) NOT NULL, 
	resolution_json TEXT NOT NULL, 
	resolved_by INTEGER NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_resolution_issue UNIQUE (comparison_id, issue_key), 
	FOREIGN KEY(comparison_id) REFERENCES limit_comparisons (id) ON DELETE CASCADE, 
	FOREIGN KEY(resolved_by) REFERENCES users (id)
)

;

CREATE TABLE limit_deliveries (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	object_id INTEGER NOT NULL, 
	recipient_id INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	content TEXT NOT NULL, 
	campaign_key VARCHAR(80) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_delivery_campaign UNIQUE (version_id, object_id, recipient_id, campaign_key), 
	FOREIGN KEY(version_id) REFERENCES limit_versions (id) ON DELETE CASCADE, 
	FOREIGN KEY(object_id) REFERENCES limit_objects (id), 
	FOREIGN KEY(recipient_id) REFERENCES limit_recipients (id)
)

;

CREATE TABLE limit_row_snapshots (
	id SERIAL NOT NULL, 
	version_id INTEGER NOT NULL, 
	object_id INTEGER, 
	source_row INTEGER NOT NULL, 
	stable_key VARCHAR(500), 
	cells_json TEXT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_snapshot_row UNIQUE (version_id, source_row), 
	FOREIGN KEY(version_id) REFERENCES limit_versions (id) ON DELETE CASCADE, 
	FOREIGN KEY(object_id) REFERENCES limit_objects (id)
)

;

CREATE TABLE historical_raw_values (
	id SERIAL NOT NULL, 
	metric_id INTEGER NOT NULL, 
	raw_text TEXT NOT NULL, 
	sheet_name VARCHAR(255) NOT NULL, 
	cell_ref VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (metric_id), 
	FOREIGN KEY(metric_id) REFERENCES historical_metric_values (id) ON DELETE CASCADE
)

;

CREATE TABLE limit_delivery_attempts (
	id SERIAL NOT NULL, 
	delivery_id INTEGER NOT NULL, 
	attempt_no INTEGER NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	response_ref VARCHAR(255), 
	error_text TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(delivery_id) REFERENCES limit_deliveries (id) ON DELETE CASCADE
)

;

CREATE TABLE limit_delivery_parts (
	id SERIAL NOT NULL, 
	delivery_id INTEGER NOT NULL, 
	part_no INTEGER NOT NULL, 
	content TEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	last_error TEXT, 
	provider_message_id VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_delivery_part UNIQUE (delivery_id, part_no), 
	FOREIGN KEY(delivery_id) REFERENCES limit_deliveries (id) ON DELETE CASCADE
)

;

-- Индексы, объявленные в SQLAlchemy metadata.
CREATE INDEX ix_analog_climate_lookup ON analog_climate_references (active, address_pattern);
CREATE INDEX ix_analog_cost_index_lookup ON analog_cost_index_references (active, price_level, effective_date);
CREATE INDEX ix_analog_cost_rows_passport_id ON analog_cost_rows (passport_id);
CREATE INDEX ix_analog_cost_rows_version_id ON analog_cost_rows (version_id);
CREATE INDEX ix_analog_diagnostics_import_id ON analog_diagnostics (import_id);
CREATE INDEX ix_analog_diagnostics_version_id ON analog_diagnostics (version_id);
CREATE INDEX ix_analog_documents_passport_id ON analog_documents (passport_id);
CREATE INDEX ix_analog_documents_version_id ON analog_documents (version_id);
CREATE INDEX ix_analog_import_status_created ON analog_imports (status, created_at);
CREATE INDEX ix_analog_imports_source_hash ON analog_imports (source_hash);
CREATE INDEX ix_analog_imports_ssr_hash ON analog_imports (ssr_hash);
CREATE INDEX ix_analog_imports_target_passport_id ON analog_imports (target_passport_id);
CREATE INDEX ix_analog_network_attributes_passport_id ON analog_network_attributes (passport_id);
CREATE INDEX ix_analog_network_attributes_version_id ON analog_network_attributes (version_id);
CREATE INDEX ix_analog_organization_roles_version_id ON analog_organization_roles (version_id);
CREATE INDEX ix_analog_passport_versions_source_hash ON analog_passport_versions (source_hash);
CREATE INDEX ix_analog_passport_versions_ssr_hash ON analog_passport_versions (ssr_hash);
CREATE INDEX ix_analog_version_conclusion ON analog_passport_versions (conclusion_number);
CREATE INDEX ix_analog_passport_active ON analog_passports (archived, updated_at);
CREATE INDEX ix_analog_passport_search ON analog_passports (object_type, climate_zone, work_type, capacity_kw);
CREATE INDEX ix_analog_passports_archived ON analog_passports (archived);
CREATE INDEX ix_analog_sources_source_hash ON analog_sources (source_hash);
CREATE INDEX ix_analog_sources_ssr_hash ON analog_sources (ssr_hash);
CREATE INDEX ix_analog_ssr_chapters_version_id ON analog_ssr_chapters (version_id);
CREATE INDEX ix_analog_ssr_items_version_id ON analog_ssr_items (version_id);
CREATE INDEX ix_analog_ssr_signatures_version_id ON analog_ssr_signatures (version_id);
CREATE INDEX ix_analog_tep_name_value ON analog_teps (name, value);
CREATE INDEX ix_analog_teps_passport_id ON analog_teps (passport_id);
CREATE INDEX ix_analog_teps_version_id ON analog_teps (version_id);
CREATE INDEX ix_company_contacts_company_id ON company_contacts (company_id);
CREATE INDEX ix_company_dadata_snapshots_company_id ON company_dadata_snapshots (company_id);
CREATE INDEX ix_company_dadata_snapshots_received_at ON company_dadata_snapshots (received_at);
CREATE INDEX ix_company_finances_company_id ON company_finances (company_id);
CREATE INDEX ix_company_licenses_company_id ON company_licenses (company_id);
CREATE INDEX ix_company_memberships_company_id ON company_memberships (company_id);
CREATE INDEX ix_company_memberships_user_id ON company_memberships (user_id);
CREATE INDEX ix_company_okveds_company_id ON company_okveds (company_id);
CREATE INDEX ix_consumer_collection_accruals_accrual_date ON consumer_collection_accruals (accrual_date);
CREATE INDEX ix_consumer_collection_accruals_company_id ON consumer_collection_accruals (company_id);
CREATE INDEX ix_consumer_collection_norms_company_id ON consumer_collection_norms (company_id);
CREATE INDEX ix_consumer_payment_allocations_accrual_document ON consumer_payment_allocations (accrual_document);
CREATE INDEX ix_consumer_payment_allocations_company_id ON consumer_payment_allocations (company_id);
CREATE INDEX ix_consumer_payment_allocations_payment_date ON consumer_payment_allocations (payment_date);
CREATE INDEX ix_cost_analytic_mappings_company_id ON cost_analytic_mappings (company_id);
CREATE INDEX ix_dadata_diagnostic_events_attempt_id ON dadata_diagnostic_events (attempt_id);
CREATE INDEX ix_dadata_diagnostic_events_created_at ON dadata_diagnostic_events (created_at);
CREATE INDEX ix_daily_reconciliation_alert_deliveries_alert_id ON daily_reconciliation_alert_deliveries (alert_id);
CREATE INDEX ix_daily_reconciliation_alert_deliveries_outbound_id ON daily_reconciliation_alert_deliveries (outbound_id);
CREATE INDEX ix_daily_reconciliation_alert_deliveries_recipient_id ON daily_reconciliation_alert_deliveries (recipient_id);
CREATE INDEX ix_daily_reconciliation_alerts_company_id ON daily_reconciliation_alerts (company_id);
CREATE INDEX ix_daily_reconciliation_alerts_status ON daily_reconciliation_alerts (status);
CREATE INDEX ix_debtor_analytic_mappings_company_id ON debtor_analytic_mappings (company_id);
CREATE INDEX ix_debtor_analytic_mappings_effective_from ON debtor_analytic_mappings (effective_from);
CREATE INDEX ix_debtor_analytic_mappings_effective_to ON debtor_analytic_mappings (effective_to);
CREATE INDEX ix_historical_imports_company_id ON historical_imports (company_id);
CREATE INDEX ix_historical_imports_file_hash ON historical_imports (file_hash);
CREATE INDEX ix_historical_mappings_company_id ON historical_mappings (company_id);
CREATE INDEX ix_historical_metric_values_metric_key ON historical_metric_values (metric_key);
CREATE INDEX ix_historical_metric_values_section_key ON historical_metric_values (section_key);
CREATE INDEX ix_historical_metric_values_year_version_id ON historical_metric_values (year_version_id);
CREATE INDEX ix_historical_year_versions_company_id ON historical_year_versions (company_id);
CREATE INDEX ix_historical_year_versions_import_id ON historical_year_versions (import_id);
CREATE INDEX ix_historical_year_versions_is_active ON historical_year_versions (is_active);
CREATE INDEX ix_historical_year_versions_year ON historical_year_versions (year);
CREATE UNIQUE INDEX uq_historical_active_year ON historical_year_versions (company_id, year) WHERE is_active;
CREATE INDEX ix_limit_bot_outbound_moderation_request_id ON limit_bot_outbound (moderation_request_id);
CREATE INDEX ix_limit_bot_outbound_status ON limit_bot_outbound (status);
CREATE INDEX ix_limit_bot_outbound_target_id ON limit_bot_outbound (target_id);
CREATE INDEX ix_limit_changes_category ON limit_changes (category);
CREATE INDEX ix_limit_changes_comparison_id ON limit_changes (comparison_id);
CREATE INDEX ix_limit_changes_object_id ON limit_changes (object_id);
CREATE INDEX ix_limit_changes_uin ON limit_changes (uin);
CREATE INDEX ix_limit_comparison_resolutions_comparison_id ON limit_comparison_resolutions (comparison_id);
CREATE INDEX ix_limit_datasets_current_version_id ON limit_datasets (current_version_id);
CREATE INDEX ix_limit_deliveries_object_id ON limit_deliveries (object_id);
CREATE INDEX ix_limit_deliveries_recipient_id ON limit_deliveries (recipient_id);
CREATE INDEX ix_limit_deliveries_status ON limit_deliveries (status);
CREATE INDEX ix_limit_deliveries_version_id ON limit_deliveries (version_id);
CREATE INDEX ix_limit_delivery_attempts_delivery_id ON limit_delivery_attempts (delivery_id);
CREATE INDEX ix_limit_delivery_parts_delivery_id ON limit_delivery_parts (delivery_id);
CREATE INDEX ix_limit_delivery_parts_status ON limit_delivery_parts (status);
CREATE INDEX ix_limit_moderation_requests_recipient_id ON limit_moderation_requests (recipient_id);
CREATE INDEX ix_limit_moderation_requests_status ON limit_moderation_requests (status);
CREATE INDEX ix_limit_objects_dataset_id ON limit_objects (dataset_id);
CREATE INDEX ix_limit_objects_is_present ON limit_objects (is_present);
CREATE INDEX ix_limit_objects_uin ON limit_objects (uin);
CREATE INDEX ix_limit_recipients_access_status ON limit_recipients (access_status);
CREATE INDEX ix_limit_row_snapshots_object_id ON limit_row_snapshots (object_id);
CREATE INDEX ix_limit_row_snapshots_stable_key ON limit_row_snapshots (stable_key);
CREATE INDEX ix_limit_row_snapshots_version_id ON limit_row_snapshots (version_id);
CREATE INDEX ix_limit_subscriptions_dataset_id ON limit_subscriptions (dataset_id);
CREATE INDEX ix_limit_subscriptions_is_active ON limit_subscriptions (is_active);
CREATE INDEX ix_limit_subscriptions_recipient_id ON limit_subscriptions (recipient_id);
CREATE INDEX ix_limit_subscriptions_uin ON limit_subscriptions (uin);
CREATE INDEX ix_limit_version_tombstones_dataset_id ON limit_version_tombstones (dataset_id);
CREATE INDEX ix_limit_version_tombstones_version_id ON limit_version_tombstones (version_id);
CREATE INDEX ix_limit_versions_dataset_id ON limit_versions (dataset_id);
CREATE INDEX ix_limit_versions_status ON limit_versions (status);
CREATE INDEX ix_metric_layouts_company_id ON metric_layouts (company_id);
CREATE INDEX ix_metric_layouts_metric_id ON metric_layouts (metric_id);
CREATE INDEX ix_metric_layouts_mode ON metric_layouts (mode);
CREATE INDEX ix_metric_layouts_user_id ON metric_layouts (user_id);
CREATE INDEX ix_one_c_attempts_channel_id ON one_c_attempts (channel_id);
CREATE INDEX ix_one_c_attempts_received_at ON one_c_attempts (received_at);
CREATE UNIQUE INDEX ix_one_c_channels_company_id ON one_c_channels (company_id);
CREATE UNIQUE INDEX ix_one_c_channels_login ON one_c_channels (login);
CREATE INDEX ix_one_c_idempotency_channel_id ON one_c_idempotency (channel_id);
CREATE INDEX ix_one_c_idempotency_created_at ON one_c_idempotency (created_at);
CREATE INDEX ix_osv_file_ai_summaries_company_id ON osv_file_ai_summaries (company_id);
CREATE INDEX ix_rating_profiles_owner_id ON rating_profiles (owner_id);
CREATE INDEX ix_revenue_analytic_mappings_company_id ON revenue_analytic_mappings (company_id);

-- Дополнительные ограничения на shared tables.
ALTER TABLE companies ADD CONSTRAINT fk_companies_curator FOREIGN KEY (curator_user_id) REFERENCES users(id);
CREATE UNIQUE INDEX uq_companies_identity_key
  ON companies(identity_key)
  WHERE identity_key IS NOT NULL AND identity_key <> '';

-- Согласованные backfill-правила существующей миграции.
UPDATE companies SET legal_address = address WHERE coalesce(legal_address, '') = '' AND coalesce(address, '') <> '';
UPDATE companies SET status = 'active' WHERE status IS NULL OR status = '';
UPDATE companies SET internal_code = id::varchar(80) WHERE coalesce(btrim(internal_code), '') = '';
UPDATE companies SET is_rso = coalesce(is_rso, true), profile_version = coalesce(profile_version, 1),
  legal_address = coalesce(legal_address, ''), address_quality = coalesce(address_quality, ''),
  address_ids_json = coalesce(address_ids_json, '{}'), classifications_json = coalesce(classifications_json, '{}'),
  labels_json = coalesce(labels_json, '[]'), dadata_source = coalesce(dadata_source, ''),
  dadata_sync_status = coalesce(nullif(dadata_sync_status, ''), 'never'), dadata_last_error = coalesce(dadata_last_error, ''),
  updated_at = coalesce(updated_at, created_at, now());

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
           WHEN length(ogrn_digits) IN (13, 15) THEN 'ogrn:' || ogrn_digits
           WHEN length(inn_digits) IN (10, 12) THEN 'inn:' || inn_digits
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
)
UPDATE companies c
SET identity_key = r.candidate_key
FROM ranked r
WHERE c.id = r.id
  AND r.rn = 1
  AND coalesce(btrim(c.identity_key), '') = '';

-- Только legacy analytics grants и создатели компаний получают membership.
WITH inserted AS (
  INSERT INTO company_memberships (user_id, company_id, participation_type, is_primary, created_at, updated_at)
  SELECT g.user_id, c.id, 'legacy_access', false, now(), now()
  FROM user_module_access g CROSS JOIN companies c WHERE g.module_key = 'analytics'
  ON CONFLICT (user_id, company_id) DO NOTHING RETURNING id
) INSERT INTO _migration_20260922.membership_ids SELECT id FROM inserted;
WITH inserted AS (
  INSERT INTO company_memberships (user_id, company_id, participation_type, is_primary, created_at, updated_at)
  SELECT c.created_by, c.id, 'creator', false, now(), now() FROM companies c WHERE c.created_by IS NOT NULL
  ON CONFLICT (user_id, company_id) DO NOTHING RETURNING id
) INSERT INTO _migration_20260922.membership_ids SELECT id FROM inserted;

INSERT INTO settings(key, value) VALUES ('external_migration_20260922_since_20260901', 'done');
COMMIT;
