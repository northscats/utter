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
import base64
import io
import math
import os
import unicodedata
import urllib.parse
from difflib import SequenceMatcher
from types import SimpleNamespace

from PIL import Image

from ai_client import call_ai_provider
from database import (AiProvider, AnalysisTemplate, DocAnalysisRun,
                      DocCatalogAnalysisRun, DocCheckResult, DocCheckRule, DocFile,
                      DocFilePage, DocFinding, DocFreeAnalysisResult,
                      DocPackage, db, utcnow)

logger = logging.getLogger(__name__)

PAGES_PER_CHUNK = 3          # страниц за один вызов ИИ
MAX_CHARS_PER_PAGE = 6000    # обрезка текста страницы (контроль токенов)
RETRY_ATTEMPTS = 3
RETRY_STATUSES = (429, 500, 502, 503)
TEXT_MATCH_MIN_CONFIDENCE = 0.85
VISION_BOX_MIN_CONFIDENCE = 0.75
FINDING_ALGORITHM_VERSION = 'hybrid_bbox_v1'

# Один анализ одновременно (LLM + БД, тот же принцип, что в doc_pipeline)
_ANALYZE_SEMAPHORE = threading.Semaphore(1)


class ProviderNotLocalError(Exception):
    """Провайдер не является локальным — отправка документов запрещена."""


class AnalysisCancelled(Exception):
    """Кооперативная остановка на безопасной границе выполнения."""


def _raise_if_cancelled(cancel_check=None):
    if cancel_check is not None and cancel_check():
        raise AnalysisCancelled('Анализ остановлен пользователем')


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


def _call_with_retry(provider, api_key, messages, cancel_check=None,
                     response_format=None) -> dict:
    """call_ai_provider с retry (3 попытки, экспоненциальный backoff)."""
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        _raise_if_cancelled(cancel_check)
        try:
            result = call_ai_provider(
                base_url=provider.base_url,
                api_key=api_key,
                model=provider.model,
                messages=messages,
                temperature=0.1,
                max_tokens=4000,
                enable_thinking=False,
                timeout=60,
                response_format=response_format,
            )
            _raise_if_cancelled(cancel_check)
            if result.get('finish_reason') == 'length':
                raise ValueError(
                    'ИИ не завершил JSON-ответ: достигнут лимит генерации')
            if not (result.get('content') or '').strip():
                raise ValueError('ИИ вернул пустой ответ вместо JSON')
            return result
        except Exception as exc:
            if isinstance(exc, AnalysisCancelled):
                raise
            last_exc = exc
            status = getattr(exc, 'status', None)
            if status in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1:
                deadline = time.monotonic() + (2 ** attempt)
                while time.monotonic() < deadline:
                    _raise_if_cancelled(cancel_check)
                    time.sleep(min(0.1, deadline - time.monotonic()))
                continue
            raise
    raise last_exc  # pragma: no cover


def _provider_json_response_format(provider):
    """JSON mode только для подтверждённо совместимого официального DeepSeek."""
    hostname = (urllib.parse.urlparse(provider.base_url).hostname or '').lower()
    return {'type': 'json_object'} if hostname == 'api.deepseek.com' else None


# Приоритет статусов при агрегации chunk-результатов
_STATUS_RANK = {'violation': 3, 'ok': 2, 'not_found': 1}


def _normalized_word(value) -> str:
    value = unicodedata.normalize('NFKC', str(value or '')).casefold().replace('ё', 'е')
    return ''.join(ch for ch in value if ch.isalnum())


def _numeric_ocr_key(value: str) -> str:
    """Разрешает только узкую O/0-подмену внутри числового токена."""
    normalized = _normalized_word(value)
    if any(ch.isdigit() for ch in normalized):
        return normalized.replace('o', '0').replace('о', '0')
    return normalized


def _token_match(expected: str, actual: str, allow_fuzzy=False):
    expected_key, actual_key = _numeric_ocr_key(expected), _numeric_ocr_key(actual)
    numeric = any(ch.isdigit() for ch in expected_key + actual_key)
    if expected_key == actual_key:
        return 1.0
    if numeric or not allow_fuzzy or min(len(expected_key), len(actual_key)) < 6:
        return None
    score = SequenceMatcher(None, expected_key, actual_key).ratio()
    return score if score >= 0.90 else None


