# -*- coding: utf-8 -*-
"""Единый расширяемый реестр пользовательских модулей."""

MODULES = {
    'analytics': {
        'key': 'analytics',
        'name': 'Аналитика ФХД РСО',
        'description': 'Предприятия, оборотно-сальдовые ведомости, КПЭ и финансовые дашборды.',
        'icon': 'chart',
        'endpoint': 'home',
        'exact_paths': ('/home',),
        'path_prefixes': ('/company/', '/dashboard/', '/ai/', '/rating-api'),
    },
    'documents': {
        'key': 'documents',
        'name': 'Проверка документов',
        'description': 'Загрузка, распределение, просмотр и согласование пакетов документов.',
        'icon': 'documents',
        'endpoint': 'docs.docs_ui',
        'exact_paths': ('/docs',),
        'path_prefixes': ('/docs/',),
    },
    'directories': {
        'key': 'directories',
        'name': 'Справочники',
        'description': 'Единый каталог предприятий, реквизиты, контакты и назначения.',
        'icon': 'directory',
        'endpoint': 'directory_companies',
        'exact_paths': ('/directories/companies',),
        'path_prefixes': ('/directories/', '/api/directories/', '/api/companies/'),
    },
}


def registered_modules():
    return list(MODULES.values())


def valid_module_keys(values):
    """Возвращает уникальные зарегистрированные ключи, сохраняя порядок реестра."""
    selected = set(values or ())
    return [key for key in MODULES if key in selected]


def module_key_for_path(path):
    """Определяет модуль только по декларативным областям из реестра."""
    for key, module in MODULES.items():
        if path in module.get('exact_paths', ()):
            return key
        if path.startswith(tuple(module.get('path_prefixes', ()))):
            return key
    return None
