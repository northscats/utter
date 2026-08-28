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
from database import (AiProvider, AnalysisTemplate, DocAnalysisRun,
                      DocCheckResult, DocFile, DocFilePage, DocFinding,
                      DocFreeAnalysisResult, DocPackage, db, utcnow)

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


def build_prompt(rules: list, pages: list, find_types=(), system_extra='',
                 user_extra='') -> list:
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
    if user_extra:
        parts.append(
            'Дополнительная инструкция пользователя:\n' + user_extra.strip() +
            '\nЭта инструкция уточняет предмет проверки, но не отменяет правила выше '
            'и не изменяет обязательный JSON-формат ответа.')
    system_msg = '\n'.join(parts)

    user_parts = []
    if rules:
        user_parts.append('ПРАВИЛА ПРОВЕРКИ:\n' + '\n'.join(rules_lines))
    user_parts.append('ТЕКСТ ДОКУМЕНТА:\n' + '\n\n'.join(pages_lines))
    return [{'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': '\n\n'.join(user_parts)}]


def build_free_prompt(pages: list, user_prompt: str,
                      system_extra: str = '') -> list:
    """Строгий контракт свободного вопроса к страницам документа."""
    page_parts = []
    for p in pages:
        text = (p.text or '')[:MAX_CHARS_PER_PAGE]
        page_parts.append(
            f'--- Страница {p.page_num} ---\n{text or "(текст не извлечён)"}')
    system = [
        'Ты эксперт по анализу документов. Выполни задачу пользователя только '
        'по содержимому переданных страниц. Не придумывай отсутствующие факты.',
        'Ответь СТРОГО валидным JSON-объектом без Markdown и внешних пояснений: '
        '{"answer": "<содержательный ответ на русском>"}',
    ]
    if system_extra:
        system.append('Указания администратора:\n' + system_extra.strip())
    return [
        {'role': 'system', 'content': '\n'.join(system)},
        {'role': 'user', 'content':
            'ЗАДАЧА ПОЛЬЗОВАТЕЛЯ:\n' + user_prompt.strip() +
            '\n\nТЕКСТ ДОКУМЕНТА:\n' + '\n\n'.join(page_parts)},
    ]


def build_free_synthesis_prompt(user_prompt: str, partial_answers: list) -> list:
    """Сводит ответы по блокам страниц в один ответ по всему документу."""
    excerpts = '\n\n'.join(partial_answers)[:30000]
    return [
        {'role': 'system', 'content':
            'Сформируй единый итоговый ответ по документу из результатов анализа '
            'его частей. Не добавляй факты, которых нет в результатах. Ответь '
            'СТРОГО валидным JSON без Markdown: {"answer": "<ответ на русском>"}'},
        {'role': 'user', 'content':
            'ИСХОДНАЯ ЗАДАЧА:\n' + user_prompt.strip() +
            '\n\nРЕЗУЛЬТАТЫ ПО ЧАСТЯМ ДОКУМЕНТА:\n' + excerpts},
    ]


def _parse_free_ai_json(content: str) -> str:
    """Извлекает обязательное строковое поле answer или отклоняет ответ."""
    m = re.search(r'\{[\s\S]*\}', content or '')
    if not m:
        raise ValueError('ИИ вернул ответ не в формате JSON')
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError('ИИ вернул повреждённый JSON') from exc
    answer = data.get('answer') if isinstance(data, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('ИИ вернул пустой свободный ответ')
    return answer.strip()[:12000]


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
    raise ValueError('ИИ вернул повреждённый или неподдерживаемый JSON')


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
                 system_extra='', user_extra='') -> None:
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

    # Агрегация: violation > ok > not_found по всем chunk'ам
    agg: dict = {r.id: {'status': 'not_found', 'details': []} for r in rules}
    max_page = pages[-1].page_num
    staged_findings = []

    for i in range(0, len(pages), PAGES_PER_CHUNK):
        chunk = pages[i:i + PAGES_PER_CHUNK]
        messages = build_prompt(rules, chunk, find_types, system_extra,
                                user_extra)
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
            staged_findings.append(dict(
                page_num=page, ftype=ftype,
                snippet=str(item.get('quote') or '')[:1000],
                description=str(item.get('description') or '')[:1000]))

    # Старый успешный результат заменяется только после полного получения нового.
    DocCheckResult.query.filter_by(file_id=df.id).delete()
    DocFinding.query.filter_by(file_id=df.id).delete()
    for rule in rules:
        a = agg[rule.id]
        db.session.add(DocCheckResult(
            file_id=df.id,
            check_rule_id=rule.id,
            status=a['status'],
            details=json.dumps({'notes': a['details']}, ensure_ascii=False),
        ))
    for item in staged_findings:
        db.session.add(DocFinding(file_id=df.id, **item))
    df.analysis_status = 'done'
    db.session.commit()