def _page_words(page) -> list:
    try:
        words = json.loads(page.words_json or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    return words if isinstance(words, list) else []


def _backfill_page_coordinates(df: DocFile, pages: list, cancel_check=None):
    """Лениво добавляет координаты старым страницам при повторном анализе."""
    missing = [page for page in pages if not _page_words(page)]
    if not missing:
        return
    from doc_parser import (IMAGE_EXTS, extract_pdf_pages_data, ocr_image_data,
                            prepare_ocr_page)
    pdf_pages = extract_pdf_pages_data(df.pdf_path) if df.pdf_path else []
    for page in missing:
        _raise_if_cancelled(cancel_check)
        source = pdf_pages[page.page_num - 1] if page.page_num <= len(pdf_pages) else {}
        words = source.get('words') or []
        if words:
            page.words_json = json.dumps(words, ensure_ascii=False)
            page.coordinate_version = 'pdf_text_v1'
        elif page.image_path and os.path.isfile(page.image_path):
            is_photo = os.path.splitext(df.orig_name)[1].lower() in IMAGE_EXTS
            page.coordinate_version = prepare_ocr_page(
                page.image_path, is_photo=is_photo)
            ocr = ocr_image_data(page.image_path)
            page.words_json = json.dumps(ocr['words'], ensure_ascii=False)
            page.image_width = ocr['width']
            page.image_height = ocr['height']
            if not page.text and ocr['text']:
                page.text = ocr['text']
            page.ocr_used = bool(ocr['words'])


def _valid_normalized_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, width, height = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, width, height)):
        return None
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > 1.0001 or y + height > 1.0001):
        return None
    return [round(max(0.0, min(1.0, x)), 6),
            round(max(0.0, min(1.0, y)), 6),
            round(max(0.0, min(1.0 - x, width)), 6),
            round(max(0.0, min(1.0 - y, height)), 6)]


def _merge_word_boxes(words: list) -> list:
    """Объединяет соседние слова строки, сохраняя переносы как несколько bbox."""
    boxes = []
    for word in words:
        box = _valid_normalized_box(word.get('bbox') if isinstance(word, dict) else None)
        if box is None:
            continue
        x, y, width, height = box
        if boxes:
            px, py, pw, ph = boxes[-1]
            same_line = abs((py + ph / 2) - (y + height / 2)) <= max(ph, height) * 0.65
            close = x <= px + pw + 0.035
            if same_line and close:
                right, bottom = max(px + pw, x + width), max(py + ph, y + height)
                boxes[-1] = [px, min(py, y), right - px, bottom - min(py, y)]
                continue
        boxes.append(box)
    return [[round(v, 6) for v in box] for box in boxes]


def match_quote_boxes(page, quote: str) -> tuple[list, float | None, str]:
    """Сопоставляет цитату с последовательностью PDF/OCR токенов."""
    raw_words = _page_words(page)
    words = [(word, _normalized_word(word.get('text')))
             for word in raw_words if isinstance(word, dict)]
    words = [(word, norm) for word, norm in words if norm]
    quote_words = [_normalized_word(x) for x in re.findall(r'\S+', quote or '', re.UNICODE)]
    quote_words = [x for x in quote_words if x]
    if not words or not quote_words:
        return [], None, ''
    q_len = len(quote_words)
    if q_len > len(words):
        return [], None, ''

    def candidates(allow_fuzzy):
        found = []
        for start in range(0, len(words) - q_len + 1):
            scores = [
                _token_match(expected, actual, allow_fuzzy=allow_fuzzy)
                for expected, (_, actual) in zip(quote_words, words[start:start + q_len])
            ]
            if all(score is not None for score in scores):
                found.append((sum(scores) / len(scores), start, start + q_len))
        return found

    matches = candidates(False) or candidates(True)
    # Повторяющаяся одинаковая цитата неоднозначна: безопаснее оставить точку.
    if len(matches) != 1:
        best_score = max((m[0] for m in matches), default=0.0)
        return [], round(best_score, 3) if matches else None, ''
    score, start, end = matches[0]
    if score < 0.94:
        return [], round(score, 3), ''
    selected = [word for word, _ in words[start:end]]
    token_conf = []
    for word in selected:
        try:
            value = float(word.get('confidence', 1.0))
        except (TypeError, ValueError):
            return [], None, ''
        if not math.isfinite(value) or value < 0 or value > 1:
            return [], None, ''
        token_conf.append(value)
    confidence = score * (sum(token_conf) / len(token_conf) if token_conf else 1.0)
    if confidence < TEXT_MATCH_MIN_CONFIDENCE:
        return [], round(confidence, 3), ''
    source = 'ocr_text' if page.ocr_used else 'pdf_text'
    return _merge_word_boxes(selected), round(min(1.0, confidence), 3), source


