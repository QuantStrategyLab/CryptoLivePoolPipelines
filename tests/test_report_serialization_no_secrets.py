import json
import os
import re
import unittest
from unittest.mock import patch

from scripts.run_monthly_build_telegram import build_health_payload, send_telegram_message


class ReportSerializationTests(unittest.TestCase):
    def test_report_serialization_has_no_secrets(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        token = str(123456789) + ':' + 'A' * 35
        chat_id = 'synthetic-private-chat'
        for succeeds in (True, False):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.read.return_value = json.dumps({'ok': succeeds}).encode()
            with TemporaryDirectory() as directory, patch.dict(
                os.environ, {'TELEGRAM_BOT_TOKEN': token}, clear=True,
            ), patch('urllib.request.urlopen', return_value=response):
                report = build_health_payload(Path(directory))
                report['notification'] = send_telegram_message(chat_id, 'Monthly status')
            serialized = json.dumps(report)
            self.assertEqual(report['notification']['ok'], succeeds)
            self.assertNotRegex(serialized, r'\d{6,12}:A[\w-]{34}')
            self.assertNotIn(token, serialized)
            self.assertNotIn(chat_id, serialized)

            def check(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key.lower() in {'token', 'secret', 'password', 'api_key'}:
                            self.assertFalse(item)
                        check(item)
                elif isinstance(value, list):
                    for item in value:
                        check(item)
            check(json.loads(serialized))

    def test_provider_exception_does_not_enter_receipt(self):
        token = str(123456789) + ':' + 'A' * 35
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': token}, clear=True), patch(
            'urllib.request.urlopen', side_effect=RuntimeError(token),
        ):
            receipt = send_telegram_message('synthetic-private-chat', 'Monthly status')
        self.assertFalse(receipt['ok'])
        self.assertIsNone(re.search(r'\d{6,12}:A[\w-]{34}', json.dumps(receipt)))
