# -*- coding: utf-8 -*-
"""Mocked contract tests for the durable limits MAX worker."""
import unittest
import json
import os
import tempfile
from datetime import date

_TEMP = tempfile.TemporaryDirectory(prefix='limits_bot_test_')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TEMP.name + '/bot.db'
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')
os.environ.setdefault('ENCRYPTION_KEY', 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')

from app import app, init_db
import database
from database import (LimitBotOutbound, LimitDataset, LimitDelivery, LimitMaxEvent, LimitModerationRequest,
                      LimitObject, LimitRecipient, LimitSubscription, Setting, User, db)
from limits_bot import (LimitsBotError, ingest_event, recover, retry_bot_outbound,
                        retry_event, run_worker_once)
from limits_max import MaxAPIError


class FakeMax:
    is_configured = True

    def __init__(self):
        self.sent, self.admins, self.timeout, self.failure, self.admin_failure = (
            [], {'moderator'}, False, None, None)

    def send_message(self, user_id=None, text=None, chat_id=None, attachments=None):
        target = chat_id if chat_id is not None else user_id
        self.sent.append((str(target), text, attachments or []))
        if self.timeout:
            raise TimeoutError('network outcome unknown')
        if self.failure:
            raise self.failure
        return {'body': {'mid': 'mx-%d' % len(self.sent)}}

    def get_chat_admins(self, group_id):
        self.last_group = str(group_id)
        return self.admins

    def is_human_admin(self, group_id, user_id):
        self.last_group = str(group_id)
        if self.admin_failure:
            raise self.admin_failure
        return str(user_id) in self.admins

    def answer_callback(self, callback_id, text=None, message=None, notification=None):
        self.answer = (callback_id, text)


class LimitsBotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        init_db()

    def setUp(self):
        with app.app_context():
            for table in reversed(db.metadata.sorted_tables):
                db.session.execute(table.delete())
            admin = User(username='bot-admin', password_hash='x', role='admin')
            db.session.add(admin)
            db.session.flush()
            dataset = LimitDataset(name='limits', owner_id=admin.id)
            db.session.add(dataset)
            db.session.flush()
            db.session.add(LimitObject(dataset_id=dataset.id, uin='100.200'))
            Setting.set('limits_bot_dataset_id', str(dataset.id))
            Setting.set('limits_max_enabled', '1')
            Setting.set('limits_max_token', 'encrypted-test-token')
            Setting.set('limits_max_group_chat_id', 'moderators')
            Setting.set('limits_max_webhook_registered', '1')
            db.session.commit()
            self.dataset_id = dataset.id

    def event(self, event_id, payload, max_client=None, group_chat_id='moderators'):
        with app.app_context():
            ingest_event(event_id, payload)
            return run_worker_once(max_client or FakeMax(), group_chat_id=group_chat_id)

    def test_duplicate_event_creates_one_durable_reply(self):
        with app.app_context():
            _, first = ingest_event('same', {'update_type': 'bot_started', 'timestamp': 1,
                                              'user': {'user_id': 'u1'}})
            _, second = ingest_event('same', {'update_type': 'bot_started', 'timestamp': 1,
                                               'user': {'user_id': 'u1'}})
            self.assertTrue(first)
            self.assertFalse(second)
            client = FakeMax()
            result = run_worker_once(client, group_chat_id='moderators')
            self.assertEqual(1, result['events'])
            self.assertEqual(1, LimitBotOutbound.query.count())
            self.assertEqual('processed', LimitMaxEvent.query.one().status)

    def test_worker_uses_configured_group_without_nested_transaction(self):
        with app.app_context():
            Setting.set('limits_max_group_chat_id', 'moderators')
            db.session.commit()
            event, _ = ingest_event('provider-id-not-primary-key', {
                'update_type': 'bot_started', 'timestamp': 19, 'user': {'user_id': 'u1'}})
            event_pk = event.id
            result = run_worker_once(FakeMax())
            self.assertEqual(1, result['events'])
            self.assertEqual('processed', db.session.get(LimitMaxEvent, event_pk).status)
            db.session.get(LimitMaxEvent, event_pk).status = 'error'
            db.session.commit()
            retry_event(event_pk, by_pk=True)
            self.assertEqual('received', db.session.get(LimitMaxEvent, event_pk).status)

    def test_card_message_binding_and_one_time_current_admin_decision(self):
        client = FakeMax()
        with app.app_context():
            recipient = LimitRecipient(max_user_id='u1', organization_inn='7707083893',
                                       surname_initials='Иванов И.И.', phone='+79990000000')
            db.session.add(recipient)
            db.session.flush()
            request = LimitModerationRequest(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                             initial_uin='100.200', group_chat_id='moderators')
            db.session.add(request)
            db.session.flush()
            from limits_bot import _queue_moderation_card
            _queue_moderation_card(request, 'moderators')
            db.session.commit()
            button = json.loads(LimitBotOutbound.query.one().keyboard_json)[0][0]
            self.assertEqual('callback', button['type'])
            self.assertIn('payload', button)
            self.assertNotIn('callback_data', button)
            run_worker_once(client, group_chat_id='moderators')
            attachment = next(item[2] for item in client.sent if item[0] == 'moderators')
            self.assertEqual('inline_keyboard', attachment[0]['type'])
            self.assertEqual('callback', attachment[0]['payload']['buttons'][0][0]['type'])
            request_id = request.id
            message_id = db.session.get(LimitModerationRequest, request_id).moderation_message_id
            self.assertTrue(message_id)
        self.event('decision', {'update_type': 'message_callback', 'timestamp': 1,
                                'callback': {'callback_id': 'cb1',
                                             'user': {'user_id': 'moderator'},
                                             'payload': 'limits:approve:%d' % request_id},
                                'message': {'recipient': {'chat_id': 'moderators',
                                                          'chat_type': 'chat'},
                                            'body': {'mid': message_id}}}, client)
        with app.app_context():
            recipient = LimitRecipient.query.filter_by(max_user_id='u1').one()
            self.assertEqual('approved', recipient.access_status)
            self.assertTrue(LimitSubscription.query.filter_by(recipient_id=recipient.id,
                                                               uin='100.200', is_active=True).first())
        # An old button cannot reverse or duplicate the one-time decision.
        self.event('decision-again', {'update_type': 'message_callback', 'timestamp': 2,
                                      'user': {'user_id': 'moderator'}, 'chat_id': 'moderators',
                                      'message_callback': {'message_id': message_id,
                                                           'payload': 'limits:approve:%d' % request_id}}, client)
        with app.app_context():
            self.assertEqual(1, LimitSubscription.query.count())
            self.assertEqual('error', LimitMaxEvent.query.filter_by(event_id='decision-again').one().status)

    def test_transient_admin_lookup_retries_event_without_decision(self):
        client = FakeMax()
        client.admin_failure = MaxAPIError('MAX: HTTP 503.', status=503, retryable=True)
        with app.app_context():
            recipient = LimitRecipient(max_user_id='u1')
            db.session.add(recipient)
            db.session.flush()
            request = LimitModerationRequest(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                             initial_uin='100.200', group_chat_id='77',
                                             moderation_message_id='99')
            db.session.add(request)
            db.session.commit()
            request_id = request.id
        self.event('temporary-admins', {
            'update_type': 'message_callback', 'timestamp': 1,
            'callback': {'callback_id': 'cb', 'user': {'user_id': 'moderator'},
                         'payload': 'limits:approve:%d' % request_id},
            'message': {'recipient': {'chat_id': '77', 'chat_type': 'chat'}, 'body': {'mid': '99'}},
        }, client, group_chat_id='77')
        with app.app_context():
            event = LimitMaxEvent.query.filter_by(event_id='temporary-admins').one()
            self.assertEqual('retry', event.status)
            self.assertEqual(1, event.attempts)
            self.assertEqual('pending', db.session.get(LimitModerationRequest, request_id).status)
            retry_event('temporary-admins')
            self.assertEqual('received', LimitMaxEvent.query.filter_by(
                event_id='temporary-admins').one().status)

    def test_recovery_requeues_crash_claimed_event(self):
        with app.app_context():
            ingest_event('crash-claim', {
                'update_type': 'bot_started', 'timestamp': 1, 'user': {'user_id': 'u1'},
            })
            event = LimitMaxEvent.query.filter_by(event_id='crash-claim').one()
            event.status, event.attempts = 'processing', 1
            db.session.commit()
            self.assertEqual(1, recover())
            event = LimitMaxEvent.query.filter_by(event_id='crash-claim').one()
            self.assertEqual('retry', event.status)
            self.assertEqual(1, event.attempts)

    def test_explicit_unknown_card_retry_invalidates_old_callback_binding(self):
        with app.app_context():
            recipient = LimitRecipient(max_user_id='u1')
            db.session.add(recipient)
            db.session.flush()
            request = LimitModerationRequest(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                             initial_uin='100.200', group_chat_id='77',
                                             moderation_message_id='old-mid')
            db.session.add(request)
            db.session.flush()
            outbound = LimitBotOutbound(target_id='77', content='card', status='unknown',
                                        moderation_request_id=request.id)
            db.session.add(outbound)
            db.session.commit()
            with self.assertRaises(LimitsBotError):
                retry_bot_outbound(outbound.id)
            retry_bot_outbound(outbound.id, confirm_unknown=True)
            self.assertEqual('queued', db.session.get(LimitBotOutbound, outbound.id).status)
            self.assertEqual('', db.session.get(LimitModerationRequest, request.id).moderation_message_id)

    def test_registration_consent_and_full_uin_create_one_moderation_card(self):
        client = FakeMax()
        for event_id, text in (
                ('start', '/start'), ('consent', 'Согласен'), ('inn', '7707083893'),
                ('initials', 'Иванов И.И.'), ('phone', '+7 999 000-00-00'),
                ('uin', '100.200')):
            self.event(event_id, {'update_type': 'message_created', 'timestamp': event_id,
                                  'user': {'user_id': 'new-user'}, 'message': {'text': text}}, client)
        with app.app_context():
            recipient = LimitRecipient.query.filter_by(max_user_id='new-user').one()
            request = LimitModerationRequest.query.filter_by(recipient_id=recipient.id).one()
            self.assertEqual('pending', request.status)
            self.assertEqual('moderators', request.group_chat_id)
            self.assertEqual('100.200', request.initial_uin)
            self.assertTrue(request.moderation_message_id)
            if hasattr(recipient, 'consent_granted_at'):
                self.assertIsNotNone(recipient.consent_granted_at)

    def test_approved_recipient_can_subscribe_mine_and_unsubscribe(self):
        client = FakeMax()
        with app.app_context():
            recipient = LimitRecipient(max_user_id='approved', access_status='approved')
            db.session.add(recipient)
            db.session.commit()
        self.event('subscribe', {'update_type': 'message_created', 'timestamp': 1,
                                 'user': {'user_id': 'approved'},
                                 'message': {'text': '/subscribe 100.200'}}, client)
        self.event('mine', {'update_type': 'message_created', 'timestamp': 2,
                            'user': {'user_id': 'approved'}, 'message': {'text': '/mine'}}, client)
        self.event('unsubscribe', {'update_type': 'message_created', 'timestamp': 3,
                                   'user': {'user_id': 'approved'},
                                   'message': {'text': '/unsubscribe 100.200'}}, client)
        with app.app_context():
            self.assertFalse(LimitSubscription.query.one().is_active)
            self.assertTrue(any('Ваши объекты: 100.200' in item[1] for item in client.sent))

    def test_group_message_never_starts_contact_registration(self):
        client = FakeMax()
        self.event('group-start', {
            'update_type': 'message_created', 'timestamp': 1, 'user': {'user_id': 'group-user'},
            'message': {'recipient': {'chat_id': '900', 'chat_type': 'chat'},
                        'body': {'text': '/start'}},
        }, client)
        with app.app_context():
            self.assertIsNone(LimitRecipient.query.filter_by(max_user_id='group-user').first())
            self.assertTrue(any('только в личном диалоге' in item[1] for item in client.sent))

    def test_timeout_and_restart_turn_sending_into_unknown_not_retry(self):
        client = FakeMax()
        client.timeout = True
        with app.app_context():
            from limits_bot import _queue_message
            _queue_message('u1', 'reply')
            db.session.commit()
            run_worker_once(client, group_chat_id='moderators')
            self.assertEqual('unknown', LimitBotOutbound.query.one().status)
            LimitBotOutbound.query.one().status = 'sending'
            db.session.commit()
            self.assertEqual(1, recover())
            self.assertEqual('unknown', LimitBotOutbound.query.one().status)

    def test_long_alert_is_losslessly_sent_as_durable_parts(self):
        Part = database.LimitDeliveryPart
        client = FakeMax()
        original = ('Изменение лимитов по УИН 100.200\n' + ('• финансовое поле изменено\n' * 220))
        with app.app_context():
            recipient = LimitRecipient(max_user_id='10001', access_status='approved',
                                       consent_granted_at=database.utcnow())
            db.session.add(recipient)
            db.session.flush()
            version = database.LimitVersion(
                dataset_id=self.dataset_id,
                uploaded_by=User.query.filter_by(username='bot-admin').one().id,
                source_date=date(2026, 1, 1), source_name='test.xlsx', source_path='/tmp/test.xlsx',
                sha256='a' * 64, status='accepted')
            db.session.add(version)
            db.session.flush()
            obj = LimitObject.query.filter_by(dataset_id=self.dataset_id, uin='100.200').one()
            db.session.add(LimitSubscription(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                              uin=obj.uin))
            db.session.add(LimitDelivery(version_id=version.id, object_id=obj.id,
                                         recipient_id=recipient.id, content=original))
            db.session.commit()
            result = run_worker_once(client, group_chat_id='moderators')
            parts = Part.query.order_by(Part.part_no).all()
            delivery = LimitDelivery.query.one()
            self.assertGreater(len(parts), 1)
            self.assertEqual(original, ''.join(part.content for part in parts))
            self.assertTrue(all(part.status == 'accepted' for part in parts))
            self.assertEqual('accepted', delivery.status)
            self.assertEqual(len(parts), result['alerts'])

    def test_non_retryable_max_error_marks_part_and_parent_error(self):
        Part = database.LimitDeliveryPart
        client = FakeMax()
        client.failure = MaxAPIError('MAX: HTTP 401.', status=401, retryable=False)
        with app.app_context():
            recipient = LimitRecipient(max_user_id='10002', access_status='approved',
                                       consent_granted_at=database.utcnow())
            db.session.add(recipient)
            db.session.flush()
            version = database.LimitVersion(
                dataset_id=self.dataset_id,
                uploaded_by=User.query.filter_by(username='bot-admin').one().id,
                source_date=date(2026, 1, 1), source_name='test.xlsx', source_path='/tmp/test.xlsx',
                sha256='b' * 64, status='accepted')
            db.session.add(version)
            db.session.flush()
            obj = LimitObject.query.filter_by(dataset_id=self.dataset_id, uin='100.200').one()
            db.session.add(LimitSubscription(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                              uin=obj.uin))
            db.session.add(LimitDelivery(version_id=version.id, object_id=obj.id,
                                         recipient_id=recipient.id, content='Проверка ошибки MAX.'))
            db.session.commit()
            run_worker_once(client, group_chat_id='moderators')
            self.assertEqual('error', Part.query.one().status)
            self.assertEqual('error', LimitDelivery.query.one().status)

    def test_stopped_user_has_subscriptions_and_queued_messages_cancelled(self):
        with app.app_context():
            recipient = LimitRecipient(max_user_id='u1', access_status='approved')
            db.session.add(recipient)
            db.session.flush()
            db.session.add(LimitSubscription(recipient_id=recipient.id, dataset_id=self.dataset_id,
                                              uin='100.200'))
            from limits_bot import _queue_message
            _queue_message('u1', 'will not send')
            db.session.commit()
        self.event('stopped', {'update_type': 'bot_stopped', 'timestamp': 1,
                               'user': {'user_id': 'u1'}})
        with app.app_context():
            recipient = LimitRecipient.query.filter_by(max_user_id='u1').one()
            self.assertEqual('approved', recipient.access_status)
            self.assertIsNotNone(recipient.stopped_at)
            self.assertTrue(LimitSubscription.query.one().is_active)
            self.assertEqual('cancelled', LimitBotOutbound.query.one().status)
        self.event('started-again', {'update_type': 'bot_started', 'timestamp': 2,
                                     'user': {'user_id': 'u1'}})
        with app.app_context():
            self.assertIsNone(LimitRecipient.query.filter_by(max_user_id='u1').one().stopped_at)
            self.assertTrue(LimitSubscription.query.one().is_active)


if __name__ == '__main__':
    unittest.main()