def _vision_image_data_uri(image_path: str) -> str:
    with Image.open(image_path) as image:
        image = image.convert('RGB')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def _parse_vision_findings(content: str, page_num: int, find_types: tuple) -> list:
    parsed = _parse_ai_json(content)
    results = []
    for item in parsed['findings']:
        if not isinstance(item, dict) or item.get('type') not in find_types:
            continue
        try:
            confidence = float(item.get('confidence'))
        except (TypeError, ValueError):
            confidence = 0.0
        if not math.isfinite(confidence):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        raw_boxes = item.get('boxes')
        if not isinstance(raw_boxes, list):
            raw_boxes = [item.get('bbox')] if item.get('bbox') is not None else []
        boxes = [box for box in (_valid_normalized_box(v) for v in raw_boxes) if box]
        # Не рисуем правдоподобную, но сомнительную рамку.
        if confidence < VISION_BOX_MIN_CONFIDENCE:
            boxes = []
        results.append({
            'page_num': page_num,
            'ftype': item['type'],
            'snippet': str(item.get('quote') or '')[:1000],
            'description': str(item.get('description') or '')[:1000],
            'source': 'vision',
            'boxes_json': json.dumps(boxes),
            'confidence': round(confidence, 3),
            'algorithm_version': FINDING_ALGORITHM_VERSION,
        })
    return results


def _analyze_page_vision(page, provider, api_key, find_types,
                         cancel_check=None) -> list:
    if not provider.is_local:
        raise ProviderNotLocalError('Изображения документов разрешены только локальному провайдеру.')
    if not provider.supports_vision or not page.image_path:
        return []
    _raise_if_cancelled(cancel_check)
    requested = ', '.join(find_types)
    messages = [
        {'role': 'system', 'content':
            'Ты локальный детектор реквизитов документов. Найди только реально '
            'видимые графические объекты указанных типов. Координаты bbox нормализованы '
            'относительно изображения: [x,y,width,height], все значения 0..1. '
            'Не создавай рамку по одному лишь слову «подпись» или «печать». Ответь '
            'строго JSON: {\"rules\":[],\"findings\":[{\"type\":\"signature|seal\",'
            '\"confidence\":0.0,\"bbox\":[0,0,0,0],\"quote\":\"\",'
            '\"description\":\"\"}]}.'},
        {'role': 'user', 'content': [
            {'type': 'text', 'text':
                f'Страница {page.page_num}. Искомые типы: {requested}. '
                'Верни только обнаруженные объекты.'},
            {'type': 'image_url', 'image_url': {
                'url': _vision_image_data_uri(page.image_path)}},
        ]},
    ]
    result = _call_with_retry(
        provider, api_key, messages, cancel_check=cancel_check,
        response_format={'type': 'json_object'})
    _raise_if_cancelled(cancel_check)
    return _parse_vision_findings(result['content'], page.page_num, find_types)


