import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from parking_demo import parking_demo
from parking_provider import ParkingProviderError
from server_session import SQLiteSessionInterface


USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com"}


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        app = Flask(__name__, instance_path=self.tmp.name,
                    template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        app.secret_key = "test"
        app.session_interface = SQLiteSessionInterface()
        app.register_blueprint(parking_demo)
        app.testing = True
        self.client = app.test_client()
        configured = patch("parking_demo.ai_assistant.configured", return_value=True)
        configured.start()
        self.addCleanup(configured.stop)
        owner = patch("parking_demo.store.is_owner", return_value=False)
        self.owner = owner.start()
        self.addCleanup(owner.stop)
        with self.client.session_transaction() as session:
            session["parking_form_token"] = "test-token"

    def login(self):
        with self.client.session_transaction() as session:
            session["access_token"] = "test-access-token"

    def request_chat(self, message="강남역 근처 노외 무료 주차장"):
        return self.client.post("/assistant", data={"form_token": "test-token", "message": message})

    @patch("parking_demo.store.call")
    def test_login_and_csrf_required(self, call):
        self.assertEqual(self.request_chat().status_code, 401)
        self.assertEqual(self.client.post("/assistant").status_code, 403)
        call.assert_not_called()

    @patch("parking_demo.nearby_parking")
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=-1)
    def test_denied_never_queries_provider(self, call, get_user, nearby):
        self.login()
        self.assertEqual(self.request_chat().status_code, 403)
        nearby.assert_not_called()

    @patch("parking_demo.store.record")
    @patch("parking_demo.nearby_parking", return_value=([{"name": "주차장", "area": "주소", "distance_km": 0.1}], (37, 127)))
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "search", "reply": "검색할게요", "location": "강남역", "place_type": "off_street"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_success_consumes_quota_and_returns_nearest(self, call, get_user, ai, nearby, record):
        self.login()
        result = self.request_chat()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["remaining"], 2)
        self.assertEqual(result.json["places"][0]["name"], "주차장")
        nearby.assert_called_once_with("강남역", "off_street", limit=3)
        self.assertEqual(call.call_args.kwargs["body"], {"p_user_id": USER["id"]})

    @patch("parking_demo.store.record")
    @patch("parking_demo.nearby_parking", side_effect=ParkingProviderError("조회 실패"))
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "search", "reply": "검색할게요", "location": "강남역", "place_type": "all"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=1)
    def test_provider_failure_shows_remaining(self, call, get_user, ai, nearby, record):
        self.login()
        result = self.request_chat()
        self.assertEqual((result.status_code, result.json["remaining"]), (503, 1))

    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_invalid_input_does_not_consume_quota(self, call, get_user):
        self.login()
        self.assertEqual(self.request_chat(" ").status_code, 400)
        call.assert_not_called()

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "reply", "reply": "어느 지역을 찾아드릴까요?", "location": "", "place_type": "all"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_followup_uses_conversation_history(self, call, get_user, ai):
        self.login()
        self.assertEqual(self.request_chat("주차장 찾아줘").status_code, 200)
        self.assertEqual(self.request_chat("서울이요").status_code, 200)
        self.assertEqual(ai.call_args.args[1][0]["text"], "주차장 찾아줘")

    @patch("parking_demo.ai_assistant.configured", return_value=False)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_missing_ai_config_does_not_consume_quota(self, call, get_user, configured):
        self.login()
        self.assertEqual(self.request_chat().status_code, 503)
        call.assert_not_called()

    @patch("parking_demo.store.is_admin", return_value=False)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_non_admin_cannot_grant_access(self, call, get_user, is_admin):
        self.login()
        result = self.client.post("/admin", data={"form_token": "test-token", "action": "grant_chat", "user_id": USER["id"]})
        self.assertEqual(result.status_code, 403)
        call.assert_not_called()

    @patch("parking_demo.store.is_admin", return_value=True)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_admin_can_grant_and_revoke(self, call, get_user, is_admin):
        self.login()
        for action, method in (("grant_chat", "POST"), ("revoke_chat", "DELETE")):
            with self.subTest(action=action):
                result = self.client.post("/admin", data={"form_token": "test-token", "action": action, "user_id": USER["id"]})
                self.assertEqual(result.status_code, 303)
                self.assertEqual(call.call_args.kwargs["method"], method)
                self.assertTrue(call.call_args.kwargs["admin"])

    @patch("parking_demo.store.is_admin", return_value=True)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_regular_admin_cannot_change_admin_membership(self, call, get_user, is_admin):
        self.login()
        for action in ("grant_admin", "revoke_admin"):
            result = self.client.post("/admin", data={"form_token": "test-token", "action": action, "user_id": USER["id"]})
            self.assertEqual(result.status_code, 403)
        call.assert_not_called()

    @patch("parking_demo.store.is_admin", return_value=True)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_owner_manages_admins_with_user_token(self, call, get_user, is_admin):
        self.login()
        self.owner.return_value = True
        target = "00000000-0000-0000-0000-000000000002"
        for action in ("grant_admin", "revoke_admin"):
            result = self.client.post("/admin", data={"form_token": "test-token", "action": action, "user_id": target})
            self.assertEqual(result.status_code, 303)
            self.assertEqual(call.call_args.kwargs["token"], "test-access-token")
            self.assertNotIn("admin", call.call_args.kwargs)
        result = self.client.post("/admin", data={"form_token": "test-token", "action": "revoke_admin", "user_id": USER["id"]})
        self.assertEqual(result.status_code, 400)

    @patch("parking_demo.store.is_admin", return_value=False)
    @patch("parking_demo.store.preferences", return_value=[])
    @patch("parking_demo.store.get_user", return_value=USER)
    def test_logged_in_page_renders_chat_and_user_id(self, get_user, preferences, is_admin):
        self.login()
        result = self.client.get("/")
        self.assertEqual(result.status_code, 200)
        self.assertIn(USER["id"].encode(), result.data)
        self.assertIn(b'id="chat-form"', result.data)


if __name__ == "__main__":
    unittest.main()
