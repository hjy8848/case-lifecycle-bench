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
