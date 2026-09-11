BEGIN;

CREATE TABLE IF NOT EXISTS debtor_analytic_mappings (
    id serial PRIMARY KEY,
    company_id integer NOT NULL
        REFERENCES companies(id) ON DELETE CASCADE,
    account varchar(32) NOT NULL,
    analytic_key varchar(64) NOT NULL,
    analytic_label varchar(1000) NOT NULL,
    category varchar(32) NOT NULL,
    effective_from date NOT NULL,
    effective_to date NOT NULL,
    updated_by integer REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_debtor_mapping_company_account_key_period
        UNIQUE (company_id, account, analytic_key,
                effective_from, effective_to),
    CONSTRAINT ck_debtor_mapping_account
        CHECK (account = '62.01' OR account LIKE '76.%'),
    CONSTRAINT ck_debtor_mapping_period
        CHECK (effective_from <= effective_to)
);

ALTER TABLE debtor_analytic_mappings
    ALTER COLUMN account TYPE varchar(32);
ALTER TABLE debtor_analytic_mappings
    DROP CONSTRAINT IF EXISTS ck_debtor_mapping_account;
ALTER TABLE debtor_analytic_mappings
    ADD CONSTRAINT ck_debtor_mapping_account
    CHECK (account = '62.01' OR account LIKE '76.%');

CREATE INDEX IF NOT EXISTS ix_debtor_analytic_mappings_company_id
    ON debtor_analytic_mappings(company_id);
CREATE INDEX IF NOT EXISTS ix_debtor_analytic_mappings_effective_from
    ON debtor_analytic_mappings(effective_from);
CREATE INDEX IF NOT EXISTS ix_debtor_analytic_mappings_effective_to
    ON debtor_analytic_mappings(effective_to);

COMMIT;