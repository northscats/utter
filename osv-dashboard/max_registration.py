"""Moderated MAX registrations, approved UIN access, and object catalog."""
import io
import json
import os
import re
import secrets
import ssl
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from openpyxl import Workbook

from database import (
    LimitDataset, LimitObject, LimitVersion, MaxLimitsApplication,
    MaxLimitsOutbox, MaxLimitsProfile, MaxLimitsUin, db, utcnow,
)
from max_dataset_scope import selected_dataset_id

MENU = [[('Изменения', 'menu:changes'), ('Подписки', 'menu:subscriptions')],
        [('Выгрузка Excel', 'menu:changes-excel')]]
HOME = [[('Главное меню', 'menu:home')]]
SUBSCRIPTIONS = [[('Мои объекты', 'menu:uins')],
                 [('Добавить УИН', 'menu:add'), ('Убрать УИН', 'menu:remove')],
                 [('Остановить подписки', 'menu:stop')]] + HOME
REGISTER = [[('Начать регистрацию', 'menu:register')]] + HOME
CHOICE = [[('Добавить ещё', 'apply:more'), ('Завершить', 'apply:finish')]] + HOME
PHONE = re.compile(r'^\+?[0-9][0-9 ()-]{8,19}$')


def group_id():
    try:
        value = int(os.environ.get('MAX_LIMITS_MODERATION_CHAT_ID', ''))
        return value if value else None
    except ValueError:
        return None


def response(text, buttons=None, file=False):
    result = {'text': text}
    result['buttons'] = buttons if buttons is not None else HOME
    if file:
        result['file'] = file
    return result


def profile_for(user_id):
    profile = db.session.get(MaxLimitsProfile, user_id)
    if profile is None:
        profile = MaxLimitsProfile(user_id=user_id, step='name')
        db.session.add(profile)
        db.session.flush()
    return profile


def approved_uins(user_id):
    return [row.uin for row in MaxLimitsUin.query.filter_by(
        user_id=user_id, status='approved').order_by(MaxLimitsUin.uin).all()]


def pending_application(user_id):
    return MaxLimitsApplication.query.filter_by(user_id=user_id, status='pending').first()


def _valid_uin(uin):
    if len(uin) > 120 or not uin or any(ch.isspace() for ch in uin):
        return False
    dataset_id = selected_dataset_id()
    if dataset_id is None:
        return False
    return (db.session.query(LimitObject.id)
            .join(LimitDataset, LimitDataset.id == LimitObject.dataset_id)
            .join(LimitVersion, LimitVersion.id == LimitDataset.current_version_id)
            .filter(LimitDataset.id == dataset_id,
                    LimitObject.uin == uin, LimitObject.is_present.is_(True),
                    LimitObject.last_seen_version_id == LimitVersion.id,
                    LimitVersion.status == 'accepted').first() is not None)


def _drafts(user_id):
    return MaxLimitsUin.query.filter_by(
        user_id=user_id, status='draft').order_by(MaxLimitsUin.uin).all()


def start(user_id):
    profile = profile_for(user_id)
    if pending_application(user_id):
        return response('Ваша заявка ждёт решения группы модерации.', MENU)
    approved = approved_uins(user_id)
    if approved:
        profile.step = 'idle'
        db.session.commit()
        return response('Выберите действие. Объекты открываются по одному, со списком страниц.',
                        MENU)
    profile.step = 'name'
    db.session.commit()
    return response('Здравствуйте! Для регистрации напишите ФИО полностью (фамилия, имя, отчество).')


def _submit(profile):
    if not profile.full_name or not profile.phone:
        return start(profile.user_id)
    drafts = _drafts(profile.user_id)
    if not drafts:
        return response('Сначала укажите хотя бы один УИН.')
    if pending_application(profile.user_id):
        return response('Предыдущая заявка ещё находится на рассмотрении.')
    if not group_id():
        return response('Группа модерации пока не настроена. УИН сохранены; нажмите «Завершить» позже.',
                        CHOICE)
    application = MaxLimitsApplication(
        user_id=profile.user_id, full_name=profile.full_name,
        phone=profile.phone, uins_json=json.dumps([row.uin for row in drafts]))
    db.session.add(application)
    for row in drafts:
        row.status = 'pending'
    profile.step = 'idle'
    db.session.commit()
    return response('Заявка отправлена в группу модерации. После решения вы получите сообщение.')


