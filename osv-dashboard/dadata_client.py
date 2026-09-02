# -*- coding: utf-8 -*-
"""Минимальный серверный клиент DaData findById/party."""
import json
import time
from urllib import error, request

from directory_service import clean_identifier, normalize_party


class DaDataError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def find_party(token, *, inn='', ogrn='', timeout=12):
    inn = clean_identifier(inn, (10, 12))
    ogrn = clean_identifier(ogrn, (13, 15))
    query = inn or ogrn
    if not token:
        raise DaDataError('Токен DaData не настроен')
    if not query:
        raise DaDataError('Укажите корректный ИНН (10/12 цифр) или ОГРН (13/15 цифр)')

    endpoint = 'https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party'
    body = json.dumps({'query': query}).encode('utf-8')
    req = request.Request(
        endpoint, data=body, method='POST',
        headers={'Authorization': 'Token ' + token, 'Content-Type': 'application/json'},
    )
    last_error = None
    for attempt in range(2):
        try:
            with request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode('utf-8'))
            suggestions = payload.get('suggestions') or []
            if ogrn:
                suggestions = [
                    item for item in suggestions
                    if (item.get('data') or {}).get('ogrn') == ogrn
                ]
            if inn:
                suggestions = [
                    item for item in suggestions
                    if (item.get('data') or {}).get('inn') == inn
                ]
            if not suggestions:
                raise DaDataError('Организация по указанным реквизитам не найдена')
            party = suggestions[0]
            return party, normalize_party(party)
        except error.HTTPError as exc:
            detail = 'Провайдер отклонил запрос'
            if exc.code == 401:
                detail = 'Токен DaData недействителен или не имеет доступа'
            elif exc.code == 429:
                detail = 'Превышен лимит запросов DaData, повторите позже'
            elif exc.code >= 500:
                detail = 'DaData временно недоступна'
            last_error = DaDataError(detail, exc.code)
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise last_error
        except error.URLError as exc:
            last_error = DaDataError('Сетевая ошибка при обращении к DaData')
            if attempt:
                raise last_error from exc
        except (ValueError, TimeoutError) as exc:
            last_error = DaDataError('DaData вернула некорректный ответ')
            if attempt:
                raise last_error from exc
        if last_error:
            time.sleep(0.35)
    raise last_error or DaDataError('Не удалось получить ответ DaData')


def check_token(token):
    """Проверяет авторизацию тем же точным API, не раскрывая ответ клиенту."""
    try:
        find_party(token, inn='7707083893', timeout=8)
        return True, 'Подключение к DaData успешно'
    except DaDataError as exc:
        return False, str(exc)