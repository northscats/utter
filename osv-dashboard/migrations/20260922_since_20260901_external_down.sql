-- psql: запускать с ON_ERROR_STOP=1 и PGOPTIONS='-c migration.allow_down=YES'.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';
SELECT pg_advisory_xact_lock(20260922, 20260901);
DO $guard$ BEGIN
  IF current_setting('migration.allow_down', true) IS DISTINCT FROM 'YES' THEN
    RAISE EXCEPTION 'Откат запрещён: установите SET migration.allow_down = ''YES'' перед запуском';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM settings WHERE key='external_migration_20260922_since_20260901' AND value='done') THEN
    RAISE EXCEPTION 'Маркер UP не найден';
  END IF;
END $guard$;

-- Любые прикладные записи блокируют DOWN; membership допускает только tracked IDs.
DO $empty$ DECLARE t text; n bigint; BEGIN
  FOREACH t IN ARRAY ARRAY['analog_climate_references', 'analog_cost_index_references', 'analog_cost_rows', 'analog_diagnostics', 'analog_documents', 'analog_imports', 'analog_network_attributes', 'analog_organization_roles', 'analog_passport_versions', 'analog_passports', 'analog_sources', 'analog_ssr_chapters', 'analog_ssr_items', 'analog_ssr_signatures', 'analog_teps', 'company_contacts', 'company_dadata_profiles', 'company_dadata_snapshots', 'company_finances', 'company_licenses', 'company_okveds', 'consumer_collection_accruals', 'consumer_collection_norms', 'consumer_payment_allocations', 'cost_analytic_mappings', 'dadata_diagnostic_events', 'daily_reconciliation_alert_deliveries', 'daily_reconciliation_alerts', 'debtor_analytic_mappings', 'historical_imports', 'historical_mappings', 'historical_metric_values', 'historical_raw_values', 'historical_year_versions', 'limit_bot_dialogs', 'limit_bot_outbound', 'limit_changes', 'limit_comparison_resolutions', 'limit_comparisons', 'limit_datasets', 'limit_deliveries', 'limit_delivery_attempts', 'limit_delivery_parts', 'limit_max_events', 'limit_moderation_requests', 'limit_objects', 'limit_recipients', 'limit_row_snapshots', 'limit_subscriptions', 'limit_version_tombstones', 'limit_versions', 'metric_layouts', 'one_c_attempts', 'one_c_channels', 'one_c_idempotency', 'osv_file_ai_summaries', 'rating_profiles', 'revenue_analytic_mappings'] LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO n;
    IF n <> 0 THEN RAISE EXCEPTION 'DOWN остановлен: public.% содержит прикладные строки', t; END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM company_memberships m WHERE NOT EXISTS (SELECT 1 FROM _migration_20260922.membership_ids i WHERE i.id=m.id)) THEN
    RAISE EXCEPTION 'DOWN остановлен: найдены membership, созданные приложением';
  END IF;
END $empty$;
DELETE FROM company_memberships WHERE id IN (SELECT id FROM _migration_20260922.membership_ids);