def analyze_file(df: DocFile, provider, api_key, find_types=(),
                 system_extra='', user_extra='', rules_override=None,
                 cancel_check=None, commit=True) -> dict:
    """Проверяет один файл: правила каталога + поиск находок.

    Результаты (по одному DocCheckResult на правило) и находки перезаписываются.
    """
    _raise_if_cancelled(cancel_check)
    if rules_override is None:
        rules = [r for r in (df.catalog.check_rules if df.catalog else [])
                 if r.is_active]
    else:
        rules = [
            SimpleNamespace(
                id=int(r['id']), name=str(r.get('name') or '—'),
                prompt_text=str(r.get('prompt_text') or ''))
            for r in rules_override if isinstance(r, dict) and r.get('id')
        ]
    pages = (DocFilePage.query.filter_by(file_id=df.id)
             .order_by(DocFilePage.page_num).all())
    if not pages or (not rules and not find_types):
        _raise_if_cancelled(cancel_check)
        df.analysis_status = 'done'
        if commit:
            db.session.commit()
        return {'rules': [], 'findings': []}
    if find_types:
        _backfill_page_coordinates(df, pages, cancel_check=cancel_check)

    # Агрегация: violation > ok > not_found по всем chunk'ам
    agg: dict = {r.id: {'status': 'not_found', 'details': []} for r in rules}
    max_page = pages[-1].page_num
    staged_findings = []

    visual_types = tuple(t for t in find_types if t in ('signature', 'seal'))
    text_find_types = tuple(find_types)

    for i in range(0, len(pages), PAGES_PER_CHUNK):
        _raise_if_cancelled(cancel_check)
        chunk = pages[i:i + PAGES_PER_CHUNK]
        messages = build_prompt(rules, chunk, text_find_types, system_extra,
                                user_extra)
        result = _call_with_retry(
            provider, api_key, messages, cancel_check=cancel_check,
            response_format=_provider_json_response_format(provider))
        _raise_if_cancelled(cancel_check)
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
            if ftype not in text_find_types:
                continue
            try:
                page = int(item.get('page') or chunk[0].page_num)
            except (TypeError, ValueError):
                page = chunk[0].page_num
            if page not in chunk_pages:
                page = min(max(1, page), max_page)
            page_row = next((p for p in chunk if p.page_num == page), None)
            boxes, confidence, source = (match_quote_boxes(page_row, item.get('quote') or '')
                                         if page_row and ftype == 'date'
                                         else ([], None, ''))
            staged_findings.append(dict(
                page_num=page, ftype=ftype,
                snippet=str(item.get('quote') or '')[:1000],
                description=str(item.get('description') or '')[:1000],
                source=source,
                boxes_json=json.dumps(boxes),
                confidence=confidence,
                algorithm_version=FINDING_ALGORITHM_VERSION if source else ''))

    if provider.supports_vision and visual_types:
        for page in pages:
            _raise_if_cancelled(cancel_check)
            try:
                vision_findings = _analyze_page_vision(
                    page, provider, api_key, visual_types,
                    cancel_check=cancel_check)
                if vision_findings:
                    detected_types = {item['ftype'] for item in vision_findings}
                    # Vision заменяет только page-level fallback того же типа.
                    # Если vision ничего не нашёл/сломался, текстовая карточка
                    # сохраняется без ложной координатной рамки.
                    staged_findings = [
                        item for item in staged_findings
                        if not (item['page_num'] == page.page_num
                                and item['ftype'] in detected_types
                                and not item.get('source'))
                    ]
                    staged_findings.extend(vision_findings)
            except AnalysisCancelled:
                raise
            except Exception as exc:
                # Vision экспериментален: сбой координат не отменяет текстовую
                # проверку и не создаёт ложных рамок.
                logger.warning('vision analysis failed for file %s page %s: %s',
                               df.id, page.page_num, exc)

    snapshot_rules = []
    for rule in rules:
        a = agg[rule.id]
        snapshot_rules.append(dict(
            rule_id=rule.id,
            rule_name=rule.name,
            status=a['status'],
            details={'notes': list(a['details'])},
        ))
    snapshot_findings = [
        dict(page=item['page_num'], type=item['ftype'],
             quote=item['snippet'], description=item['description'],
             source=item.get('source') or '',
             boxes=json.loads(item.get('boxes_json') or '[]'),
             confidence=item.get('confidence'),
             algorithm_version=item.get('algorithm_version') or '')
        for item in staged_findings
    ]

    # Старый успешный результат заменяется только после полного получения нового.
    # Исторический snapshot не зависит от живой строки правила. В общую таблицу
    # пишем только правила, которые всё ещё существуют (FK PostgreSQL).
    _raise_if_cancelled(cancel_check)
    DocCheckResult.query.filter_by(file_id=df.id).delete()
    DocFinding.query.filter_by(file_id=df.id).delete()
    live_rule_ids = {
        row[0] for row in db.session.query(DocCheckRule.id).filter(
            DocCheckRule.id.in_([r.id for r in rules])).all()
    } if rules else set()
    for rule in rules:
        if rule.id not in live_rule_ids:
            continue
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
    _raise_if_cancelled(cancel_check)
    if commit:
        db.session.commit()
    return {'rules': snapshot_rules, 'findings': snapshot_findings}


def analyze_file_free(df: DocFile, provider, api_key, user_prompt: str,
                      system_extra: str = '', cancel_check=None) -> str:
    """Свободный анализ файла; возвращает ответ, не меняя проверки правил."""
    _raise_if_cancelled(cancel_check)
    pages = (DocFilePage.query.filter_by(file_id=df.id)
             .order_by(DocFilePage.page_num).all())
    if not pages:
        raise ValueError('В документе нет страниц для анализа')

    answers = []
    for i in range(0, len(pages), PAGES_PER_CHUNK):
        _raise_if_cancelled(cancel_check)
        chunk = pages[i:i + PAGES_PER_CHUNK]
        result = _call_with_retry(
            provider, api_key,
            build_free_prompt(chunk, user_prompt, system_extra),
            cancel_check=cancel_check,
            response_format=_provider_json_response_format(provider))
        _raise_if_cancelled(cancel_check)
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
        build_free_synthesis_prompt(user_prompt, answers),
        cancel_check=cancel_check,
        response_format=_provider_json_response_format(provider))
    _raise_if_cancelled(cancel_check)
    return _parse_free_ai_json(result['content'])[:30000]