def handle_text(user_id, text):
    profile = profile_for(user_id)
    if text == '/start':
        return start(user_id)
    if text == '/catalog':
        return response('Отправляю Excel со списком объектов текущих принятых версий.',
                        file=True)
    if text == '/stop':
        for row in MaxLimitsUin.query.filter_by(user_id=user_id).all():
            if row.status in ('approved', 'pending', 'draft'):
                row.status = 'inactive'
        for application in MaxLimitsApplication.query.filter_by(
                user_id=user_id, status='pending').all():
            application.status = 'cancelled'
        MaxLimitsOutbox.query.filter_by(
            user_id=user_id, delivered_at=None, cancelled_at=None).update(
                {'cancelled_at': utcnow()}, synchronize_session=False)
        profile.step = 'idle'
        db.session.commit()
        return response('Подписки остановлены. Можно подать новую заявку.', REGISTER)
    if text in ('/uin', '/changes'):
        from max_object_browser import browse
        mode = 'info' if text == '/uin' else 'changes'
        return browse(user_id, f'browse:list:{mode}:0')
    if text == '/add':
        if pending_application(user_id):
            return response('Дождитесь решения по предыдущей заявке.')
        if not profile.full_name or not profile.phone:
            return start(user_id)
        profile.step = 'uin'
        db.session.commit()
        return response('Введите один точный УИН. Список объектов отправляю файлом Excel.',
                        file=True)
    if text == '/remove':
        return handle_callback(user_id, 'remove-page:0')
    if text.startswith('/'):
        return response('Выберите действие кнопкой ниже.', MENU)
    if profile.step == 'name':
        parts = text.split()
        if len(parts) < 3 or len(text) > 200 or any(not re.fullmatch(
                r"[^\W\d_][^\W\d_'-]*", part, re.UNICODE) for part in parts):
            return response('Напишите ФИО полностью: фамилия, имя и отчество.')
        profile.full_name = text
        profile.step = 'phone'
        db.session.commit()
        return response('Укажите номер телефона, например +79201234567.')
    if profile.step == 'phone':
        if not PHONE.fullmatch(text):
            return response('Укажите корректный номер телефона, например +79201234567.')
        profile.phone = re.sub(r'[^+\d]', '', text)
        profile.step = 'uin'
        db.session.commit()
        return response('Сейчас отправлю Excel со всеми актуальными объектами. '
                        'Введите один точный УИН из файла.', file=True)
    if profile.step == 'uin':
        if profile.phone and re.sub(r'[^+\d]', '', text) == profile.phone:
            return response('Повторно отправляю Excel со списком объектов. '
                            'Введите один точный УИН.', file=True)
        if not _valid_uin(text):
            return response('УИН не найден среди объектов текущих принятых версий. '
                            'Проверьте Excel и отправьте один точный УИН.')
        row = MaxLimitsUin.query.filter_by(user_id=user_id, uin=text).first()
        if row and row.status == 'approved':
            return response('Этот УИН уже одобрен. Введите другой.', MENU)
        if row and row.status == 'pending':
            return response('Этот УИН уже рассматривается. Введите другой.')
        if not row:
            row = MaxLimitsUin(user_id=user_id, uin=text)
            db.session.add(row)
        row.status = 'draft'
        profile.step = 'choice'
        db.session.commit()
        return response(f'Добавлен УИН {text}. Добавить ещё или завершить заявку?', CHOICE)
    if profile.step == 'choice':
        return response('Нажмите «Добавить ещё» или «Завершить».', CHOICE)
    return response('Выберите действие кнопкой ниже.', MENU)


