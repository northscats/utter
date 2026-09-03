# -*- coding: utf-8 -*-
"""Единые серверные формулы и правила рейтинга предприятий."""
from copy import deepcopy
import math


METRICS = {
    'dz_days': {'name': 'Оборачиваемость ДЗ', 'unit': 'дн.', 'direction': 'lower',
                'green': 60, 'yellow': 90,
                'reasons': ('ДЗ оборачивается в нормативный срок',
                            'Оборачиваемость ДЗ требует внимания',
                            'Деньги надолго отвлечены в дебиторскую задолженность')},
    'kz_dz': {'name': 'КЗ / ДЗ', 'unit': '×', 'direction': 'lower',
              'green': 1.0, 'yellow': 1.5,
              'reasons': ('КЗ не превышает ДЗ', 'КЗ умеренно превышает ДЗ',
                          'Высокая зависимость от кредиторов')},
    'liquidity': {'name': 'Ликвидность ДС / КЗ', 'unit': '', 'direction': 'higher',
                  'green': .5, 'yellow': .2,
                  'reasons': ('Денежная подушка достаточна',
                              'Ликвидность требует контроля',
                              'Денежных средств критически мало')},
    'debt_ebitda': {'name': 'Долг / EBITDA', 'unit': '×', 'direction': 'lower',
                    'green': 2.5, 'yellow': 4,
                    'reasons': ('Долговая нагрузка комфортна',
                                'Долговая нагрузка выше нормы',
                                'Долговая нагрузка критична')},
    'rate': {'name': 'Ставка по кредитам', 'unit': '%', 'direction': 'lower',
             'green': 18, 'yellow': 22,
             'reasons': ('Стоимость долга в норме',
                         'Стоит рассмотреть рефинансирование',
                         'Стоимость долга критически высока')},
    'tax_burden': {'name': 'Нагрузка на ФОТ', 'unit': '%', 'direction': 'lower',
                   'green': 32, 'yellow': 38,
                   'reasons': ('Нагрузка на ФОТ в норме',
                               'Нагрузка на ФОТ выше нормы',
                               'Нагрузка на ФОТ критически высока')},
    'cost_pct': {'name': 'Себестоимость / выручка', 'unit': '%', 'direction': 'lower',
                 'green': 80, 'yellow': 95,
                 'reasons': ('Себестоимость в норме', 'Себестоимость высока',
                             'Себестоимость критически высока')},
    'comm_pct': {'name': 'Коммерческие расходы', 'unit': '%', 'direction': 'lower',
                 'green': 3, 'yellow': 5,
                 'reasons': ('Коммерческие расходы в норме',
                             'Коммерческие расходы выше нормы',
                             'Коммерческие расходы критически высоки')},
    'adm_pct': {'name': 'Управленческие расходы', 'unit': '%', 'direction': 'lower',
                'green': 8, 'yellow': 12,
                'reasons': ('Управленческие расходы в норме',
                            'Управленческие расходы выше нормы',
                            'Управленческие расходы критически высоки')},
    'nvv_pct': {'name': 'Выполнение НВВ', 'unit': '%', 'direction': 'higher',
                'green': 95, 'yellow': 85, 'available': False,
                'unavailable_reason': 'Нет подтверждённого планового значения НВВ'},
}

DEFAULT_PROFILE = {
    'name': 'Базовый профиль',
    'period': 'latest_closed',
    'missing_policy': 'renormalize',
    'min_coverage': 70,
    'metrics': {
        key: {'enabled': key != 'nvv_pct', 'weight': 100 / 9,
              'green': meta['green'], 'yellow': meta['yellow']}
        for key, meta in METRICS.items()
    },
}


class ProfileValidationError(ValueError):
    pass


def metric_signal(value, metric, green=None, yellow=None):
    meta = METRICS[metric]
    if value is None or meta.get('available', True) is False:
        return 'na'
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 'na'
    if not math.isfinite(value):
        return 'na'
    green = meta['green'] if green is None else float(green)
    yellow = meta['yellow'] if yellow is None else float(yellow)
    if meta['direction'] == 'lower':
        return 'green' if value <= green else 'yellow' if value <= yellow else 'red'
    return 'green' if value >= green else 'yellow' if value >= yellow else 'red'