-- Drop в обратном dependency order.
DROP TABLE "limit_delivery_parts";
DROP TABLE "limit_delivery_attempts";
DROP TABLE "historical_raw_values";
DROP TABLE "limit_row_snapshots";
DROP TABLE "limit_deliveries";
DROP TABLE "limit_comparison_resolutions";
DROP TABLE "limit_changes";
DROP TABLE "historical_metric_values";
DROP TABLE "daily_reconciliation_alert_deliveries";
DROP TABLE "one_c_idempotency";
DROP TABLE "one_c_attempts";
DROP TABLE "limit_objects";
DROP TABLE "limit_comparisons";
DROP TABLE "limit_bot_outbound";
DROP TABLE "historical_year_versions";
DROP TABLE "analog_teps";
DROP TABLE "analog_ssr_signatures";
DROP TABLE "analog_ssr_items";
DROP TABLE "analog_ssr_chapters";
DROP TABLE "analog_sources";
DROP TABLE "analog_organization_roles";
DROP TABLE "analog_network_attributes";
DROP TABLE "analog_documents";
DROP TABLE "analog_diagnostics";
DROP TABLE "analog_cost_rows";
DROP TABLE "revenue_analytic_mappings";
DROP TABLE "osv_file_ai_summaries";
DROP TABLE "one_c_channels";
DROP TABLE "metric_layouts";
DROP TABLE "limit_versions";
DROP TABLE "limit_subscriptions";
DROP TABLE "limit_moderation_requests";
DROP TABLE "limit_bot_dialogs";
DROP TABLE "historical_mappings";
DROP TABLE "historical_imports";
DROP TABLE "debtor_analytic_mappings";
DROP TABLE "daily_reconciliation_alerts";
DROP TABLE "cost_analytic_mappings";
DROP TABLE "consumer_payment_allocations";
DROP TABLE "consumer_collection_norms";
DROP TABLE "consumer_collection_accruals";
DROP TABLE "company_okveds";
DROP TABLE "company_memberships";
DROP TABLE "company_licenses";
DROP TABLE "company_finances";
DROP TABLE "company_dadata_snapshots";
DROP TABLE "company_dadata_profiles";
DROP TABLE "company_contacts";
DROP TABLE "analog_passport_versions";
DROP TABLE "analog_imports";
DROP TABLE "rating_profiles";
DROP TABLE "limit_datasets";
DROP TABLE "analog_passports";
DROP TABLE "limit_version_tombstones";
DROP TABLE "limit_recipients";
DROP TABLE "limit_max_events";
DROP TABLE "dadata_diagnostic_events";
DROP TABLE "analog_cost_index_references";
DROP TABLE "analog_climate_references";

ALTER TABLE companies DROP CONSTRAINT fk_companies_curator;
DROP INDEX uq_companies_identity_key;
ALTER TABLE "users" DROP COLUMN "password_version";
ALTER TABLE "users" DROP COLUMN "password_change_required";
ALTER TABLE "users" DROP COLUMN "password_changed_at";
ALTER TABLE "uploads" DROP COLUMN "source_manifest_json";
ALTER TABLE "uploads" DROP COLUMN "metrics_json";
ALTER TABLE "daily_rows" DROP COLUMN "source_type";
ALTER TABLE "companies" DROP COLUMN "updated_at";
ALTER TABLE "companies" DROP COLUMN "osv_source_revision";
ALTER TABLE "companies" DROP COLUMN "profile_version";
ALTER TABLE "companies" DROP COLUMN "dadata_last_error";
ALTER TABLE "companies" DROP COLUMN "dadata_sync_status";
ALTER TABLE "companies" DROP COLUMN "dadata_actuality_date";
ALTER TABLE "companies" DROP COLUMN "dadata_received_at";
ALTER TABLE "companies" DROP COLUMN "dadata_source";
ALTER TABLE "companies" DROP COLUMN "labels_json";
ALTER TABLE "companies" DROP COLUMN "classifications_json";
ALTER TABLE "companies" DROP COLUMN "primary_okved";
ALTER TABLE "companies" DROP COLUMN "director_position";
ALTER TABLE "companies" DROP COLUMN "address_ids_json";
ALTER TABLE "companies" DROP COLUMN "address_quality";
ALTER TABLE "companies" DROP COLUMN "legal_address";
ALTER TABLE "companies" DROP COLUMN "liquidation_date";
ALTER TABLE "companies" DROP COLUMN "actuality_date";
ALTER TABLE "companies" DROP COLUMN "ogrn_date";
ALTER TABLE "companies" DROP COLUMN "registration_date";
ALTER TABLE "companies" DROP COLUMN "legal_status";
ALTER TABLE "companies" DROP COLUMN "opf_short";
ALTER TABLE "companies" DROP COLUMN "kpp";
ALTER TABLE "companies" DROP COLUMN "ogrn";
ALTER TABLE "companies" DROP COLUMN "curator_user_id";
ALTER TABLE "companies" DROP COLUMN "is_rso";
ALTER TABLE "companies" DROP COLUMN "status";
ALTER TABLE "companies" DROP COLUMN "identity_key";
ALTER TABLE "companies" DROP COLUMN "internal_code";
ALTER TABLE "ai_summaries" DROP COLUMN "source_revision";
ALTER TABLE "ai_summaries" DROP COLUMN "analysis_end";
ALTER TABLE "ai_summaries" DROP COLUMN "analysis_start";
DROP TABLE _migration_20260922.membership_ids;
DROP SCHEMA _migration_20260922;
DELETE FROM settings WHERE key='external_migration_20260922_since_20260901';
COMMIT;
