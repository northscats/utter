"""Idempotent additive migration for pre-release limits tables."""
from sqlalchemy import inspect, text
from database import db


def migrate_limits_schema():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    declarations = {
        'limit_recipients': {'consent_granted_at': 'TIMESTAMP WITH TIME ZONE'},
        'limit_max_events': {
            'last_error': "TEXT DEFAULT ''",
            'processing_started_at': 'TIMESTAMP WITH TIME ZONE',
            'attempts': 'INTEGER NOT NULL DEFAULT 0',
            'next_attempt_at': 'TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP',
        },
        'limit_bot_outbound': {
            'moderation_request_id': 'INTEGER REFERENCES limit_moderation_requests(id) ON DELETE SET NULL',
            'provider_message_id': "VARCHAR(255) DEFAULT ''",
            'accepted_at': 'TIMESTAMP WITH TIME ZONE',
        },
        'limit_deliveries': {
            'campaign_key': "VARCHAR(80) NOT NULL DEFAULT 'automatic'",
        },
    }
    with db.engine.begin() as connection:
        for table, fields in declarations.items():
            if table not in tables:
                continue
            columns = {column['name'] for column in inspector.get_columns(table)}
            for name, declaration in fields.items():
                if name not in columns:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}'))
        if db.engine.dialect.name == 'postgresql' and 'limit_deliveries' in tables:
            connection.execute(text(
                'ALTER TABLE limit_deliveries DROP CONSTRAINT IF EXISTS '
                'uq_limit_delivery_version_object_recipient'))
            connection.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_limit_delivery_campaign ON '
                'limit_deliveries(version_id, object_id, recipient_id, campaign_key)'))
            connection.execute(text(
                'ALTER TABLE limit_row_snapshots ALTER COLUMN object_id DROP NOT NULL'))