# Проверенный контракт MAX

Проверено по опубликованной документации 14 сентября 2026 года, без
авторизованных запросов к API и без отправки сообщений.

- [Обзор](https://dev.max.ru/docs-api): `https://platform-api2.max.ru`,
  заголовок `Authorization: <token>`. Токен в query string не поддерживается.
- [POST /messages](https://dev.max.ru/docs-api/methods/POST/messages):
  адресат `user_id` или `chat_id` в query; тело `text` (до 4000 символов)
  и `attachments`. Клавиатура:
  `{"type":"inline_keyboard","payload":{"buttons":[[...]]}}`.
  Успешный результат — объект `message`, ID — `message.body.mid`.
- [GET /chats/{chatId}/members/admins](https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members/admins):
  массив `members`. [ChatMember](https://dev.max.ru/docs-api/objects/ChatMember)
  содержит **плоские** поля `user_id`, `is_bot`, `is_admin`, `is_owner`.
  Не считать отсутствие `is_bot` доказательством, что это человек.
- [POST /answers](https://dev.max.ru/docs-api/methods/POST/answers):
  `callback_id` в query, в теле документирован `message`.
  Эта страница прямо указывает `updates[i].callback.callback_id`.
  Недокументированное поле `notification` в транспорт не передаётся:
  ответ изменяет сообщение и снимает его клавиатуру.
- [POST /subscriptions](https://dev.max.ru/docs-api/methods/POST/subscriptions):
  HTTPS `url`, `secret`, выбранные `update_types`. Секрет приходит в
  заголовке `X-Max-Bot-Api-Secret`.
- [Update](https://dev.max.ru/docs-api/objects/Update):
  `update_type`, `timestamp`, объекты пользователя/сообщения и данные callback.
  Страница объекта перечисляет `message_callback`, а страница `/answers`
  описывает `callback.callback_id`: обработчик учитывает обе документированные
  формы, не доверяя им как доказательству полномочий.
  Глобальный `update_id` не гарантирован; используется хеш полного
  канонического события с timestamp и идентификаторами сообщения/callback.
- [GET /updates](https://dev.max.ru/docs-api/methods/GET/updates):
  альтернатива webhook, а не параллельный второй потребитель.

Сетевой таймаут после POST — неопределённый исход. Без идемпотентности
провайдера автоматическое «exactly once» невозможно. Принятое MAX сообщение
не помечается прочитанным или доставленным.

`ssl.create_default_context()` и проверка hostname обязательны. Дополнительный
доверенный CA можно передать адаптеру; отключать проверку TLS запрещено.