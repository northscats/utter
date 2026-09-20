# -*- coding: utf-8 -*-
"""Range and immutable-file AI analysis for accepted OSV sources."""
import os
import hashlib
import re
import threading
from datetime import date

from ai_client import call_ai_provider
from database import (AiProvider, Company, OsvFileAiSummary, Upload, db, utcnow)
from osvparser import parse_osv
from source_manifest import sha256_file
from sqlalchemy.exc import IntegrityError


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def accepted_sources(uploads):
    """Flatten only explicitly accepted, versioned manifest records."""
    sources = {}
    for upload in uploads:
        manifest = upload.source_manifest if not isinstance(upload, dict) \
            else upload.get('source_manifest')
        if not isinstance(manifest, list):
            continue
        for item in manifest:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get('source_id') or '')
            digest = str(item.get('sha256') or '').lower()
            relative = str(item.get('relative_path') or '')
            expected_id = hashlib.sha256(
                f'{relative}\0{digest}'.encode('utf-8')).hexdigest()
            if (len(digest) != 64 or not relative
                    or source_id != expected_id):
                continue
            sources[source_id] = {
                'source_id': source_id, 'sha256': digest,
                'relative_path': relative,
                'filename': os.path.basename(relative),
                'account': str(item.get('account') or ''),
                'period': str(item.get('period') or ''),
                'start': _as_date(
                    item.get('report_start') or item.get('observed_start')),
                'end': _as_date(
                    item.get('report_end') or item.get('observed_end')),
            }
    return list(sources.values())


def applied_account_sources(uploads):
    """Return one currently applied source per account in numeric order.

    Uploads are expected newest first. Older immutable versions remain
    available for audit, but should not create duplicate account pills in the
    current AI conclusion.
    """
    by_account = {}
    for source in accepted_sources(uploads):
        account = str(source.get('account') or '').strip()
        if account and account not in by_account:
            by_account[account] = source

    def account_key(item):
        parts = re.findall(r'\d+|[^\d]+', item['account'])
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in parts
        )

    return sorted(by_account.values(), key=account_key)


def source_set_revision(sources):
    """Fingerprint the exact immutable source versions used by an aggregate."""
    payload = '\n'.join(sorted(item['source_id'] for item in sources))
    return hashlib.sha256(payload.encode('ascii')).hexdigest()


def range_evidence(sources, extra_dates=()):
    """Return actual boundaries and explicit uncovered calendar months."""
    bounded = [
        {'start': item['start'], 'end': item['end']}
        for item in sources if item['start'] and item['end']
    ]
    bounded.extend(
        {'start': value, 'end': value}
        for value in (_as_date(item) for item in extra_dates) if value)
    if not bounded:
        return {'start': None, 'end': None, 'covered_months': [],
                'missing_months': [], 'missing_intervals': []}
    start = min(item['start'] for item in bounded)
    end = max(item['end'] for item in bounded)
    covered = set()
    for item in bounded:
        cursor = date(item['start'].year, item['start'].month, 1)
        last = date(item['end'].year, item['end'].month, 1)
        while cursor <= last:
            covered.add(cursor.strftime('%Y-%m'))
            cursor = date(cursor.year + (cursor.month == 12),
                          1 if cursor.month == 12 else cursor.month + 1, 1)
    expected = []
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        expected.append(cursor.strftime('%Y-%m'))
        cursor = date(cursor.year + (cursor.month == 12),
                      1 if cursor.month == 12 else cursor.month + 1, 1)
    intervals = sorted((item['start'], item['end']) for item in bounded)
    merged = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1].fromordinal(
                merged[-1][1].toordinal() + 1):
            merged.append([interval_start, interval_end])
        elif interval_end > merged[-1][1]:
            merged[-1][1] = interval_end
    missing_intervals = []
    for previous, following in zip(merged, merged[1:]):
        gap_start = previous[1].fromordinal(previous[1].toordinal() + 1)
        gap_end = following[0].fromordinal(following[0].toordinal() - 1)
        missing_intervals.append((gap_start, gap_end))
    return {'start': start, 'end': end, 'covered_months': sorted(covered),
            'missing_months': [month for month in expected if month not in covered],
            'missing_intervals': missing_intervals}


