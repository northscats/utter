# -*- coding: utf-8 -*-
"""ИИ-проверка документов по правилам каталога.

ИБ-требование: документы отправляются ТОЛЬКО локальным провайдерам
(AiProvider.is_local=True). Внешние провайдеры блокируются явной ошибкой.

Использует исключительно существующий call_ai_provider() из ai_client.py —
никаких новых интеграций.
"""
import json
import logging
import re
import threading
import time

from ai_client import call_ai_provider
from database import (AiProvider, AnalysisTemplate, DocCheckResult,
                      DocCheckRule, DocFile, DocFilePage, DocFinding,
                      DocPackage, db)

logger = logging.getLogger(__name__)

PAGES_PER_CHUNK = 3          # страниц за один вызов ИИ
MAX_CHARS_PER_PAGE = 6000    # обрезка текста страницы (контроль токенов)
RETRY_ATTEMPTS = 3
RETRY_STATUSES = (429, 500, 502, 503)

# Один анализ одновременно (LLM + БД, тот же принцип, что в doc_pipeline)
_ANALYZE_SEMAPHORE = threading.Semaphore(1)


class ProviderNotLocalError(Exception):
    """Провайдер не является локальным — отправка документов запрещена."""


def get_local_provider() -> AiProvider:
    """Возвращает активный ЛОКАЛЬНЫЙ провайдер или бросает ProviderNotLocalError.

    Порядок выбора: default+local → любой active+local.
    Если настроен только внешний провайдер — явная ошибка (ИБ-требование:
    документы не покидают периметр).
    """
    provider = (AiProvider.query.filter_by(is_default=True, is_active=True,
                                           is_local=True).first()
                or AiProvider.query.filter_by(is_active=True, is_local=True).first())
    if provider is None:
        any_active = AiProvider.query.filter_by(is_active=True).first()
        if any_active is not None:
            raise ProviderNotLocalError(
                f'Провайдер «{any_active.name}» не помечен как локальный. '
                'Проверка документов разрешена только через локальный (внутренний) '
                'ИИ-провайдер — отметьте его флагом «Локальный» в настройках.')
        raise ProviderNotLocalError('Локальный ИИ-провайдер не настроен.')
    if not provider.base_url or not provider.model:
        raise ProviderNotLocalError(
            f'У провайдера «{provider.name}» не заполнены адрес или модель.')
    return provider


# Типы находок и их русские названия (для промпта)
FINDING_TYPES = ('date', 'signature', 'seal')
_FINDING_RU = {'date': 'даты подписания/составления документа',
               'signature': 'подписи (упоминания «подпись», ФИО с должностью у подписи, м.п./роспись)',
               'seal': 'печати и штампы (упоминания «м.п.», «печать», оттиски)'}


def build_prompt(rules: list, pages: list, find_types=(), system_extra='') -> list:
    """Собирает messages для ИИ: правила каталога + поиск находок.

    Промпт формируется динамически — правила задаёт администратор,
    типы находок (даты/подписи/печати) выбираются при запуске анализа.
    """
    rules_lines = []
    for i, rule in enumerate(rules, 1):
        rules_lines.append(f'{i}. [{rule.id}] {rule.name}: {rule.prompt_text}')

    pages_lines = []
    for p in pages:
        text = (p.text or '')[:MAX_CHARS_PER_PAGE]
        pages_lines.append(f'--- Страница {p.page_num} ---\n{text or "(текст не извлечён)"}')

    parts = ['Ты эксперт по проверке документов. Тебе дан текст страниц документа.']
    if rules:
        parts.append(
            'Для КАЖДОГО правила проверки определи статус:\n'
            '  "ok" — требование выполнено (найдено подтверждение);\n'
            '  "violation" — требование нарушено (найдено противоречие);\n'
            '  "not_found" — на этих страницах нет информации для вывода.')
    if find_types:
        ft_lines = '\n'.join(f'  "{t}" — {_FINDING_RU[t]}'
                             for t in find_types if t in _FINDING_RU)
        parts.append('Также найди на страницах реквизиты следующих типов:\n' + ft_lines +
                     '\nДля каждой находки укажи тип, номер страницы, короткую цитату '
                     'из текста (quote) и краткое пояснение (description).')
    parts.append(
        'Ответь СТРОГО валидным JSON-объектом без пояснений, формат:\n'
        '{"rules": [{"rule_id": <число>, "status": "ok|violation|not_found", '
        '"details": "<краткое обоснование на русском>"}], '
        '"findings": [{"type": "date|signature|seal", "page": <число>, '
        '"quote": "<цитата>", "description": "<пояснение>"}]}')
    if system_extra:
        parts.append('Дополнительные указания администратора:\n' + system_extra.strip())
    system_msg = '\n'.join(parts)

    user_parts = []
    if rules:
        user_parts.append('ПРАВИЛА ПРОВЕРКИ:\n' + '\n'.join(rules_lines))
    user_parts.append('ТЕКСТ ДОКУМЕНТА:\n' + '\n\n'.join(pages_lines))
    return [{'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': '\n\n'.join(user_parts)}]


