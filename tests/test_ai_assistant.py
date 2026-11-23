import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import ai_assistant


def settings(name):
    return {"GEMINI_API_KEY": "test-key", "GEMINI_FREE_TIER_CONFIRMED": "true"}.get(name, "")


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *args):
        return False


class GeminiTests(unittest.TestCase):
    @patch("ai_assistant.setting", side_effect=settings)
    @patch("ai_assistant.urlopen")
    def test_model_response_and_history_are_sent_to_fixed_model(self, urlopen, setting):
        model_answer = {"action": "search", "reply": "찾아볼게요.", "location": "서울역", "place_type": "on_street"}
        urlopen.return_value = FakeResponse({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(model_answer)}]}}]})
        answer = ai_assistant.respond("노상으로", [{"role": "user", "text": "서울역 주변"}, {"role": "model", "text": "어떤 유형인가요?"}])
        self.assertEqual(answer, model_answer)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn("gemini-3.1-flash-lite", request.full_url)
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "서울역 주변")
        self.assertNotIn("tools", payload)

    @patch("ai_assistant.setting", side_effect=settings)
    @patch("ai_assistant.urlopen")
    def test_invalid_model_response_does_not_return_untrusted_data(self, urlopen, setting):
        urlopen.return_value = FakeResponse({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"action":"search"}'}]}}]})
        with self.assertRaises(ai_assistant.AssistantError):
            ai_assistant.respond("검색해줘", [])

    @patch("ai_assistant.setting", side_effect=settings)
    @patch("ai_assistant.urlopen", side_effect=HTTPError("https://example.test", 429, "limit", {}, None))
    def test_quota_error_is_reported_without_fallback(self, urlopen, setting):
        with self.assertRaisesRegex(ai_assistant.AssistantError, "한도"):
            ai_assistant.respond("안녕", [])
        self.assertEqual(urlopen.call_count, 1)

    @patch("ai_assistant.setting", return_value="")
    @patch("ai_assistant.urlopen")
    def test_unconfigured_service_never_calls_google(self, urlopen, setting):
        with self.assertRaises(ai_assistant.AssistantError):
            ai_assistant.respond("안녕", [])
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
