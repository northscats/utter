"""Static regression assertions for the additive analogue upgrade."""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parent


class AnalogSchemaUpgradeTest(unittest.TestCase):
    def test_postgres_migration_is_version_aware(self):
        sql = (ROOT / 'migrations' / '20260915_analog_search.sql').read_text()
        self.assertIn('DROP CONSTRAINT IF EXISTS uq_analog_network_attribute', sql)
        self.assertIn('DROP INDEX IF EXISTS uq_analog_network_attribute', sql)
        self.assertIn('passport_id, version_id, key', sql)
        self.assertIn('UPDATE analog_network_attributes', sql)
        self.assertIn('ALTER TABLE analog_documents ADD COLUMN IF NOT EXISTS node_kind', sql)

    def test_runtime_upgrade_backfills_all_children_and_fk(self):
        source = (ROOT / 'app.py').read_text()
        self.assertIn('analog_organization_roles', source)
        self.assertIn('analog_teps', source)
        self.assertIn('analog_documents', source)
        self.assertIn('analog_cost_rows', source)
        self.assertIn('ALTER COLUMN version_id SET NOT NULL', source)
        self.assertIn('fk_analog_chapter_version', source)
        self.assertIn('uq_analog_network_attribute_version', source)


if __name__ == '__main__':
    unittest.main()