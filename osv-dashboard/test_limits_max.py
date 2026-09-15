import io
import json
import ssl
import unittest
from urllib.error import HTTPError

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


if __name__ == '__main__':
    unittest.main()