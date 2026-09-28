"""Administrator registry and access revocation for MAX users."""
from sqlalchemy import or_

from database import (
    MaxLimitsProfile, MaxLimitsUin, MaxLimitsApplication,
    MaxLimitsSubscription, MaxLimitsOutbox, db, utcnow,
)


def registry(page=1, search=''):
    ids = db.session.query(MaxLimitsProfile.user_id.label('user_id')).union(
        db.session.query(MaxLimitsUin.user_id),
        db.session.query(MaxLimitsApplication.user_id),
        db.session.query(MaxLimitsSubscription.user_id)).subquery()
    query = (db.session.query(ids.c.user_id, MaxLimitsProfile)
             .outerjoin(MaxLimitsProfile, MaxLimitsProfile.user_id == ids.c.user_id)
             .filter(or_(MaxLimitsProfile.step != 'deleted',
                         MaxLimitsProfile.user_id.is_(None))))
    if search:
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        conditions = [MaxLimitsProfile.full_name.ilike(pattern, escape='\\'),
                      MaxLimitsProfile.phone.ilike(pattern, escape='\\')]
        if search.isascii() and search.isdecimal() and len(search) < 19:
            conditions.append(ids.c.user_id == int(search))
        query = query.filter(or_(*conditions))
    total = query.count()
    pages = max(1, (total + 24) // 25)
    page = min(max(page, 1), pages)
    records = query.order_by(ids.c.user_id).offset((page - 1) * 25).limit(25).all()
    user_ids = [user_id for user_id, _ in records]
    uins = {}
    for row in MaxLimitsUin.query.filter(MaxLimitsUin.user_id.in_(user_ids)).order_by(MaxLimitsUin.uin):
        uins.setdefault(row.user_id, []).append(row)
    pending = {row.user_id for row in MaxLimitsApplication.query.filter(
        MaxLimitsApplication.user_id.in_(user_ids),
        MaxLimitsApplication.status == 'pending')}
    rows = []
    for user_id, profile in records:
        subscriptions = uins.get(user_id, [])
        approved = [row.uin for row in subscriptions if row.status == 'approved']
        status = ('На рассмотрении' if user_id in pending else
                  'Доступ одобрен' if approved else
                  'Регистрация не завершена' if profile and profile.step in ('name', 'phone', 'uin', 'choice')
                  else 'Нет одобренного доступа')
        rows.append(dict(user_id=user_id, name=profile.full_name if profile else '',
                         phone=profile.phone if profile else '', status=status,
                         approved=approved))
    return dict(rows=rows, total=total, page=page, pages=pages, search=search)


def remove_user(user_id):
    """Revoke access atomically; keep inactive records so additive restore cannot revive it."""
    profile = db.session.get(MaxLimitsProfile, user_id)
    if profile is None:
        if not any(model.query.filter_by(user_id=user_id).first() for model in (
                MaxLimitsUin, MaxLimitsApplication, MaxLimitsSubscription)):
            return False
        profile = MaxLimitsProfile(user_id=user_id)
        db.session.add(profile)
    profile.step = 'deleted'
    profile.full_name = profile.phone = profile.notice = ''
    MaxLimitsUin.query.filter_by(user_id=user_id).update({'status': 'inactive'})
    MaxLimitsSubscription.query.filter_by(user_id=user_id).update({'active': False})
    MaxLimitsApplication.query.filter_by(user_id=user_id, status='pending').update(
        {'status': 'cancelled'})
    MaxLimitsApplication.query.filter_by(user_id=user_id).update(
        {'full_name': '', 'phone': ''})
    MaxLimitsOutbox.query.filter_by(user_id=user_id, delivered_at=None).update(
        {'cancelled_at': utcnow()})
    db.session.commit()
    return True