def _parse_ai_json(content: str) -> dict:
    """Лениво извлекает JSON-объект (или массив правил) из ответа ИИ.

    Возвращает dict {"rules": [...], "findings": [...]}."""
    m = re.search(r'\{[\s\S]*\}', content)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return {'rules': data.get('rules') if isinstance(data.get('rules'), list) else [],
                        'findings': data.get('findings') if isinstance(data.get('findings'), list) else []}
        except json.JSONDecodeError:
            pass
    # Обратная совместимость: некоторые модели вернут просто массив правил
    m = re.search(r'\[[\s\S]*\]', content)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return {'rules': data, 'findings': []}
        except json.JSONDecodeError:
            pass
    return {'rules': [], 'findings': []}


def _call_with_retry(provider, api_key, messages) -> dict:
    """call_ai_provider с retry (3 попытки, экспоненциальный backoff)."""
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return call_ai_provider(
                base_url=provider.base_url,
                api_key=api_key,
                model=provider.model,
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
                enable_thinking=False,
                timeout=120,
            )
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, 'status', None)
            if status in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1:
                time.sleep(2 ** attempt)   # 1с, 2с
                continue
            raise
    raise last_exc  # pragma: no cover


# Приоритет статусов при агрегации chunk-результатов
_STATUS_RANK = {'violation': 3, 'ok': 2, 'not_found': 1}


def analyze_file(df: DocFile, provider, api_key, find_types=(),
                 system_extra='') -> None:
    """Проверяет один файл: правила каталога + поиск находок.

    Результаты (по одному DocCheckResult на правило) и находки перезаписываются.
    """
    rules = [r for r in (df.catalog.check_rules if df.catalog else [])
             if r.is_active]
    pages = (DocFilePage.query.filter_by(file_id=df.id)
             .order_by(DocFilePage.page_num).all())
    if not pages or (not rules and not find_types):
        df.analysis_status = 'done'
        db.session.commit()
        return

    # Перезапуск проверки: старые результаты и находки файла удаляем
    DocCheckResult.query.filter_by(file_id=df.id).delete()
    DocFinding.query.filter_by(file_id=df.id).delete()

    # Агрегация: violation > ok > not_found по всем chunk'ам
    agg: dict = {r.id: {'status': 'not_found', 'details': []} for r in rules}
    max_page = pages[-1].page_num

    for i in range(0, len(pages), PAGES_PER_CHUNK):
        chunk = pages[i:i + PAGES_PER_CHUNK]
        messages = build_prompt(rules, chunk, find_types, system_extra)
        result = _call_with_retry(provider, api_key, messages)
        parsed = _parse_ai_json(result['content'])
        for item in parsed['rules']:
            if not isinstance(item, dict):
                continue
            rid = item.get('rule_id')
            status = item.get('status')
            if rid not in agg or status not in _STATUS_RANK:
                continue
            cur = agg[rid]
            if _STATUS_RANK[status] > _STATUS_RANK[cur['status']]:
                cur['status'] = status
            det = (item.get('details') or '').strip()
            if det and status != 'not_found':
                pages_lbl = f'стр. {chunk[0].page_num}–{chunk[-1].page_num}' \
                    if len(chunk) > 1 else f'стр. {chunk[0].page_num}'
                cur['details'].append(f'[{pages_lbl}] {det}')
        chunk_pages = {p.page_num for p in chunk}
        for item in parsed['findings']:
            if not isinstance(item, dict):
                continue
            ftype = item.get('type')
            if ftype not in find_types:
                continue
            try:
                page = int(item.get('page') or chunk[0].page_num)
            except (TypeError, ValueError):
                page = chunk[0].page_num
            if page not in chunk_pages:
                page = min(max(1, page), max_page)
            db.session.add(DocFinding(
                file_id=df.id, page_num=page, ftype=ftype,
                snippet=str(item.get('quote') or '')[:1000],
                description=str(item.get('description') or '')[:1000]))

    for rule in rules:
        a = agg[rule.id]
        db.session.add(DocCheckResult(
            file_id=df.id,
            check_rule_id=rule.id,
            status=a['status'],
            details=json.dumps({'notes': a['details']}, ensure_ascii=False),
        ))
    df.analysis_status = 'done'
    db.session.commit()


