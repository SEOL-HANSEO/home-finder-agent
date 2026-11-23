import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from parking_demo import favorite_token, parking_demo
from server_session import SQLiteSessionInterface

USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com"}
PLACE = {"name": "서울역 주차장", "area": "서울", "lat": 37.55, "lon": 126.97}


class FavoriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Flask(__name__, instance_path=self.tmp.name, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        self.app.secret_key = "test"
        self.app.session_interface = SQLiteSessionInterface()
        self.app.register_blueprint(parking_demo)
        self.app.testing = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["parking_form_token"] = "csrf"
            session["access_token"] = "user-token"
        with self.app.app_context():
            self.place_token = favorite_token(PLACE)

    @patch("parking_demo.ai_assistant.configured", return_value=False)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_button_and_mock_command_save_same_place(self, call, get_user, configured):
        button = self.client.post("/favorites", data={"form_token": "csrf", "action": "add", "place": self.place_token})
        self.assertEqual(button.status_code, 200)
        first = call.call_args.kwargs
        command = self.client.post("/favorites/command", data={"form_token": "csrf", "command": "1번 즐겨찾기 추가", "place": self.place_token})
        self.assertEqual(command.status_code, 200)
        self.assertEqual(command.json["mode"], "모의 선택")
        self.assertEqual(first, call.call_args.kwargs)

    @patch("parking_demo.ai_assistant.select_favorite", return_value=1)
    @patch("parking_demo.ai_assistant.configured", return_value=True)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_ai_command_uses_quota_and_same_save(self, call, get_user, configured, select):
        call.side_effect = [2, None]
        result = self.client.post("/favorites/command", data={"form_token": "csrf", "command": "서울역 주차장을 저장해", "place": self.place_token})
        self.assertEqual(result.status_code, 200)
        self.assertEqual((result.json["mode"], result.json["remaining"]), ("AI 선택", 2))
        self.assertEqual(call.call_args.kwargs["body"]["name"], PLACE["name"])

    @patch("parking_demo.ai_assistant.configured", return_value=False)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_mock_rejects_ambiguous_or_delete_request(self, call, get_user, configured):
        for command in ("1번 즐겨찾기 삭제", "좋은 곳 추가", "2번 즐겨찾기 추가"):
            result = self.client.post("/favorites/command", data={"form_token": "csrf", "command": command, "place": self.place_token})
            self.assertEqual(result.status_code, 400)
        call.assert_not_called()

    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_tampered_token_and_missing_csrf_rejected(self, call, get_user):
        self.assertEqual(self.client.post("/favorites", data={"form_token": "csrf", "action": "add", "place": self.place_token + "x"}).status_code, 400)
        self.assertEqual(self.client.post("/favorites", data={"action": "add", "place": self.place_token}).status_code, 403)
        call.assert_not_called()

    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=[])
    def test_listing_uses_authenticated_token(self, call, get_user):
        result = self.client.get("/favorites")
        self.assertEqual(result.json["favorites"], [])
        self.assertEqual(call.call_args.kwargs["token"], "user-token")

    @patch("parking_demo.ai_assistant.configured", return_value=False)
    @patch("parking_demo.store.record")
    @patch("parking_demo.store.is_admin", return_value=False)
    @patch("parking_demo.store.preferences", return_value=[])
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.nearby_parking", return_value=([{**PLACE, "place_type": "all", "distance_km": 0.1, "spaces": "", "reference_date": "", "provider": ""}], (37.55, 126.97)))
    def test_search_renders_button_and_mock_mode(self, nearby, get_user, preferences, is_admin, record, configured):
        result = self.client.post("/", data={"form_token": "csrf", "action": "search", "location": "서울역", "place_type": "all"})
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'data-favorite-token=', result.data)
        self.assertIn("모의 선택".encode(), result.data)
        self.assertIn("내 주차장 즐겨찾기".encode(), result.data)


if __name__ == "__main__":
    unittest.main()
