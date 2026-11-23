import io
import json
import unittest
from unittest.mock import patch

from case_lifecycle_bench.model_agent import ChatGateway


class ModelGatewayTest(unittest.TestCase):
    def test_user_endpoint_disables_thinking_without_an_authorization_header(self):
        gateway = ChatGateway("http://127.0.0.1:8101/v1", None, enable_thinking=False)
        response = io.BytesIO(b'{"choices":[{"message":{"content":"CONFIRMED"}}]}')

        with patch(
            "case_lifecycle_bench.model_agent.urlopen", return_value=response
        ) as send:
            self.assertEqual(gateway.reply("customer", []), {"content": "CONFIRMED"})

        request = send.call_args.args[0]
        self.assertFalse(request.has_header("Authorization"))
        self.assertEqual(
            json.loads(request.data)["chat_template_kwargs"], {"enable_thinking": False}
        )


class ModelGatewayRetryTest(unittest.TestCase):
    def _http_error(self, code):
        from urllib.error import HTTPError

        return HTTPError("http://gateway/v1/chat/completions", code, "err", {}, None)

    def test_transient_errors_are_retried_with_backoff(self):
        gateway = ChatGateway("http://gateway/v1", "key")
        ok = io.BytesIO(b'{"choices":[{"message":{"content":"done"}}]}')
        failures = [self._http_error(502), ConnectionResetError(), ok]

        def send(*_args, **_kwargs):
            item = failures.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("case_lifecycle_bench.model_agent.urlopen", side_effect=send), patch(
            "case_lifecycle_bench.model_agent.time.sleep"
        ) as sleep:
            self.assertEqual(gateway.reply("agent", []), {"content": "done"})

        self.assertEqual(gateway.calls, 1)
        self.assertEqual(gateway.retries, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_client_errors_are_not_retried(self):
        gateway = ChatGateway("http://gateway/v1", "key")
        with patch(
            "case_lifecycle_bench.model_agent.urlopen",
            side_effect=self._http_error(400),
        ), patch("case_lifecycle_bench.model_agent.time.sleep") as sleep:
            with self.assertRaises(RuntimeError):
                gateway.reply("agent", [])
        self.assertEqual(gateway.retries, 0)
        sleep.assert_not_called()

    def test_gives_up_after_five_retries(self):
        gateway = ChatGateway("http://gateway/v1", "key")
        with patch(
            "case_lifecycle_bench.model_agent.urlopen",
            side_effect=self._http_error(503),
        ), patch("case_lifecycle_bench.model_agent.time.sleep"):
            with self.assertRaises(RuntimeError):
                gateway.reply("agent", [])
        self.assertEqual(gateway.retries, 5)