DEFAULT_OPTIONS = dict(find_dates=True, find_signatures=True, find_seals=True,
                       skip_analyzed=False, template_id=None, file_ids=None)


def analyze_package(app, package_id: int, options: dict | None = None) -> None:
    """Фоновая проверка файлов пакета (все или подмножество). Статус — в БД."""
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    with _ANALYZE_SEMAPHORE, app.app_context():
        pkg = db.session.get(DocPackage, package_id)
        if pkg is None:
            return
        try:
            provider = get_local_provider()
            from app import decrypt_value
            api_key = decrypt_value(provider.api_key) if provider.api_key else ''

            # Шаблон анализа: системный промпт + типы находок
            system_extra = ''
            find_types = tuple(t for t, k in
                               (('date', 'find_dates'), ('signature', 'find_signatures'),
                                ('seal', 'find_seals')) if opts.get(k))
            if opts.get('template_id'):
                tpl = db.session.get(AnalysisTemplate, opts['template_id'])
                if tpl is not None:
                    system_extra = tpl.system_prompt or ''
                    find_types = tuple(t for t, on in
                                       (('date', tpl.find_dates),
                                        ('signature', tpl.find_signatures),
                                        ('seal', tpl.find_seals)) if on)

            files = [f for f in pkg.files if f.status == 'ready']
            if opts.get('file_ids'):
                wanted = set(opts['file_ids'])
                files = [f for f in files if f.id in wanted]
            if opts.get('skip_analyzed'):
                files = [f for f in files if f.analysis_status != 'done']

            for f in files:
                f.analysis_status = 'processing'
            pkg.analysis_total = len(files)
            pkg.analysis_done = 0
            db.session.commit()

            for df in files:
                try:
                    analyze_file(df, provider, api_key, find_types, system_extra)
                except Exception as exc:
                    logger.exception('analysis failed for file %s', df.id)
                    db.session.rollback()
                    # Ошибка одного файла не валит весь пакет
                    DocCheckResult.query.filter_by(file_id=df.id).delete()
                    db.session.add(DocCheckResult(
                        file_id=df.id, check_rule_id=None, status='error',
                        details=json.dumps({'error': str(exc)[:500]},
                                           ensure_ascii=False)))
                    df2 = db.session.get(DocFile, df.id)
                    if df2 is not None:
                        df2.analysis_status = 'error'
                finally:
                    pkg.analysis_done += 1
                    db.session.commit()

            pkg.analysis_status = 'done'
            pkg.analysis_error = ''
            db.session.commit()
        except ProviderNotLocalError as pe:
            db.session.rollback()
            pkg = db.session.get(DocPackage, package_id)
            pkg.analysis_status = 'error'
            pkg.analysis_error = str(pe)
            db.session.commit()
        except Exception as exc:
            logger.exception('package analysis %s failed', package_id)
            db.session.rollback()
            pkg = db.session.get(DocPackage, package_id)
            pkg.analysis_status = 'error'
            pkg.analysis_error = str(exc)[:1000]
            db.session.commit()