def validate_profile(payload):
    src = payload or {}
    profile = deepcopy(DEFAULT_PROFILE)
    name = str(src.get('name', profile['name'])).strip()
    if not 1 <= len(name) <= 120:
        raise ProfileValidationError('Название должно содержать от 1 до 120 символов')
    profile['name'] = name
    if src.get('period', profile['period']) not in ('latest_closed', 'all_closed'):
        raise ProfileValidationError('Неизвестное правило периода')
    profile['period'] = src.get('period', profile['period'])
    if src.get('missing_policy', profile['missing_policy']) != 'renormalize':
        raise ProfileValidationError('Поддерживается только исключение пропусков с нормализацией весов')
    coverage = float(src.get('min_coverage', profile['min_coverage']))
    if not math.isfinite(coverage):
        raise ProfileValidationError('Минимальное покрытие должно быть конечным числом')
    if coverage < 0 or coverage > 100:
        raise ProfileValidationError('Минимальное покрытие должно быть от 0 до 100')
    profile['min_coverage'] = coverage
    incoming = src.get('metrics', {})
    if not isinstance(incoming, dict):
        raise ProfileValidationError('Настройки метрик должны быть объектом')
    enabled_weight = 0
    for key, meta in METRICS.items():
        cfg = incoming.get(key, {})
        if not isinstance(cfg, dict):
            raise ProfileValidationError(f'Некорректная настройка метрики {key}')
        enabled = bool(cfg.get('enabled', profile['metrics'][key]['enabled']))
        if meta.get('available', True) is False and enabled:
            raise ProfileValidationError(f'Метрика «{meta["name"]}» пока недоступна')
        weight = float(cfg.get('weight', profile['metrics'][key]['weight']))
        green = float(cfg.get('green', meta['green']))
        yellow = float(cfg.get('yellow', meta['yellow']))
        if not all(math.isfinite(v) for v in (weight, green, yellow)):
            raise ProfileValidationError('Вес и пороги должны быть конечными числами')
        if weight < 0 or weight > 100:
            raise ProfileValidationError('Вес каждой метрики должен быть от 0 до 100')
        if (meta['direction'] == 'lower' and green > yellow) or (
                meta['direction'] == 'higher' and green < yellow):
            raise ProfileValidationError(f'Пороги метрики «{meta["name"]}» заданы в неверном порядке')
        profile['metrics'][key] = dict(enabled=enabled, weight=weight,
                                       green=green, yellow=yellow)
        if enabled:
            enabled_weight += weight
    if not profile['metrics'] or enabled_weight <= 0:
        raise ProfileValidationError('Нужно включить хотя бы одну метрику с положительным весом')
    if abs(enabled_weight - 100) > .05:
        raise ProfileValidationError('Сумма весов включённых метрик должна равняться 100%')
    return profile


def score_company(values, profile, missing_reasons=None):
    """Оценивает готовые серверные KPI; пропуски не превращаются в нули."""
    missing_reasons = missing_reasons or {}
    enabled = [(k, c) for k, c in profile['metrics'].items() if c['enabled']]
    total_weight = sum(c['weight'] for _, c in enabled)
    available_weight = 0
    contributions = []
    severity = {'red': 3, 'yellow': 2, 'green': 1, 'na': 0}
    overall_signal = 'na'
    for key, cfg in enabled:
        value = values.get(key)
        signal = metric_signal(value, key, cfg['green'], cfg['yellow'])
        points = {'green': 100, 'yellow': 50, 'red': 0, 'na': None}[signal]
        if points is not None:
            available_weight += cfg['weight']
        meta = METRICS[key]
        if signal == 'na':
            reason = (meta.get('unavailable_reason') or missing_reasons.get(key)
                      or 'Нет данных для расчёта')
            contribution = None
        else:
            reason = meta['reasons'][{'green': 0, 'yellow': 1, 'red': 2}[signal]]
            contribution = cfg['weight'] * points / 100
            if severity[signal] > severity[overall_signal]:
                overall_signal = signal
        contributions.append(dict(key=key, name=meta['name'], unit=meta['unit'],
                                  value=value, signal=signal, reason=reason,
                                  weight=cfg['weight'], points=points,
                                  contribution=contribution))
    coverage = round(available_weight / total_weight * 100, 1) if total_weight else 0
    ranked = coverage >= profile['min_coverage'] and available_weight > 0
    raw_points = sum(x['contribution'] or 0 for x in contributions)
    score = round(raw_points / available_weight * 100, 1) if ranked else None
    return dict(score=score, coverage=coverage, ranked=ranked,
                signal=overall_signal,
                exclusion_reason=None if ranked else
                f'Покрытие {coverage:g}% ниже требуемых {profile["min_coverage"]:g}%',
                contributions=contributions)


def rank_companies(rows):
    """Competition ranking: равные баллы имеют одно место; порядок стабилен по id."""
    ranked = sorted((r for r in rows if r['ranked']),
                    key=lambda r: (-r['score'], r['company_name'].casefold(), r['company_id']))
    last_score = None
    last_place = 0
    for index, row in enumerate(ranked, 1):
        if row['score'] != last_score:
            last_place = index
            last_score = row['score']
        row['place'] = last_place
    for row in rows:
        if not row['ranked']:
            row['place'] = None
    return ranked + sorted((r for r in rows if not r['ranked']),
                           key=lambda r: (r['company_name'].casefold(), r['company_id']))