def build_catalog_summary_prompt(user_prompt: str, documents: list,
                                 errors: list) -> list:
    """Строит сводку только из структурированных результатов документов."""
    source_text = json.dumps(
        {'documents': documents, 'analysis_errors': errors},
        ensure_ascii=False)
    return [
        {'role': 'system', 'content':
            'Ты формируешь сводное заключение по каталогу документов. Используй '
            'ТОЛЬКО переданные результаты анализов, не делай выводов по файлам с '
            'ошибками и не придумывай источники. Ответь СТРОГО JSON без Markdown: '
            '{"summary":"<общий вывод>",'
            '"common_issues":["<общие нарушения>"],'
            '"discrepancies":["<расхождения между документами>"],'
            '"missing_information":["<документы без нужных сведений>"]}'},
        {'role': 'user', 'content':
            'ИСХОДНЫЙ ПРОМПТ ПОЛЬЗОВАТЕЛЯ:\n' + user_prompt.strip() +
            '\n\nСТРУКТУРИРОВАННЫЕ РЕЗУЛЬТАТЫ:\n' + source_text},
    ]


def _parse_catalog_summary(content: str) -> dict:
    m = re.search(r'\{[\s\S]*\}', content or '')
    if not m:
        raise ValueError('ИИ вернул сводку не в формате JSON')
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError('ИИ вернул повреждённый JSON сводки') from exc
    if not isinstance(data, dict) or not isinstance(data.get('summary'), str):
        raise ValueError('ИИ вернул сводку без общего вывода')
    result = {'summary': data['summary'].strip()[:6000]}
    for key in ('common_issues', 'discrepancies', 'missing_information'):
        values = data.get(key)
        result[key] = ([str(v)[:500] for v in values[:20]]
                       if isinstance(values, list) else [])
    return result


def _compact_summary_document(document: dict, max_chars=12000) -> dict:
    """Оставляет валидное представление каждого источника в заданном бюджете."""
    base = {
        'file_id': document.get('file_id'),
        'name': str(document.get('name') or '—')[:500],
        'run_id': document.get('run_id'),
    }
    if document.get('source_file_ids'):
        base['represented_source_ids'] = document.get('source_file_ids')
    result = document.get('result') if isinstance(document.get('result'), dict) else {}
    if isinstance(result.get('answer'), str):
        base['result'] = {'answer': result['answer'][:max_chars - 1000]}
        return base
    if isinstance(result.get('summary'), str):
        base['result'] = {
            'summary': result['summary'][:6000],
            'common_issues': [
                str(v)[:500] for v in (result.get('common_issues') or [])[:20]],
            'discrepancies': [
                str(v)[:500] for v in (result.get('discrepancies') or [])[:20]],
            'missing_information': [
                str(v)[:500]
                for v in (result.get('missing_information') or [])[:20]],
        }
        return base

    compact_rules = []
    omitted_details = 0
    omitted_rules = 0
    source_rules = result.get('rules') or []
    for rule_index, rule in enumerate(source_rules):
        if not isinstance(rule, dict):
            continue
        item = {
            'rule_id': rule.get('rule_id'),
            'rule_name': str(rule.get('rule_name') or '—')[:300],
            'status': rule.get('status'),
        }
        details = rule.get('details')
        notes = details.get('notes') if isinstance(details, dict) else None
        if isinstance(notes, list) and notes:
            item['details'] = '; '.join(str(n) for n in notes)[:500]
        candidate = dict(base, result={
            'rules': compact_rules + [item],
            'finding_counts': {},
        })
        if len(json.dumps(candidate, ensure_ascii=False)) > max_chars - 1500:
            omitted_details += 1
            item.pop('details', None)
            candidate = dict(base, result={
                'rules': compact_rules + [item],
                'finding_counts': {},
            })
            if len(json.dumps(candidate, ensure_ascii=False)) > max_chars - 1000:
                omitted_rules = len(source_rules) - rule_index
                break
        compact_rules.append(item)

    finding_counts = {}
    finding_examples = []
    for finding in result.get('findings') or []:
        if not isinstance(finding, dict):
            continue
        ftype = str(finding.get('type') or 'other')
        finding_counts[ftype] = finding_counts.get(ftype, 0) + 1
        example = {
            'page': finding.get('page'),
            'type': ftype,
            'quote': str(finding.get('quote') or '')[:250],
            'description': str(finding.get('description') or '')[:250],
        }
        candidate = dict(base, result={
            'rules': compact_rules,
            'finding_counts': finding_counts,
            'finding_examples': finding_examples + [example],
        })
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_chars:
            finding_examples.append(example)

    base['result'] = {
        'rules': compact_rules,
        'finding_counts': finding_counts,
        'finding_examples': finding_examples,
    }
    if omitted_details:
        base['result']['details_compacted'] = omitted_details
    if omitted_rules:
        base['result']['rules_omitted_after_budget'] = omitted_rules
    return base