def verified_source_facts(company_id, sources, uploads_root):
    """Read each accepted version independently for the aggregate prompt."""
    root = os.path.realpath(os.path.join(uploads_root, str(company_id)))
    lines = []
    unavailable = []
    for source in sources:
        path = os.path.realpath(os.path.join(root, source['relative_path']))
        try:
            if not path.startswith(root + os.sep) or not os.path.isfile(path):
                raise OSError('файл недоступен')
            if sha256_file(path).lower() != source['sha256']:
                raise OSError('версия файла не совпадает')
            parsed = parse_osv(path)
            top = parsed.get('top') or {}
            if top:
                lines.append(
                    f'{source["filename"]} | счёт {source["account"] or "?"} | '
                    f'{source["period"] or "период не указан"} | '
                    f'оборот Дт {_format_amount(top.get("obd"))}, '
                    f'оборот Кт {_format_amount(top.get("obk"))}, '
                    f'конечное сальдо Дт {_format_amount(top.get("skd"))}, '
                    f'Кт {_format_amount(top.get("skk"))} руб.')
            else:
                lines.append(
                    f'{source["filename"]}: итоговые показатели отсутствуют.')
        except Exception as exc:
            unavailable.append(f'{source["filename"]}: {exc}')
    return lines, unavailable


def _provider():
    return (AiProvider.query.filter_by(is_default=True, is_active=True).first()
            or AiProvider.query.filter_by(is_active=True).first())


def _decrypt(value):
    # Local import avoids an app/database import cycle during startup.
    from app import decrypt_value
    return decrypt_value(value) if value else ''


def _format_amount(value):
    return f'{float(value or 0):,.2f}'.replace(',', ' ')


def _has_recommendations(content):
    return '### рекомендации' in str(content or '').casefold()


def _file_prompt(company, source, parsed):
    top = parsed.get('top') or {}
    facts = [
        f'Организация: {company.name}',
        f'Единственный анализируемый файл: {source["filename"]}',
        f'Версия источника SHA-256: {source["sha256"]}',
        f'Счёт: {source["account"] or "не определён"}',
        f'Период: {source["period"] or "не определён"}',
    ]
    if source['start'] and source['end']:
        facts.append(
            f'Фактические границы: {source["start"]:%d.%m.%Y}–'
            f'{source["end"]:%d.%m.%Y}')
    else:
        facts.append('Фактические границы периода не подтверждены.')
    if top:
        for key, label in (
                ('snd', 'начальное сальдо Дт'), ('snk', 'начальное сальдо Кт'),
                ('obd', 'оборот Дт'), ('obk', 'оборот Кт'),
                ('skd', 'конечное сальдо Дт'), ('skk', 'конечное сальдо Кт')):
            facts.append(f'{label}: {_format_amount(top.get(key))} руб.')
    else:
        facts.append('Итоговые показатели в проверенном файле отсутствуют.')
    warnings = list(parsed.get('warnings') or [])
    if warnings:
        facts.append('Предупреждения проверки: ' + '; '.join(warnings[:4]))
    return '\n'.join(facts)


_FILE_SYSTEM = (
    'Ты финансовый аналитик. Дай краткое заключение строго только по одному '
    'указанному файлу ОСВ, на русском языке, не более 900 знаков. Структура: '
    '### Итог, затем ### Риски (1–2 пункта), затем ### Рекомендации '
    '(1–3 конкретных действия, обоснованных только фактами этого файла). '
    'В итоге сравни начальное и конечное '
    'сальдо, укажи абсолютное и процентное изменение, только если исходные числа '
    'позволяют это вычислить, и приведи обороты Дт/Кт. Не добавляй сведения из '
    'других файлов, не придумывай причины и показатели. При недостатке данных '
    'рекомендуй, какие данные нужно загрузить или проверить.'
)


