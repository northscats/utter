BEGIN;
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS climate_source text DEFAULT '';
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS climate_reference_version varchar(80) DEFAULT '';
ALTER TABLE analog_passports ADD COLUMN IF NOT EXISTS climate_reference_date date;
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS adjustment_reference_version varchar(80) DEFAULT '';
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS adjustment_reference_date date;
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS adjustment_source text DEFAULT '';
ALTER TABLE analog_passport_versions ADD COLUMN IF NOT EXISTS adjustment_price_level varchar(120) DEFAULT '';
CREATE TABLE IF NOT EXISTS analog_climate_references (
 id serial PRIMARY KEY, address_pattern varchar(500) NOT NULL, climate_zone varchar(32) NOT NULL,
 climate_subzone varchar(32) DEFAULT '', source text NOT NULL, reference_version varchar(80) NOT NULL,
 reference_date date NOT NULL, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_analog_climate_lookup ON analog_climate_references(active,address_pattern);
CREATE TABLE IF NOT EXISTS analog_cost_index_references (
 id serial PRIMARY KEY, effective_date date NOT NULL, price_level varchar(120) NOT NULL,
 factor numeric(18,8) NOT NULL, source text NOT NULL, reference_version varchar(80) NOT NULL,
 reference_date date NOT NULL, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT uq_analog_cost_index_version UNIQUE(effective_date,price_level,reference_version)
);
CREATE INDEX IF NOT EXISTS ix_analog_cost_index_lookup ON analog_cost_index_references(active,price_level,effective_date);
COMMIT;