def _chunk_summary_documents(documents: list, max_chars=45000) -> list:
    chunks, current, current_size = [], [], 0
    for document in documents:
        size = len(json.dumps(document, ensure_ascii=False))
        if current and current_size + size > max_chars:
            chunks.append(current)
            current, current_size = [], 0
        current.append(document)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def _synthesize_catalog(provider, api_key, user_prompt: str,
                        documents: list, errors: list,
                        cancel_check=None) -> dict:
    """Иерархически сводит все источники без обрезки или ложных ссылок."""
    _raise_if_cancelled(cancel_check)
    compact = [_compact_summary_document(d) for d in documents]
    source_map = {
        d['file_id']: dict(file_id=d['file_id'], name=d['name'],
                           run_id=d.get('run_id'))
        for d in compact
    }
    chunks = _chunk_summary_documents(compact)
    if len(chunks) == 1:
        response = _call_with_retry(
            provider, api_key,
            build_catalog_summary_prompt(user_prompt, chunks[0], errors),
            cancel_check=cancel_check,
            response_format=_provider_json_response_format(provider))
        summary = _parse_catalog_summary(response['content'])
    else:
        level = []
        for index, chunk in enumerate(chunks, 1):
            response = _call_with_retry(
                provider, api_key,
                build_catalog_summary_prompt(user_prompt, chunk, []),
                cancel_check=cancel_check,
                response_format=_provider_json_response_format(provider))
            partial = _parse_catalog_summary(response['content'])
            level.append({
                'file_id': f'group-{index}',
                'name': f'Промежуточная сводка {index}',
                'result': partial,
                'source_file_ids': [d['file_id'] for d in chunk],
            })
        while len(level) > 1:
            next_level = []
            for index, chunk in enumerate(
                    _chunk_summary_documents(
                        [_compact_summary_document(d) for d in level]), 1):
                response = _call_with_retry(
                    provider, api_key,
                    build_catalog_summary_prompt(user_prompt, chunk, []),
                    cancel_check=cancel_check,
                    response_format=_provider_json_response_format(provider))
                partial = _parse_catalog_summary(response['content'])
                next_level.append({
                    'file_id': f'reduced-{len(next_level)}-{index}',
                    'name': f'Объединённая сводка {index}',
                    'result': partial,
                })
            level = next_level
        if errors:
            response = _call_with_retry(
                provider, api_key,
                build_catalog_summary_prompt(user_prompt, level, errors),
                cancel_check=cancel_check,
                response_format=_provider_json_response_format(provider))
            summary = _parse_catalog_summary(response['content'])
        else:
            summary = level[0]['result']
    _raise_if_cancelled(cancel_check)
    summary['sources'] = list(source_map.values())
    summary['errors'] = errors
    return summary


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
                snapshot = analyze_file(
                    df, provider, api_key, find_types,
                    run.template_prompt or '', run.user_prompt or '',
                    rules_override=opts.get('rules'))
                run.result_json = json.dumps(snapshot, ensure_ascii=False)

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


def _catalog_cancel_requested(catalog_run_id: int) -> bool:
    status = (db.session.query(DocCatalogAnalysisRun.status)
              .filter_by(id=catalog_run_id).scalar())
    return status in ('cancel_requested', 'cancelled')


def _lock_running_catalog_run(catalog_run_id: int) -> DocCatalogAnalysisRun:
    """Сериализует cancel и terminal-write по строке родительского запуска."""
    batch = (DocCatalogAnalysisRun.query.filter_by(id=catalog_run_id)
             .with_for_update().populate_existing().first())
    if batch is None or batch.status != 'running':
        raise AnalysisCancelled('Анализ остановлен пользователем')
    return batch


def _start_catalog_run(catalog_run_id: int) -> DocCatalogAnalysisRun:
    """Атомарно переводит queued→running либо замечает раннюю отмену."""
    batch = (DocCatalogAnalysisRun.query.filter_by(id=catalog_run_id)
             .with_for_update().populate_existing().first())
    if batch is None or batch.status not in ('queued', 'running'):
        raise AnalysisCancelled('Анализ остановлен пользователем')
    batch.status = 'running'
    return batch


