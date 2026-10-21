"""Isolated SQLite/template/route checks; never import the production app."""
import ast
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
import threading
from unittest.mock import Mock, patch

from flask import Flask, abort, g, render_template, request
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from database import db, LimitDataset, LimitVersion, LimitComparison, LimitChange, LimitObject, Setting, User
from limits_version_display import display_number, version_numbers


ROOT = Path(__file__).resolve().parents[1]


class LimitsPresentationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test',
                               SQLALCHEMY_DATABASE_URI='sqlite://')
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username='presentation', password_hash='unused')
        db.session.add(user)
        db.session.flush()
        self.dataset = LimitDataset(name='A', scope='', owner_id=user.id)
        other = LimitDataset(name='B', scope='', owner_id=user.id)
        db.session.add_all([self.dataset, other])
        db.session.flush()
        for offset, identifier in enumerate((25, 26, 29)):
            db.session.add(LimitVersion(
                id=identifier, dataset_id=(other.id if offset == 1 else self.dataset.id),
                uploaded_by=user.id, source_date=date(2026, 1, 1),
                uploaded_at=datetime(2026, 1, 1) + timedelta(seconds=offset),
                status='accepted', source_name=f'file-{identifier}.xlsx',
                source_path='', sha256=str(identifier)*32))
        db.session.commit()
        self.app.jinja_loader = ChoiceLoader([
            DictLoader({'limits_base.html': '{% block title %}{% endblock %}{% block limits_content %}{% endblock %}',
                        'limits_nav.html': '', 'limits_header_actions.html': ''}),
            FileSystemLoader(str(ROOT / 'templates'))])
        self.app.jinja_env.filters['limit_version_number'] = display_number
        self.app.jinja_env.filters['limit_value'] = str
        self.app.jinja_env.filters['limit_json'] = __import__('json').loads
        self.app.jinja_env.globals.update(
            current_user=SimpleNamespace(is_admin=True),
            csrf_token=lambda: 'csrf',
            url_for=lambda endpoint, **kw: '/' + endpoint + '?' + '&'.join(
                f'{k}={v}' for k, v in kw.items()))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_global_numbers_and_deletion_leave_ids_and_max_setting_unchanged(self):
        Setting.set('max_limits_version_id', '29')
        db.session.commit()
        self.assertEqual(version_numbers(), {25: 1, 26: 2, 29: 3})
        with self.app.test_request_context():
            self.assertEqual(display_number(29), 3)
        # Production requests each own their app context; this fixture holds one.
        g.pop('limits_version_numbers', None)
        db.session.delete(db.session.get(LimitVersion, 26))
        db.session.commit()
        with self.app.test_request_context():
            self.assertEqual(display_number(29), 2)
            self.assertEqual(display_number(26), '—')
        self.assertEqual(Setting.get('max_limits_version_id'), '29')
        self.assertEqual(db.session.get(LimitVersion, 29).source_name, 'file-29.xlsx')

    def test_purged_excluded_and_ties_use_id(self):
        db.session.get(LimitVersion, 26).uploaded_at = datetime(2026, 1, 1)
        db.session.get(LimitVersion, 25).status = 'purged'
        db.session.commit()
        self.assertEqual(version_numbers(), {26: 1, 29: 2})

    def test_analytics_uses_same_global_number(self):
        from limits_dashboard_data import _version_label
        self.assertEqual(_version_label(db.session.get(LimitVersion, 29)),
                         '01.01.2026 · №3')

    def test_review_delete_max_templates_keep_real_identifiers(self):
        version = db.session.get(LimitVersion, 29)
        common = dict(version=version, dataset=self.dataset,
                      comparison=None, row_count=0, object_count=0, warnings=[])
        with self.app.test_request_context():
            review = render_template('limits_version_review.html', **common)
            self.assertIn('Проверка версии #3', review)
            self.assertIn('version_id=29', review)
            deletion = render_template('limits_delete_version.html',
                                       comparison_count=0, fallback=None, blocked=None, **common)
            self.assertIn('Удаление загрузки #3', deletion)
            self.assertIn('version_id=29', deletion)
            bot = render_template('limits_max_bot.html', dataset=self.dataset,
                                  selected_version=version, ready_versions=[version],
                                  users=SimpleNamespace(pages=1, items=[], total=0),
                                  funding_year_choices=[], funding_year_selection=None,
                                  bot_diagnostics=SimpleNamespace(stages=[]))
            self.assertIn('value="29" selected>#3 · file-29.xlsx', bot)
            comparison = SimpleNamespace(
                id=101, old_version_id=25, new_version_id=29, summary_json='{}',
                old_version=db.session.get(LimitVersion, 25), new_version=version)
            report = render_template('limits_comparison.html', comparison=comparison,
                                     changes=[], issues=[], total=0, page=1, pages=1,
                                     available_years=[], ministries=[], categories=[])
            self.assertIn('Старая версия: <b>#1</b>', report)
            self.assertIn('Новая версия: <b>#3</b>', report)
            self.assertIn('comparison_id=101', report)

    def test_dashboard_route_renders_pollable_fragment_with_real_actions(self):
        # Execute only the real route body, without startup/restore/background work.
        node = next(n for n in ast.parse((ROOT / 'app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'limit_notifications')
        node.decorator_list = []
        namespace = dict(Setting=Setting, LimitDataset=LimitDataset,
                         LimitVersion=LimitVersion, LimitComparison=LimitComparison,
                         LimitChange=LimitChange, LimitObject=LimitObject, db=db,
                         request=request, render_template=render_template,
                         _limits_parse_threads={29: object()},
                         _limits_parse_threads_lock=threading.Lock(),
                         current_user=SimpleNamespace(is_admin=True),
                         _limit_dataset_or_404=lambda identifier: db.session.get(LimitDataset, identifier) or abort(404),
                         _limits_upload_source_for_view=lambda: self.dataset,
                         datetime=datetime, timezone=__import__('datetime').timezone,
                         timedelta=timedelta, json=__import__('json'))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'route', 'exec'), namespace)
        self.app.add_url_rule('/limit-notifications', view_func=namespace['limit_notifications'])
        db.session.get(LimitVersion, 29).status = 'parsing'
        db.session.commit()
        response = self.app.test_client().get('/limit-notifications')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<td>#3</td>', html)
        self.assertIn('data-active="1"', html)
        self.assertIn('version_id=29', html)
        self.assertNotIn('window.location.reload', html)
        self.assertIn('replaceWith(next)', html)
        version = db.session.get(LimitVersion, 29)
        version.status = 'review'
        db.session.commit()
        client = self.app.test_client()

        # parse_version commits review before comparison and autoaccept.
        html = client.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('data-active="1"', html)
        self.assertIn('badge parsing">Обработка', html)
        self.assertNotIn('Требуется проверка данных', html)
        self.assertNotIn('Исправьте неоднозначности', html)

        version.previous_version_id = 25
        report = LimitComparison(old_version_id=25, new_version_id=29,
                                 status='ready', issues_json='[]')
        db.session.add(report)
        db.session.commit()
        html = client.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('data-active="1"', html)
        self.assertIn('badge parsing">Обработка', html)

        version.status = 'accepted'
        db.session.commit()
        # Worker may still be doing backups after publication.
        html = client.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('data-active="0"', html)
        self.assertIn('badge accepted">Готово', html)
        self.assertNotIn('<script>', html)

        version.status = 'review'
        report.status = 'needs_resolution'
        report.issues_json = '[{"type":"ambiguous"}]'
        db.session.commit()
        # Real issues win even before the worker has left its registry.
        html = client.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('data-active="0"', html)
        self.assertIn('badge needs_resolution">Требуется проверка данных', html)
        self.assertIn('Исправить сопоставление', html)
        self.assertNotIn('<script>', html)
        report.status = 'ready'
        db.session.commit()
        html = client.get('/limit-notifications').get_data(as_text=True)
        self.assertIn('badge needs_resolution', html)

        # Restart/legacy reviews must offer recovery, not poll forever.
        namespace['_limits_parse_threads'].clear()
        report.issues_json = '[]'
        db.session.commit()
        for has_report in (True, False):
            if not has_report:
                db.session.delete(report)
                db.session.commit()
            html = client.get('/limit-notifications').get_data(as_text=True)
            self.assertIn('data-active="0"', html)
            self.assertIn('badge interrupted">Обработка приостановлена', html)
            self.assertIn('Возобновить обработку', html)
            self.assertNotIn('Требуется проверка данных', html)
            self.assertNotIn('<script>', html)

    def worker_namespace(self):
        node = next(n for n in ast.parse((ROOT / 'app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_limit_parse_in_background')
        namespace = dict(threading=threading, app=self.app, db=db,
                         LimitVersion=LimitVersion, Setting=Setting,
                         _limits_parse_slots=threading.Semaphore(1),
                         _limits_parse_threads={}, _limits_parse_threads_lock=threading.Lock(),
                         )
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'worker', 'exec'), namespace)
        return namespace

    def test_worker_deduplicates_including_waiting_threads_and_cleans_up(self):
        namespace = self.worker_namespace()
        start = namespace['_limit_parse_in_background']
        slots = namespace['_limits_parse_slots']
        slots.acquire()
        with patch('limits_service.process_version') as process:
            thread = start(29)
            try:
                self.assertIs(start(29), thread)
                process.assert_not_called()
            finally:
                slots.release()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            process.assert_called_once_with(29)
            self.assertEqual(namespace['_limits_parse_threads'], {})

    def test_worker_persists_uncaught_failure_without_overwriting_acceptance(self):
        namespace = self.worker_namespace()
        db.session.get(LimitVersion, 29).status = 'parsing'
        db.session.commit()
        with patch('limits_service.process_version', side_effect=RuntimeError('worker failure')):
            thread = namespace['_limit_parse_in_background'](29)
            thread.join(5)
            self.assertFalse(thread.is_alive())
        db.session.expire_all()
        failed = db.session.get(LimitVersion, 29)
        self.assertEqual(failed.status, 'error')
        self.assertIn('Возобновите', failed.error_text)
        failed.status = 'accepted'
        db.session.commit()
        with patch('limits_service.process_version', side_effect=RuntimeError('after acceptance')):
            thread = namespace['_limit_parse_in_background'](29)
            thread.join(5)
        db.session.expire_all()
        self.assertEqual(db.session.get(LimitVersion, 29).status, 'accepted')

    def test_recovery_schedules_parsing_without_mutating_status(self):
        node = next(n for n in ast.parse((ROOT / 'app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_ensure_limits_recovery')
        work = next(n for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.name == 'work')
        # Run only the startup block, not the periodic daemon's infinite loop.
        work.body = [work.body[0]]
        schedule = Mock()
        namespace = dict(app=self.app, LimitVersion=LimitVersion,
                         _limit_parse_in_background=schedule)
        exec(compile(ast.Module(body=[work], type_ignores=[]), 'recovery', 'exec'), namespace)
        db.session.get(LimitVersion, 25).status = 'queued'
        db.session.get(LimitVersion, 26).status = 'review'
        db.session.get(LimitVersion, 29).status = 'parsing'
        db.session.commit()
        namespace['work']()
        self.assertEqual([call.args[0] for call in schedule.call_args_list], [25, 26, 29])
        db.session.expire_all()
        self.assertEqual(db.session.get(LimitVersion, 29).status, 'parsing')


if __name__ == '__main__':
    unittest.main()