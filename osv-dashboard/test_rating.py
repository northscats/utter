# -*- coding: utf-8 -*-
"""Регрессии настраиваемого рейтинга предприятий."""
import json
import os
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone

TMP = tempfile.mkdtemp(prefix='rating_test_')
os.environ['DATABASE_URL'] = f'sqlite:///{TMP}/rating.db'
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import OUTPUT, _dataset_closure_status, app, init_db
from builder import extract_kpis
from database import Company, RatingProfile, Upload, User, UserModuleAccess, db
from rating import (DEFAULT_PROFILE, ProfileValidationError, metric_signal,
                    rank_companies, score_company, validate_profile)

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def check(label, condition):
    print(('PASS' if condition else 'FAIL'), label)
    if not condition:
        raise AssertionError(label)


def login_as(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


check('нижняя граница нормы включительна',
      metric_signal(60, 'dz_days') == 'green')
check('верхняя граница внимания включительна',
      metric_signal(90, 'dz_days') == 'yellow')
check('направление higher соблюдается',
      metric_signal(.19, 'liquidity') == 'red' and
      metric_signal(.5, 'liquidity') == 'green')

profile = validate_profile(DEFAULT_PROFILE)
values = {key: meta['green'] for key, meta in profile['metrics'].items()
          if key != 'nvv_pct'}
scored = score_company(values, profile)
check('все нормы дают 100 баллов', scored['score'] == 100)

partial = score_company({'dz_days': 50}, profile)
check('пропуски не считаются нулём', partial['score'] is None)
check('низкое покрытие явно исключает из рейтинга',
      not partial['ranked'] and 'ниже требуемых' in partial['exclusion_reason'])
low_threshold = deepcopy(profile)
low_threshold['min_coverage'] = 10
partial_ranked = score_company({'dz_days': 50}, low_threshold)
check('при допустимом покрытии веса нормализуются',
      partial_ranked['score'] == 100)

ties = [
    dict(company_id=2, company_name='Бета', score=80, ranked=True),
    dict(company_id=1, company_name='Альфа', score=80, ranked=True),
    dict(company_id=3, company_name='Гамма', score=None, ranked=False),
]
ranked = rank_companies(ties)
check('равные баллы получают одно место',
      ranked[0]['place'] == ranked[1]['place'] == 1)
check('равные баллы сортируются детерминированно',
      [r['company_id'] for r in ranked] == [1, 2, 3])

bad = deepcopy(DEFAULT_PROFILE)
bad['metrics']['dz_days']['weight'] = 50
try:
    validate_profile(bad)
    raise AssertionError('невалидная сумма весов принята')
except ProfileValidationError:
    print('PASS невалидная сумма весов отклоняется')
bad_nan = deepcopy(DEFAULT_PROFILE)
bad_nan['min_coverage'] = float('nan')
try:
    validate_profile(bad_nan)
    raise AssertionError('NaN принят')
except ProfileValidationError:
    print('PASS NaN в профиле отклоняется')
check('NaN в данных считается пропуском',
      metric_signal(float('nan'), 'dz_days') == 'na')
check('структурная закрытость определяется без текста brief',
      _dataset_closure_status({'closing_label': ['закрыт', 'закрыт']}) is True and
      _dataset_closure_status({'closing_label': ['закрыт', 'НЕ ЗАКРЫТ']}) is False)

# Индивидуальный KPI использует тот же каталог и возвращает raw values.
dataset = {
    'months': ['2026-01'], 'pl': {'rev': [1000], 'nds': [0], 'cost': [700],
                                  'adm': [50]},
    'dz_tot': {'sk': 100}, 'kz_tot': {'sk': 80},
    'cf': {'b51_end': 50, 'b50_end': 0},
    'credits': {'sk': 0, 'sn': 0}, 'e91': [], 'comm44': [20],
    'zp': {'v': [100]}, 'nal': [('69', '', '', '', 30)],
}
kpis = extract_kpis(dataset)
check('индивидуальный KPI публикует серверные значения',
      kpis['metrics']['dz_days'] == 3)
check('индивидуальный и общий сигнал согласованы',
      kpis['signals']['dz_days'] ==
      score_company(kpis['metrics'], profile)['contributions'][0]['signal'])

with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    other = User(username='rating-other', password_hash='unused', role='user')
    no_access = User(username='rating-none', password_hash='unused', role='user')
    db.session.add_all([other, no_access])
    db.session.flush()
    db.session.add(UserModuleAccess(user_id=other.id, module_key='analytics'))
    c_closed = Company(name='Закрытый период', created_by=admin.id)
    c_open = Company(name='Незакрытый период', created_by=admin.id)
    c_legacy = Company(name='Старые снимки', created_by=admin.id)
    c_archived = Company(name='Архивное предприятие', created_by=admin.id,
                         status='archived')
    db.session.add_all([c_closed, c_open, c_legacy, c_archived])
    db.session.flush()
    open_values = dict(values)
    open_values['dz_days'] = 999
    older_values = dict(values)
    older_values['dz_days'] = 120
    now = datetime.now(timezone.utc)
    db.session.add_all([
        Upload(company_id=c_closed.id, uploaded_by=admin.id,
               period_label='январь 2026', closure_status=True,
               uploaded_at=now,
               metrics_json=json.dumps(values)),
        Upload(company_id=c_closed.id, uploaded_by=admin.id,
               period_label='декабрь 2025', closure_status=True,
               uploaded_at=now - timedelta(days=31),
               metrics_json=json.dumps(older_values)),
        Upload(company_id=c_closed.id, uploaded_by=admin.id,
               period_label='февраль 2026', closure_status=False,
               uploaded_at=now + timedelta(days=31),
               metrics_json=json.dumps(open_values)),
        Upload(company_id=c_open.id, uploaded_by=admin.id,
               period_label='февраль 2026', closure_status=False,
               uploaded_at=now,
               metrics_json=json.dumps(values)),
        Upload(company_id=c_legacy.id, uploaded_by=admin.id,
               period_label='январь 2026', closure_status=True,
               uploaded_at=now, metrics_json='{}'),
        Upload(company_id=c_legacy.id, uploaded_by=admin.id,
               period_label='декабрь 2025', closure_status=True,
               uploaded_at=now - timedelta(days=31), metrics_json='{}'),
        Upload(company_id=c_archived.id, uploaded_by=admin.id,
               period_label='январь 2026', closure_status=True,
               uploaded_at=now, metrics_json=json.dumps(values)),
    ])
    db.session.commit()
    admin_id, other_id, no_access_id = admin.id, other.id, no_access.id
    closed_id, open_id, legacy_id = c_closed.id, c_open.id, c_legacy.id
    archived_id = c_archived.id
    for company_id in (closed_id, open_id):
        out = os.path.join(OUTPUT, str(company_id))
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, 'kpi_cache.json'), 'w', encoding='utf-8') as f:
            json.dump({'metrics': values}, f)
    legacy_out = os.path.join(OUTPUT, str(legacy_id))
    os.makedirs(legacy_out, exist_ok=True)
    with open(os.path.join(legacy_out, 'kpi_cache.json'), 'w', encoding='utf-8') as f:
        json.dump({'metrics': values}, f)

