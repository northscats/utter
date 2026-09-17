import io
import json
import ssl
import threading
import time
import unittest
from urllib.error import HTTPError

from limits_connection import _serialized_connection, public_webhook_url
from limits_max import MaxClient, MaxAPIError, MaxUnknownOutcome, event_key, verify_webhook_secret


class Transport:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or {'message': {'body': {'mid': 'mid1'}}}
        self.error = error

    def __call__(self, request, timeout, context):
        self.calls.append((request, timeout, context))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.response).encode())


class MaxAdapterTests(unittest.TestCase):
    def test_public_webhook_url_only_uses_trusted_configuration(self):
        self.assertEqual('', public_webhook_url())
        self.assertEqual(
            'https://override.example/max',
            public_webhook_url(configured='https://override.example/max'))

    def test_connection_registration_lock_serializes_threads(self):
        entered = []
        first_inside = threading.Event()

        def contender(name, wait=0):
            with _serialized_connection():
                entered.append(name)
                if name == 'first':
                    first_inside.set()
                    time.sleep(wait)

        first = threading.Thread(target=contender, args=('first', .05))
        second = threading.Thread(target=contender, args=('second',))
        first.start()
        self.assertTrue(first_inside.wait(1))
        second.start()
        first.join(1)
        second.join(1)
        self.assertEqual(['first', 'second'], entered)

    def test_receiver_preflight_uses_secret_not_bot_token_and_verified_tls(self):
        transport = Transport({'ready': True})
        client = MaxClient('synthetic-bot-token', transport)
        client.check_webhook_receiver('https://example.com/webhook', 'synthetic-secret')
        request, _, context = transport.calls[0]
        self.assertEqual('GET', request.method)
        self.assertEqual('synthetic-secret', request.get_header('X-max-bot-api-secret'))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        with self.assertRaises(ValueError):
            client.check_webhook_receiver('http://example.com/webhook', 'synthetic-secret')
        self.assertEqual(1, len(transport.calls))
        with self.assertRaises(MaxAPIError):
            MaxClient('fake', Transport({'ready': False})).check_webhook_receiver(
                'https://example.com/webhook', 'synthetic-secret')

    def test_receiver_preflight_reports_outdated_publication(self):
        error = HTTPError('https://example.com/webhook', 404, 'missing', {}, None)
        with self.assertRaises(MaxAPIError) as caught:
            MaxClient('fake', Transport(error=error)).check_webhook_receiver(
                'https://example.com/webhook', 'synthetic-secret')
        self.assertEqual('receiver_outdated', caught.exception.code)
        self.assertEqual(404, caught.exception.status)

    def test_unconfigured_never_calls_network(self):
        transport = Transport()
        with self.assertRaises(MaxAPIError):
            MaxClient(transport=transport).send_message(42, 'Тест')
        self.assertFalse(transport.calls)

    def test_auth_header_https_and_result_are_real(self):
        transport = Transport()
        client = MaxClient(token='test-not-a-real-token', transport=transport)
        self.assertEqual('mid1', client.send_message(42, 'Тест')['message']['body']['mid'])
        request, timeout, context = transport.calls[0]
        self.assertEqual('https://platform-api2.max.ru/messages?user_id=42', request.full_url)
        self.assertEqual('test-not-a-real-token', request.get_header('Authorization'))
        self.assertNotIn('test-not', repr(client))
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)

    def test_timeout_is_unknown_no_automatic_retry(self):
        transport = Transport(error=TimeoutError())
        with self.assertRaises(MaxUnknownOutcome) as raised:
            MaxClient('fake', transport).send_message(1, 'Тест')
        self.assertTrue(raised.exception.unknown_outcome)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(1, len(transport.calls))

    def test_429_and_auth_errors_are_distinct(self):
        for status, retryable in ((429, True), (401, False), (403, False), (503, True)):
            transport = Transport(error=HTTPError('redacted', status, 'redacted',
                                                   {'Retry-After': '7'}, None))
            with self.assertRaises(MaxAPIError) as raised:
                MaxClient('fake', transport).send_message(1, 'Тест')
            self.assertEqual(retryable, raised.exception.retryable)
            self.assertEqual(7, raised.exception.retry_after)

    def test_admins_require_explicit_human(self):
        transport = Transport({'members': [
            {'user_id': 1, 'is_bot': False}, {'user_id': 2, 'is_bot': True},
            {'user_id': 3},
        ]})
        client = MaxClient('fake', transport)
        self.assertTrue(client.is_human_admin(-100, 1))
        self.assertFalse(client.is_human_admin(-100, 2))
        self.assertFalse(client.is_human_admin(-100, 3))

    def test_callback_and_webhook_contract(self):
        transport = Transport({'success': True})
        client = MaxClient('fake', transport)
        client.answer_callback('cb1', notification='Одобрено')
        self.assertEqual({'message': {'text': 'Одобрено', 'attachments': []}},
                         json.loads(transport.calls[0][0].data))
        client.subscribe_webhook('https://example.com/limit-notifications/max/webhook', 'fake-secret')
        self.assertEqual('fake-secret', json.loads(transport.calls[1][0].data)['secret'])
        self.assertTrue(verify_webhook_secret('abc', 'abc'))
        self.assertFalse(verify_webhook_secret('', ''))
        payload = {'update_type': 'bot_started', 'timestamp': 1, 'user': {'user_id': 1}}
        self.assertEqual(event_key(payload), event_key(dict(reversed(list(payload.items())))))

    def test_subscription_accepts_real_object_without_success_flag(self):
        response = {'url': 'https://example.com/limit-notifications/max/webhook',
                    'update_types': ['message_created']}
        client = MaxClient('fake', Transport(response))
        self.assertEqual(response, client.subscribe_webhook(response['url'], 'fake-secret'))

    def test_explicit_subscription_rejection_is_an_error(self):
        with self.assertRaises(MaxAPIError) as caught:
            MaxClient('fake', Transport({'success': False})).subscribe_webhook(
                'https://example.com/limit-notifications/max/webhook', 'fake-secret')
        self.assertEqual('rejected', caught.exception.code)


if __name__ == '__main__':
    unittest.main()