def handle_callback(user_id, payload):
    from max_object_browser import browse
    profile = profile_for(user_id)
    if payload.startswith('browse:'):
        return browse(user_id, payload)
    if payload == 'menu:home':
        return response('Главное меню. Выберите раздел.', MENU)
    if payload == 'menu:subscriptions':
        return response('Подписки. Выберите действие.', SUBSCRIPTIONS)
    if payload == 'menu:register':
        return start(user_id)
    if payload == 'menu:catalog':
        return handle_text(user_id, '/catalog')
    if payload == 'menu:changes-excel':
        from max_digest import changed_uins_for_user
        if not changed_uins_for_user(user_id):
            return response('По вашим одобренным объектам нет изменений в текущем принятом сравнении.', MENU)
        result = response('Отправляю Excel только с изменениями по вашим объектам.',
                          MENU, file='changes')
        result['file_user_id'] = user_id
        return result
    if payload == 'menu:status':
        pending = pending_application(user_id)
        drafts = _drafts(user_id)
        actions = ([[('Продолжить регистрацию', 'menu:add'),
                     ('Отправить заявку', 'apply:finish')]] if drafts and not pending
                   else [[('Начать регистрацию', 'menu:register')]])
        return response(
            'Ваша заявка ждёт решения группы модерации.' if pending else
            f'Одобрено объектов: {len(approved_uins(user_id))}. '
            f'УИН в черновике: {len(drafts)}.', actions + HOME)
    if payload == 'menu:stop':
        return response('Остановить все подписки и отменить ожидающую заявку?',
                        [[('Да, остановить', 'stop:confirm')]] + HOME)
    if payload == 'stop:confirm':
        return handle_text(user_id, '/stop')
    if payload == 'apply:more':
        if not profile.full_name or not profile.phone:
            return start(user_id)
        if pending_application(user_id):
            return response('Дождитесь решения по предыдущей заявке.', MENU)
        profile.step = 'uin'
        db.session.commit()
        return response('Введите следующий точный УИН.')
    if payload == 'apply:finish':
        return _submit(profile)
    if payload == 'menu:uins':
        return browse(user_id, 'browse:list:changes:0')
    if payload == 'menu:changes':
        return browse(user_id, 'browse:list:changes:0')
    if payload == 'menu:add':
        return handle_text(user_id, '/add')
    if payload == 'menu:remove':
        return handle_text(user_id, '/remove')
    if payload.startswith('remove-page:'):
        try:
            page = int(payload.split(':')[1])
        except ValueError:
            return response('Неверная страница.')
        if page < 0 or page > 100000:
            return response('Неверная страница.')
        rows = MaxLimitsUin.query.filter_by(
            user_id=user_id, status='approved').order_by(
                MaxLimitsUin.uin).offset(page * 8).limit(9).all()
        if not rows:
            return response('Других подписок нет.', SUBSCRIPTIONS)
        buttons = [[(row.uin[:90], f'remove:{row.id}')] for row in rows[:8]]
        if len(rows) > 8:
            buttons.append([('Следующие УИН', f'remove-page:{page + 1}')])
        if page:
            buttons.append([('Предыдущие УИН', f'remove-page:{page - 1}')])
        return response(f'Выберите УИН для отключения · страница {page + 1}:',
                        buttons + [[('К подпискам', 'menu:subscriptions')]] + HOME)
    if payload.startswith(('remove:', 'remove-confirm:')):
        try:
            row_id = int(payload.split(':', 1)[1])
        except ValueError:
            return response('Неизвестная кнопка.')
        row = db.session.get(MaxLimitsUin, row_id)
        if row is None or row.user_id != user_id or row.status != 'approved':
            return response('Подписка уже отсутствует.', SUBSCRIPTIONS)
        if payload.startswith('remove:'):
            return response(f'Отключить подписку на УИН {row.uin}?',
                            [[('Да, отключить', f'remove-confirm:{row.id}')],
                             [('Назад к списку УИН', 'remove-page:0')],
                             [('К подпискам', 'menu:subscriptions')]] + HOME)
        row.status = 'inactive'
        MaxLimitsOutbox.query.filter_by(
            user_id=user_id, uin=row.uin, delivered_at=None, cancelled_at=None).update(
                {'cancelled_at': utcnow()}, synchronize_session=False)
        db.session.commit()
        return response(f'Подписка на УИН {row.uin} отключена.', SUBSCRIPTIONS)
    return response('Неизвестная кнопка.')


def decide(chat_id, moderator_id, payload):
    if chat_id != group_id():
        return response('Эта группа не назначена для модерации.')
    parts = payload.split(':')
    if len(parts) != 3 or parts[0] != 'mod' or parts[2] not in ('approve', 'reject'):
        return response('Неизвестное решение.')
    try:
        application = db.session.get(MaxLimitsApplication, int(parts[1]))
    except ValueError:
        return response('Неверный номер заявки.')
    if application is None:
        return response('Заявка не найдена.')
    if application.status != 'pending':
        return response('Заявка уже рассмотрена.')
    if not application.sent_at:
        return response('Заявка ещё не была отправлена группе.')
    uins = json.loads(application.uins_json)
    for uin in uins:
        row = MaxLimitsUin.query.filter_by(user_id=application.user_id, uin=uin).first()
        if row and row.status == 'pending':
            row.status = 'approved' if parts[2] == 'approve' else 'rejected'
    status = 'approved' if parts[2] == 'approve' else 'rejected'
    claimed = MaxLimitsApplication.query.filter_by(
        id=application.id, status='pending').update({
            'status': status, 'decided_by': moderator_id, 'decided_at': utcnow(),
        }, synchronize_session='fetch')
    if not claimed:
        db.session.rollback()
        return response('Заявка уже рассмотрена.')
    profile = db.session.get(MaxLimitsProfile, application.user_id)
    if profile:
        profile.notice = (
            f'Ваша заявка №{application.id} одобрена. Доступны УИН: {", ".join(uins)}. '
            'Откройте «Мои объекты» в меню.'
            if status == 'approved' else
            f'Ваша заявка №{application.id} не одобрена. '
            'Для повторной заявки нажмите «Начать регистрацию».')
    db.session.commit()
    return response(f'Заявка №{application.id} {("одобрена" if status == "approved" else "отклонена")}.')