client = app.test_client()
login_as(client, no_access_id)
check('API рейтинга закрыт без права на аналитику',
      client.get('/rating-api').status_code == 403)
login_as(client, admin_id)
r = client.get('/home?legacy=1')
check('экран общего рейтинга встроен в главную аналитику',
      r.status_code == 200 and 'Общий рейтинг' in r.get_data(as_text=True))
check('домашняя страница не кэширует старый JSON-клиент',
      'no-store' in r.headers.get('Cache-Control', ''))
check('CSRF-токен рейтинга доступен вне зарезервированного /api',
      client.get('/rating-api/csrf').status_code == 200 and
      client.get('/rating-api/csrf').get_json().get('csrf_token'))
r = client.get('/rating-api')
check('авторизованный API рейтинга доступен', r.status_code == 200)
by_id = {row['company_id']: row for row in r.get_json()['results']}
check('архивное предприятие исключено из рейтинга', archived_id not in by_id)
check('последний доступный период участвует в базовом рейтинге',
      by_id[closed_id]['ranked'] and by_id[open_id]['ranked'])
dz = next(x for x in by_id[closed_id]['contributions'] if x['key'] == 'dz_days')
check('базовый рейтинг использует свежие KPI незакрытого периода',
      dz['value'] == open_values['dz_days'])

latest_closed = deepcopy(DEFAULT_PROFILE)
latest_closed['name'] = 'Последний закрытый'
latest_closed['period'] = 'latest_closed'
r = client.post('/rating-api/profiles', json=latest_closed)
latest_closed_id = r.get_json()['profile']['id']
r = client.get(f'/rating-api?profile_id={latest_closed_id}')
closed_by_id = {row['company_id']: row for row in r.get_json()['results']}
closed_dz = next(x for x in closed_by_id[closed_id]['contributions']
                 if x['key'] == 'dz_days')
check('режим закрытого периода не использует последующий незакрытый снимок',
      closed_dz['value'] == values['dz_days'])
check('предприятие без закрытого периода не ранжируется в закрытом режиме',
      not closed_by_id[open_id]['ranked']
      and closed_by_id[open_id]['freshness'] is None)

all_closed = deepcopy(DEFAULT_PROFILE)
all_closed['name'] = 'Все закрытые'
all_closed['period'] = 'all_closed'
r = client.post('/rating-api/profiles', json=all_closed)
all_closed_id = r.get_json()['profile']['id']
r = client.get(f'/rating-api?profile_id={all_closed_id}')
all_row = next(x for x in r.get_json()['results'] if x['company_id'] == closed_id)
all_dz = next(x for x in all_row['contributions'] if x['key'] == 'dz_days')
check('режим всех закрытых периодов агрегирует только их снимки',
      all_dz['value'] == (values['dz_days'] + 120) / 2)
legacy_row = next(x for x in r.get_json()['results'] if x['company_id'] == legacy_id)
check('общий период не подменяется старым текущим кэшем',
      not legacy_row['ranked'] and legacy_row['coverage'] == 0)

custom = deepcopy(DEFAULT_PROFILE)
custom['name'] = 'Проверочный'
r = client.post('/rating-api/profiles', json=custom)
check('валидный профиль сохраняется', r.status_code == 201)
profile_id = r.get_json()['profile']['id']
login_as(client, other_id)
check('обычный пользователь не видит незакреплённые предприятия в рейтинге',
      client.get('/rating-api').get_json()['results'] == [])
check('чужой профиль недоступен',
      client.get(f'/rating-api?profile_id={profile_id}').status_code == 404)
check('чужой профиль нельзя изменить',
      client.put(f'/rating-api/profiles/{profile_id}',
                 json={'config': custom}).status_code == 404)

print('\nВСЕ ТЕСТЫ РЕЙТИНГА ПРОЙДЕНЫ')