def analyze_file_free(df: DocFile, provider, api_key, user_prompt: str,
                      system_extra: str = '') -> str:
    """Свободный анализ файла; возвращает ответ, не меняя проверки правил."""
    pages = (DocFilePage.query.filter_by(file_id=df.id)
             .order_by(DocFilePage.page_num).all())
    if not pages:
        raise ValueError('В документе нет страниц для анализа')

    answers = []
    for i in range(0, len(pages), PAGES_PER_CHUNK):
        chunk = pages[i:i + PAGES_PER_CHUNK]
        result = _call_with_retry(
            provider, api_key,
            build_free_prompt(chunk, user_prompt, system_extra))
        answer = _parse_free_ai_json(result['content'])
        if len(pages) > PAGES_PER_CHUNK:
            label = (f'Страницы {chunk[0].page_num}–{chunk[-1].page_num}'
                     if len(chunk) > 1 else f'Страница {chunk[0].page_num}')
            answers.append(f'{label}: {answer}')
        else:
            answers.append(answer)
    if len(answers) == 1:
        return answers[0]
    result = _call_with_retry(
        provider, api_key,
        build_free_synthesis_prompt(user_prompt, answers))
    return _parse_free_ai_json(result['content'])[:30000]


def _standard_result_snapshot(file_id: int) -> str:
    """Неизменяемый снимок результатов supplement-запуска."""
    rows = []
    for result in DocCheckResult.query.filter_by(file_id=file_id).all():
        try:
            details = json.loads(result.details or '{}')
        except (TypeError, ValueError):
            details = {'raw': result.details or ''}
        rows.append(dict(
            rule_id=result.check_rule_id,
            rule_name=(result.check_rule.name if result.check_rule else '—'),
            status=result.status,
            details=details,
        ))
    findings = [
        dict(page=f.page_num, type=f.ftype, quote=f.snippet or '',
             description=f.description or '')
        for f in (DocFinding.query.filter_by(file_id=file_id)
                  .order_by(DocFinding.page_num, DocFinding.id).all())
    ]
    return json.dumps({'rules': rows, 'findings': findings}, ensure_ascii=False)


def analyze_current_file(app, run_id: int, options: dict | None = None) -> None:
    """Фоновый запуск одного файла в supplement/free режиме."""
    opts = dict(options or {})
    with _ANALYZE_SEMAPHORE, app.app_context():
        run = db.session.get(DocAnalysisRun, run_id)
        if run is None:
            return
        pkg = db.session.get(DocPackage, run.package_id)
        df = db.session.get(DocFile, run.file_id)
        try:
            if pkg is None or df is None:
                raise ValueError('Документ или пакет больше не существует')
            provider = get_local_provider()
            from app import decrypt_value
            api_key = decrypt_value(provider.api_key) if provider.api_key else ''
            run.status = 'running'
            run.started_at = utcnow()
            df.analysis_status = 'processing'
            db.session.commit()

            if run.mode == 'free':
                content = analyze_file_free(
                    df, provider, api_key, run.user_prompt,
                    run.template_prompt or '')
                db.session.add(DocFreeAnalysisResult(
                    run_id=run.id, file_id=df.id, content=content))
                run.result_json = json.dumps({'answer': content},
                                             ensure_ascii=False)
                # Свободный вопрос не заменяет статус последней проверки правил.
                df.analysis_status = (
                    'done' if DocCheckResult.query.filter_by(file_id=df.id).first()
                    else 'none')
            else:
                find_types = tuple(t for t, k in
                                   (('date', 'find_dates'),
                                    ('signature', 'find_signatures'),
                                    ('seal', 'find_seals')) if opts.get(k, True))
                analyze_file(
                    df, provider, api_key, find_types,
                    run.template_prompt or '', run.user_prompt or '')
                run.result_json = _standard_result_snapshot(df.id)

            run.status = 'done'
            run.error_text = ''
            run.completed_at = utcnow()
            pkg.analysis_status = 'done'
            pkg.analysis_error = ''
            pkg.analysis_done = 1
            db.session.commit()
        except Exception as exc:
            logger.exception('current file analysis run %s failed', run_id)
            db.session.rollback()
            run = db.session.get(DocAnalysisRun, run_id)
            pkg = db.session.get(DocPackage, run.package_id) if run else None
            df = db.session.get(DocFile, run.file_id) if run else None
            if run is not None:
                run.status = 'error'
                run.error_text = str(exc)[:1000]
                run.completed_at = utcnow()
            if df is not None:
                # При ошибке предыдущий успешный результат остаётся доступен.
                df.analysis_status = (
                    'done' if (DocCheckResult.query.filter_by(file_id=df.id).first()
                               or DocFreeAnalysisResult.query.filter_by(
                                   file_id=df.id).first())
                    else 'error')
            if pkg is not None:
                pkg.analysis_status = 'error'
                pkg.analysis_error = str(exc)[:1000]
                pkg.analysis_done = 1
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
                    # Ошибка одного файла не валит весь пакет. Старый успешный
                    # результат сохраняем; error-запись нужна только если его нет.
                    if not DocCheckResult.query.filter_by(file_id=df.id).first():
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
