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
from database import (AiProvider, DocCheckResult, DocCheckRule, DocFile,
                      DocFilePage, DocPackage, db)

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


def build_prompt(rules: list, pages: list) -> list:
    """Собирает messages для ИИ из правил каталога и текста страниц.

    Промпт формируется динамически — правила задаёт администратор.
    """
    rules_lines = []
    for i, rule in enumerate(rules, 1):
        rules_lines.append(f'{i}. [{rule.id}] {rule.name}: {rule.prompt_text}')

    pages_lines = []
    for p in pages:
        text = (p.text or '')[:MAX_CHARS_PER_PAGE]
        pages_lines.append(f'--- Страница {p.page_num} ---\n{text or "(текст не извлечён)"}')

    system_msg = (
        'Ты эксперт по проверке документов. Тебе даны правила проверки и текст '
        'страниц документа. Для КАЖДОГО правила определи статус:\n'
        '  "ok" — требование выполнено (найдено подтверждение);\n'
        '  "violation" — требование нарушено (найдено противоречие);\n'
        '  "not_found" — на этих страницах нет информации для вывода.\n'
        'Ответь СТРОГО валидным JSON-массивом без пояснений, формат:\n'
        '[{"rule_id": <число>, "status": "ok|violation|not_found", '
        '"details": "<краткое обоснование на русском>"}]'
    )
    user_msg = ('ПРАВИЛА ПРОВЕРКИ:\n' + '\n'.join(rules_lines)
                + '\n\nТЕКСТ ДОКУМЕНТА:\n' + '\n\n'.join(pages_lines))
    return [{'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg}]


def _parse_ai_json(content: str) -> list:
    """Лениво извлекает JSON-массив из ответа ИИ."""
    m = re.search(r'\[[\s\S]*\]', content)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


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


def analyze_file(df: DocFile, provider, api_key) -> None:
    """Проверяет один файл по активным правилам его каталога.

    Результаты (по одному DocCheckResult на правило) перезаписываются.
    """
    rules = [r for r in (df.catalog.check_rules if df.catalog else [])
             if r.is_active]
    if not rules:
        return

    pages = (DocFilePage.query.filter_by(file_id=df.id)
             .order_by(DocFilePage.page_num).all())
    if not pages:
        return

    # Перезапуск проверки: старые результаты файла удаляем
    DocCheckResult.query.filter_by(file_id=df.id).delete()

    # Агрегация: violation > ok > not_found по всем chunk'ам
    agg: dict = {r.id: {'status': 'not_found', 'details': []} for r in rules}

    for i in range(0, len(pages), PAGES_PER_CHUNK):
        chunk = pages[i:i + PAGES_PER_CHUNK]
        messages = build_prompt(rules, chunk)
        result = _call_with_retry(provider, api_key, messages)
        for item in _parse_ai_json(result['content']):
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

    for rule in rules:
        a = agg[rule.id]
        db.session.add(DocCheckResult(
            file_id=df.id,
            check_rule_id=rule.id,
            status=a['status'],
            details=json.dumps({'notes': a['details']}, ensure_ascii=False),
        ))
    db.session.commit()


def analyze_package(app, package_id: int) -> None:
    """Фоновая проверка всех готовых файлов пакета. Статус — в БД."""
    with _ANALYZE_SEMAPHORE, app.app_context():
        pkg = db.session.get(DocPackage, package_id)
        if pkg is None:
            return
        try:
            provider = get_local_provider()
            from app import decrypt_value
            api_key = decrypt_value(provider.api_key) if provider.api_key else ''

            files = [f for f in pkg.files if f.status == 'ready']
            pkg.analysis_total = len(files)
            pkg.analysis_done = 0
            db.session.commit()

            for df in files:
                try:
                    analyze_file(df, provider, api_key)
                except Exception as exc:
                    logger.exception('analysis failed for file %s', df.id)
                    db.session.rollback()
                    # Ошибка одного файла не валит весь пакет
                    DocCheckResult.query.filter_by(file_id=df.id).delete()
                    db.session.add(DocCheckResult(
                        file_id=df.id, check_rule_id=None, status='error',
                        details=json.dumps({'error': str(exc)[:500]},
                                           ensure_ascii=False)))
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