def analyze_file(summary_id, uploads_root):
    """Worker body; record state makes provider failures visible and retryable."""
    row = db.session.get(OsvFileAiSummary, summary_id)
    if not row:
        return
    row.status = 'running'
    row.error_text = ''
    db.session.commit()
    try:
        provider = _provider()
        if not provider or not provider.base_url or not provider.model:
            raise RuntimeError('ИИ-провайдер не настроен')
        path = os.path.realpath(os.path.join(
            uploads_root, str(row.company_id), row.relative_path))
        root = os.path.realpath(os.path.join(uploads_root, str(row.company_id)))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise RuntimeError('Исходный файл недоступен')
        if sha256_file(path).lower() != row.source_sha256.lower():
            raise RuntimeError('Версия исходного файла изменилась')
        parsed = parse_osv(path)
        company = db.session.get(Company, row.company_id)
        source = {
            'filename': row.filename, 'sha256': row.source_sha256,
            'account': row.account, 'period': row.period_label,
            'start': row.report_start, 'end': row.report_end,
        }
        result = call_ai_provider(
            base_url=provider.base_url, api_key=_decrypt(provider.api_key),
            model=provider.model,
            messages=[{'role': 'system', 'content': _FILE_SYSTEM},
                      {'role': 'user', 'content': _file_prompt(
                          company, source, parsed)}],
            temperature=0.2, max_tokens=1000, enable_thinking=False, timeout=90)
        usage = result.get('usage') or {}
        row.content = result.get('content') or ''
        row.provider_name = provider.name
        row.model_name = provider.model
        row.prompt_tokens = usage.get('prompt_tokens', 0)
        row.completion_tokens = usage.get('completion_tokens', 0)
        row.status = 'ready'
        row.generated_at = utcnow()
    except Exception as exc:
        from flask import current_app
        current_app.logger.warning(
            'OSV file AI summary %s failed: %s', summary_id, exc)
        row.status = 'error'
        message = str(exc)
        if message in (
                'ИИ-провайдер не настроен', 'Исходный файл недоступен',
                'Версия исходного файла изменилась'):
            row.error_text = message
        else:
            row.error_text = (
                'ИИ-провайдер временно недоступен. Повторите формирование позже.')
    db.session.commit()


def ensure_file_summaries(company_id, uploads_root, source_ids=None,
                          start_workers=True):
    """Upsert version-bound jobs and start only missing/error requested jobs."""
    uploads = Upload.query.filter_by(company_id=company_id).all()
    sources = accepted_sources(uploads)
    wanted = set(source_ids or ())
    queued = []
    for source in sources:
        if wanted and source['source_id'] not in wanted:
            continue
        row = OsvFileAiSummary.query.filter_by(
            company_id=company_id, source_id=source['source_id']).first()
        if row is None:
            try:
                with db.session.begin_nested():
                    row = OsvFileAiSummary(
                        company_id=company_id, source_id=source['source_id'],
                        source_sha256=source['sha256'],
                        relative_path=source['relative_path'],
                        filename=source['filename'], account=source['account'],
                        period_label=source['period'], report_start=source['start'],
                        report_end=source['end'], status='queued')
                    db.session.add(row)
                    db.session.flush()
                queued.append(row.id)
            except IntegrityError:
                row = OsvFileAiSummary.query.filter_by(
                    company_id=company_id,
                    source_id=source['source_id']).first()
        elif wanted and (
                row.status == 'error'
                or (row.status == 'ready'
                    and not _has_recommendations(row.content))):
            claimed = OsvFileAiSummary.query.filter_by(
                id=row.id, status=row.status).update({
                    'status': 'queued', 'error_text': '', 'queued_at': utcnow(),
                }, synchronize_session=False)
            if claimed:
                queued.append(row.id)
    db.session.commit()
    if start_workers:
        from app import app
        if queued:
            threading.Thread(
                target=_run_batch_with_context,
                args=(app, queued, uploads_root), daemon=True).start()
    return sources


def _run_batch_with_context(flask_app, summary_ids, uploads_root):
    with flask_app.app_context():
        for summary_id in summary_ids:
            analyze_file(summary_id, uploads_root)


def serialized_file_summaries(company_id, sources):
    rows = {row.source_id: row for row in OsvFileAiSummary.query.filter_by(
        company_id=company_id).all()}
    result = []
    for source in sources:
        row = rows.get(source['source_id'])
        status = row.status if row else 'none'
        if (row and status == 'ready'
                and not _has_recommendations(row.content)):
            status = 'outdated'
        result.append({
            'source_id': source['source_id'], 'filename': source['filename'],
            'account': source['account'], 'period': source['period'] or 'Период не указан',
            'start': source['start'].isoformat() if source['start'] else None,
            'end': source['end'].isoformat() if source['end'] else None,
            'status': status,
            'content': row.content if row and status == 'ready' else '',
            'error': row.error_text if row and row.status == 'error' else '',
            'generated_at': (
                row.generated_at.strftime('%d.%m.%Y %H:%M')
                if row and row.generated_at else None),
        })
    return result