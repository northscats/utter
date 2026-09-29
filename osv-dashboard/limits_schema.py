"""Idempotent schema cleanup for retired MAX integration.

Only delivery/registration tables are removed. Financial alerts, limit
versions, comparisons and source snapshots remain untouched.
"""
from sqlalchemy import inspect, text
from database import db


# Explicit child-first order; never use CASCADE, which could remove financial
# data if a future schema introduced another referencing table.
RETIRED_TABLES = (
    'daily_reconciliation_alert_deliveries',
    'limit_delivery_attempts',
    'limit_delivery_parts',
    'limit_bot_dialogs',
    'limit_subscriptions',
    'limit_max_events',
    'limit_deliveries',
    'limit_bot_outbound',
    'limit_moderation_requests',
    'limit_recipients',
)


def migrate_limits_schema():
    """Retire MAX tables/settings and keep the financial snapshot migration."""
    tables = set(inspect(db.engine).get_table_names())
    version_columns = {c['name'] for c in inspect(db.engine).get_columns('limit_versions')} if 'limit_versions' in tables else set()
    with db.engine.begin() as connection:
        if version_columns and 'progress_json' not in version_columns:
            guard = 'IF NOT EXISTS ' if db.engine.dialect.name == 'postgresql' else ''
            connection.execute(text(f"ALTER TABLE limit_versions ADD COLUMN {guard}progress_json TEXT DEFAULT '{{}}'"))
        for table in RETIRED_TABLES:
            if table in tables:
                # Names are static, not interpolated user input. Without
                # CASCADE, unknown external dependencies fail loudly.
                connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        if 'settings' in tables:
            connection.execute(text(
                "DELETE FROM settings WHERE key LIKE 'limits\\_max\\_%' ESCAPE '\\' "
                "OR key LIKE 'limits\\_bot\\_%' ESCAPE '\\'"))
        if (db.engine.dialect.name == 'postgresql'
                and 'limit_row_snapshots' in tables):
            connection.execute(text(
                'ALTER TABLE limit_row_snapshots ALTER COLUMN object_id DROP NOT NULL'))