def _restore_file_after_cancel(df):
    if df is None or df.analysis_status != 'processing':
        return
    has_previous = (
        DocCheckResult.query.filter_by(file_id=df.id).first()
        or DocFreeAnalysisResult.query.filter_by(file_id=df.id).first())
    df.analysis_status = 'done' if has_previous else 'none'


def _finalize_catalog_cancel(catalog_run_id: int) -> None:
    """Завершает кооперативную отмену и сохраняет готовые дочерние результаты."""
    db.session.rollback()
    batch = db.session.get(DocCatalogAnalysisRun, catalog_run_id)
    if batch is None:
        return
    now = utcnow()
    cancelled = 0
    for child in batch.file_runs:
        if child.status in ('queued', 'running', 'cancel_requested'):
            _restore_file_after_cancel(child.file)
            child.status = 'cancelled'
            child.error_text = 'Анализ отменён пользователем'
            child.completed_at = now
            cancelled += 1
    processed = sum(
        1 for child in batch.file_runs if child.status in ('done', 'error'))
    batch.files_done = processed
    batch.files_cancelled = cancelled
    batch.status = 'cancelled'
    batch.error_text = ''
    batch.summary_json = '{}'
    batch.cancelled_at = now
    batch.completed_at = now
    db.session.commit()


def _analyze_catalog_locked(app, catalog_run_id: int) -> None:
    """Анализирует зафиксированный список файлов и затем строит одну сводку."""
    with app.app_context():
        batch = db.session.get(DocCatalogAnalysisRun, catalog_run_id)
        if batch is None:
            return
        pkg = db.session.get(DocPackage, batch.package_id)
        cancel_check = lambda: _catalog_cancel_requested(catalog_run_id)
        try:
            _raise_if_cancelled(cancel_check)
            provider = get_local_provider()
            from app import decrypt_value
            api_key = decrypt_value(provider.api_key) if provider.api_key else ''
            opts = json.loads(batch.options_json or '{}')
            file_ids = [int(x) for x in json.loads(batch.file_ids_json or '[]')]
            find_types = tuple(t for t, k in
                               (('date', 'find_dates'),
                                ('signature', 'find_signatures'),
                                ('seal', 'find_seals')) if opts.get(k, True))

            batch = _start_catalog_run(catalog_run_id)
            batch.started_at = utcnow()
            db.session.commit()
            documents = []
            try:
                errors = json.loads(batch.skipped_json or '[]')
            except (TypeError, ValueError):
                errors = []

            for file_id in file_ids:
                _raise_if_cancelled(cancel_check)
                child = (DocAnalysisRun.query.filter_by(
                    catalog_run_id=batch.id, file_id=file_id).first())
                df = db.session.get(DocFile, file_id)
                if child is None:
                    errors.append(dict(file_id=file_id, name=f'Файл #{file_id}',
                                       error='Запись анализа файла не найдена'))
                    batch = _lock_running_catalog_run(catalog_run_id)
                    batch.files_done += 1
                    pkg.analysis_done = batch.files_done
                    db.session.commit()
                    continue
                try:
                    if df is None or df.package_id != batch.package_id:
                        raise ValueError('Документ больше не существует в пакете')
                    batch = _lock_running_catalog_run(catalog_run_id)
                    child = (DocAnalysisRun.query.filter_by(
                        catalog_run_id=batch.id, file_id=file_id)
                        .with_for_update().populate_existing().first())
                    if child is None:
                        raise ValueError('Запись анализа файла не найдена')
                    child.status = 'running'
                    child.started_at = utcnow()
                    df.analysis_status = 'processing'
                    db.session.commit()

                    if batch.mode == 'free':
                        content = analyze_file_free(
                            df, provider, api_key, batch.user_prompt,
                            batch.template_prompt or '',
                            cancel_check=cancel_check)
                        db.session.add(DocFreeAnalysisResult(
                            run_id=child.id, file_id=df.id, content=content))
                        snapshot = {'answer': content}
                        child.result_json = json.dumps(snapshot,
                                                       ensure_ascii=False)
                        df.analysis_status = (
                            'done' if DocCheckResult.query.filter_by(
                                file_id=df.id).first() else 'none')
                    else:
                        snapshot = analyze_file(
                            df, provider, api_key, find_types,
                            batch.template_prompt or '', batch.user_prompt,
                            rules_override=opts.get('rules'),
                            cancel_check=cancel_check, commit=False)
                        batch = _lock_running_catalog_run(catalog_run_id)
                        child = (DocAnalysisRun.query.filter_by(
                            catalog_run_id=batch.id, file_id=file_id)
                            .with_for_update().populate_existing().first())
                        child.result_json = json.dumps(snapshot,
                                                       ensure_ascii=False)
                    if batch.mode == 'free':
                        batch = _lock_running_catalog_run(catalog_run_id)
                        child = (DocAnalysisRun.query.filter_by(
                            catalog_run_id=batch.id, file_id=file_id)
                            .with_for_update().populate_existing().first())
                    child.status = 'done'
                    child.error_text = ''
                    child.completed_at = utcnow()
                    documents.append(dict(
                        file_id=df.id, name=df.orig_name, run_id=child.id,
                        result=snapshot))
                    db.session.commit()
                except AnalysisCancelled:
                    db.session.rollback()
                    raise
                except Exception as file_exc:
                    logger.exception('catalog run %s file %s failed',
                                     catalog_run_id, file_id)
                    db.session.rollback()
                    batch = db.session.get(DocCatalogAnalysisRun,
                                           catalog_run_id)
                    batch = _lock_running_catalog_run(catalog_run_id)
                    pkg = db.session.get(DocPackage, batch.package_id)
                    child = (DocAnalysisRun.query.filter_by(
                        catalog_run_id=batch.id, file_id=file_id).first())
                    df = db.session.get(DocFile, file_id)
                    if child:
                        child.status = 'error'
                        child.error_text = str(file_exc)[:1000]
                        child.completed_at = utcnow()
                    if df:
                        df.analysis_status = (
                            'done' if (DocCheckResult.query.filter_by(
                                file_id=df.id).first()
                                or DocFreeAnalysisResult.query.filter_by(
                                    file_id=df.id).first()) else 'error')
                    errors.append(dict(
                        file_id=file_id,
                        name=(df.orig_name if df else f'Файл #{file_id}'),
                        error=str(file_exc)[:1000]))
                batch = _lock_running_catalog_run(catalog_run_id)
                batch.files_done += 1
                pkg.analysis_done = batch.files_done
                batch.errors_json = json.dumps(errors, ensure_ascii=False)
                db.session.commit()

            _raise_if_cancelled(cancel_check)
            if documents:
                summary = _synthesize_catalog(
                    provider, api_key, batch.user_prompt, documents, errors,
                    cancel_check=cancel_check)
            else:
                summary = {
                    'summary': 'Ни один документ не был успешно проанализирован.',
                    'common_issues': [],
                    'discrepancies': [],
                    'missing_information': [],
                }
                summary['sources'] = []
                summary['errors'] = errors
            _raise_if_cancelled(cancel_check)
            batch = _lock_running_catalog_run(catalog_run_id)
            pkg = db.session.get(DocPackage, batch.package_id)
            batch.summary_json = json.dumps(summary, ensure_ascii=False)
            batch.errors_json = json.dumps(errors, ensure_ascii=False)
            batch.status = 'done'
            batch.error_text = (
                f'Не удалось проанализировать файлов: {len(errors)}'
                if errors else '')
            batch.completed_at = utcnow()
            db.session.commit()
        except AnalysisCancelled:
            _finalize_catalog_cancel(catalog_run_id)
        except Exception as exc:
            logger.exception('catalog analysis run %s failed', catalog_run_id)
            db.session.rollback()
            batch = db.session.get(DocCatalogAnalysisRun, catalog_run_id)
            pkg = db.session.get(DocPackage, batch.package_id) if batch else None
            if batch:
                batch.status = 'error'
                batch.error_text = str(exc)[:1000]
                batch.completed_at = utcnow()
            db.session.commit()


def _release_catalog_package_slot(app, catalog_run_id: int) -> None:
    """Разрешает новый запуск только после фактического выхода worker из слота."""
    with app.app_context():
        batch = db.session.get(DocCatalogAnalysisRun, catalog_run_id)
        if batch is None or batch.package is None:
            return
        pkg = batch.package
        if batch.status == 'error':
            pkg.analysis_status = 'error'
            pkg.analysis_error = batch.error_text or 'Ошибка анализа каталога'
        elif batch.status in ('done', 'cancelled'):
            pkg.analysis_status = 'done'
            pkg.analysis_error = batch.error_text or ''
        else:
            return
        pkg.analysis_done = batch.files_done
        db.session.commit()


def analyze_catalog(app, catalog_run_id: int) -> None:
    _ANALYZE_SEMAPHORE.acquire()
    try:
        _analyze_catalog_locked(app, catalog_run_id)
    finally:
        _ANALYZE_SEMAPHORE.release()
    _release_catalog_package_slot(app, catalog_run_id)


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
