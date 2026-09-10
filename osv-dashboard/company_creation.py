"""Duplicate protection shared by manual and file-based enterprise creation."""
import json
import threading
from functools import wraps

from flask import flash, request
from flask_login import current_user
from sqlalchemy import text

from database import AuditLog, Company, db, utcnow
from directory_service import duplicate_companies

_creation_lock = threading.RLock()


def serialized_company_creation(view):
    """Serialize local submissions, including legacy records without identity keys."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method != 'POST':
            return view(*args, **kwargs)
        with _creation_lock:
            return view(*args, **kwargs)
    return wrapped


def normalized_company_name(name):
    return ' '.join(str(name or '').split()).casefold()


def resolve_existing_company(name, inn='', ogrn='', kpp=''):
    """Return one restored enterprise, None for new, or reject a conflict.

    Names are used only when no legal identifiers were supplied. Never merge
    different legal identities just because their display names are identical.
    """
    # Covers simultaneous directory/file submissions across PG processes.
    # Transaction-scoped lock is released on commit/rollback or request teardown.
    if db.session.get_bind().dialect.name == 'postgresql':
        db.session.execute(text('SELECT pg_advisory_xact_lock(624017311)'))
    if inn or ogrn:
        matches = duplicate_companies(inn, ogrn, kpp)
    else:
        key = normalized_company_name(name)
        matches = [item for item in Company.query.all()
                   if key and normalized_company_name(item.name) == key]
    if not matches:
        return None
    if not current_user.is_admin:
        raise ValueError(
            'Предприятие уже существует. Новая запись не создана. '
            'Для восстановления из архива обратитесь к администратору.')
    if len(matches) != 1:
        raise ValueError(
            'Найдена конфликтующая запись: совпадение неоднозначно. '
            'Уточните реквизиты в справочнике; новая запись не создана.')
    company = Company.query.filter_by(id=matches[0].id).populate_existing().with_for_update().first()
    if company is None:
        raise ValueError('Предприятие изменено другим пользователем. Повторите попытку.')
    conflicting_fields = any(
        submitted and stored and submitted != stored
        for submitted, stored in ((inn, company.inn), (ogrn, company.ogrn), (kpp, company.kpp))
    )
    if not company.is_archived or conflicting_fields:
        raise ValueError(
            f'Найдена конфликтующая запись: #{company.id} {company.name}. '
            'Новая запись не создана. Проверьте реквизиты существующего предприятия.')
    company.status = 'active'
    company.updated_at = utcnow()
    db.session.add(AuditLog(
        user_id=current_user.id, action='company_restored',
        details=json.dumps({
            'company_id': company.id, 'source': 'create',
            'previous_status': 'archived', 'status': 'active',
        }, ensure_ascii=False),
        ip=request.remote_addr,
    ))
    # Preserve every existing field, membership and uploaded dataset.
    db.session.commit()
    flash(
        f'Предприятие «{company.name}» найдено в архиве и восстановлено. '
        'Новая запись не создана. Прежний ID и все связанные данные сохранены.',
        'company_restored',
    )
    return company