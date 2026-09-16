# -*- coding: utf-8 -*-
"""Focused regressions for full-range and immutable per-file AI analysis."""
import hashlib
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

import osv_ai_summary as service


def source(source_id, filename, start, end, account='51', digest=None):
    relative_path = 'batch/' + filename
    digest = digest or ('a' * 64)
    return {
        'source_id': source_id,
        'sha256': digest,
        'relative_path': relative_path,
        'filename': filename,
        'account': account,
        'period': f'{start}–{end}',
        'start': date.fromisoformat(start),
        'end': date.fromisoformat(end),
    }


class OsvAiSummaryTests(unittest.TestCase):
    def test_range_includes_2025_and_marks_real_gap(self):
        evidence = service.range_evidence([
            source('1' * 64, '2025.xlsx', '2025-01-01', '2025-12-31'),
            source('2' * 64, '2026.xlsx', '2026-03-01', '2026-06-30'),
        ])
        self.assertEqual(evidence['start'], date(2025, 1, 1))
        self.assertEqual(evidence['end'], date(2026, 6, 30))
        self.assertEqual(evidence['missing_months'], ['2026-01', '2026-02'])
        self.assertEqual(evidence['missing_intervals'], [
            (date(2026, 1, 1), date(2026, 2, 28)),
        ])

    def test_daily_sources_expose_day_level_gaps(self):
        evidence = service.range_evidence([
            source('1' * 64, 'day-1.xlsx', '2025-01-01', '2025-01-01'),
            source('2' * 64, 'day-3.xlsx', '2025-01-03', '2025-01-03'),
        ])
        self.assertEqual(evidence['missing_months'], [])
        self.assertEqual(evidence['missing_intervals'], [
            (date(2025, 1, 2), date(2025, 1, 2)),
        ])

    def test_legacy_daily_rows_extend_aggregate_range_without_file_guess(self):
        evidence = service.range_evidence([
            source('1' * 64, 'monthly.xlsx', '2026-01-01', '2026-01-31'),
        ], extra_dates=[date(2025, 1, 1), date(2025, 1, 2)])
        self.assertEqual(evidence['start'], date(2025, 1, 1))
        self.assertEqual(evidence['end'], date(2026, 1, 31))
        self.assertEqual(
            evidence['missing_intervals'],
            [(date(2025, 1, 3), date(2025, 12, 31))])

    def test_same_path_new_hash_is_a_distinct_source_version(self):
        relative = 'batch/report.xlsx'
        first_id = hashlib.sha256(
            f'{relative}\0{"a" * 64}'.encode()).hexdigest()
        second_id = hashlib.sha256(
            f'{relative}\0{"b" * 64}'.encode()).hexdigest()
        uploads = [{
            'source_manifest': [
                {'source_id': first_id, 'sha256': 'a' * 64,
                 'relative_path': relative, 'account': '51',
                 'period': '2025', 'report_start': '2025-01-01',
                 'report_end': '2025-12-31'},
                {'source_id': second_id, 'sha256': 'b' * 64,
                 'relative_path': relative, 'account': '51',
                 'period': '2026', 'report_start': '2026-01-01',
                 'report_end': '2026-12-31'},
            ],
        }]
        result = service.accepted_sources(uploads)
        self.assertEqual([item['source_id'] for item in result],
                         [first_id, second_id])
        old_revision = service.source_set_revision(result[:1])
        new_revision = service.source_set_revision(result)
        self.assertNotEqual(old_revision, new_revision)
        self.assertEqual(
            new_revision, service.source_set_revision(list(reversed(result))))

    def test_file_prompt_contains_only_selected_file_facts(self):
        company = type('Company', (), {'name': 'Тест'})()
        selected = source(
            '1' * 64, 'selected.xlsx', '2025-01-01', '2025-01-31')
        prompt = service._file_prompt(company, selected, {
                'top': {'obd': 120, 'obk': 100, 'skd': 20, 'skk': 0}})
        self.assertIn('selected.xlsx', prompt)
        self.assertIn('120.00', prompt)
        self.assertNotIn('other.xlsx', prompt)

    def test_changed_file_version_is_not_used_for_aggregate_facts(self):
        company_id = 7
        with tempfile.TemporaryDirectory() as root:
            directory = os.path.join(root, str(company_id), 'batch')
            os.makedirs(directory)
            path = os.path.join(directory, 'report.xlsx')
            with open(path, 'wb') as stream:
                stream.write(b'changed')
            item = source(
                '1' * 64, 'report.xlsx', '2025-01-01', '2025-01-31',
                digest=hashlib.sha256(b'accepted').hexdigest())
            with patch.object(
                    service, 'parse_osv',
                    side_effect=AssertionError('changed source was parsed')):
                facts, unavailable = service.verified_source_facts(
                    company_id, [item], root)
        self.assertEqual(facts, [])
        self.assertIn('версия файла не совпадает', unavailable[0])


if __name__ == '__main__':
    unittest.main()