def catalog_bytes():
    """Only current accepted objects; no historical or unreviewed rows."""
    dataset_id = selected_dataset_id()
    records = (db.session.query(LimitObject.uin, LimitObject.title, LimitObject.address)
               .join(LimitDataset, LimitDataset.id == LimitObject.dataset_id)
               .join(LimitVersion, LimitVersion.id == LimitDataset.current_version_id)
               .filter(LimitDataset.id == dataset_id,
                       LimitObject.is_present.is_(True),
                       LimitObject.last_seen_version_id == LimitVersion.id,
                       LimitVersion.status == 'accepted')
               .order_by(LimitObject.uin).all())
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Объекты')
    sheet.append(['УИН', 'Наименование объекта', 'Адрес'])
    seen = set()
    for uin, title, address in records:
        values = (uin, title or '', address or '')
        if not uin or values in seen:
            continue
        seen.add(values)
        # Excel cells are untrusted source data: avoid formula injection.
        sheet.append([("'" + str(v) if str(v).startswith(('=', '+', '-', '@')) else str(v))
                      for v in values])
    output = io.BytesIO()
    book.save(output)
    if output.tell() > 10 * 1024 * 1024:
        raise ValueError('Object catalog is too large for a bot attachment')
    return output.getvalue()


def upload_catalog(token, api_call, data=None, filename='objects.xlsx'):
    """Upload generated XLSX to a validated MAX media host, then return its token."""
    from max_limits import TRUSTED_CA
    data = catalog_bytes() if data is None else data
    info = api_call(token, 'POST', '/uploads', {'type': 'file'}, timeout=15)
    url = info.get('url', '')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in (
            'fu.oneme.ru', 'omu.okcdn.ru', 'omub.okcdn.ru') or parts.port not in (None, 443):
        raise ValueError('MAX returned an untrusted upload host')
    boundary = '----max-' + secrets.token_hex(12)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; '
            f'filename="{filename}"\r\nContent-Type: '
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            '\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(TRUSTED_CA))
    request = Request(url, data=body, headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Authorization': token,
    }, method='POST')
    with urlopen(request, timeout=30, context=context) as result:
        uploaded = json.loads(result.read(4096))
    if not isinstance(uploaded, dict) or not uploaded.get('token'):
        raise ValueError('MAX file upload did not return a token')
    return uploaded['token']


def pending_moderation(api_call, token):
    """Retry applications and decision notices without sending anything unapproved."""
    chat_id = group_id()
    if chat_id:
        for application in MaxLimitsApplication.query.filter_by(
                status='pending', sent_at=None).order_by(MaxLimitsApplication.id).limit(5):
            uins = ', '.join(json.loads(application.uins_json))
            text = (f'📝 Новая заявка на регистрацию №{application.id}\n\n'
                    f'🆔 ID пользователя: {application.user_id}\n'
                    f'👤 ФИО: {application.full_name}\n'
                    f'📱 Телефон: {application.phone}\n'
                    f'🏗 УИН: {uins}')
            buttons = [[('Одобрить', f'mod:{application.id}:approve'),
                        ('Не одобрить', f'mod:{application.id}:reject')]]
            try:
                from max_limits import send_message
                send_message(token, chat_id, text, buttons=buttons, chat=True,
                             api_call=api_call)
                application.sent_at = utcnow()
                db.session.commit()
            except Exception:
                db.session.rollback()
                break
    for profile in MaxLimitsProfile.query.filter(MaxLimitsProfile.notice != '').limit(10):
        try:
            from max_limits import send_message
            send_message(token, profile.user_id, profile.notice, api_call=api_call,
                         buttons=MENU if approved_uins(profile.user_id) else REGISTER)
            profile.notice = ''
            db.session.commit()
        except Exception:
            db.session.rollback()
            break