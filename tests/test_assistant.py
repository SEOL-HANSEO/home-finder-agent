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
        admin = patch("parking_demo.store.is_admin", return_value=False)
        self.is_admin = admin.start()
        self.addCleanup(admin.stop)
        with self.client.session_transaction() as session:
            session["parking_form_token"] = "test-token"

    def login(self):
        with self.client.session_transaction() as session:
            session["access_token"] = "test-access-token"

    def request_chat(self, message="강남역 근처 노외 무료 주차장"):
        return self.client.post("/assistant", data={"form_token": "test-token", "message": message})

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "reply", "reply": "안녕하세요", "location": "", "place_type": "all"})
    @patch("parking_demo.store.call", return_value=2)
    def test_guest_allowed_and_csrf_required(self, call, respond):
        self.assertEqual(self.request_chat().status_code, 200)
        self.assertEqual(call.call_args.kwargs["body"]["p_actor_limit"], 3)
        call.reset_mock()
        self.assertEqual(self.client.post("/assistant").status_code, 403)
        call.assert_not_called()

    @patch("parking_demo.nearby_parking")
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=-1)
    def test_denied_never_queries_provider(self, call, get_user, nearby):
        self.login()
        self.assertEqual(self.request_chat().status_code, 429)
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
        nearby.assert_called_once_with("강남역", "off_street", limit=None)
        self.assertEqual(result.json["search"]["location"], "강남역")
        self.assertEqual(result.json["search"]["center"], [37, 127])
        self.assertEqual(call.call_args.kwargs["body"]["p_actor_limit"], 3)
        self.assertEqual(len(call.call_args.kwargs["body"]["p_actor_hash"]), 64)

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "reply", "reply": "안녕하세요", "location": "", "place_type": "all"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=9)
    def test_admin_has_ten_daily_messages(self, call, get_user, respond):
        self.login()
        self.is_admin.return_value = True
        self.assertEqual(self.request_chat().status_code, 200)
        self.assertEqual(call.call_args.kwargs["body"]["p_actor_limit"], 10)

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
        self.assertIn(b'id="floating-chat-form"', result.data)

    def test_chat_widget_visible_on_login_and_signup(self):
        for mode in ("login", "signup"):
            result = self.client.get("/account/" + mode)
            self.assertEqual(result.status_code, 200)
            self.assertIn(b'id="floating-chat-form"', result.data)

    @patch("parking_demo.store.record")
    @patch("parking_demo.store.preferences", return_value=[])
    @patch("parking_demo.store.get_user", return_value=USER)
    def test_chat_history_survives_logout(self, get_user, preferences, record):
        self.login()
        history = [{"role": "user", "text": "서울역"}]
        with self.client.session_transaction() as session:
            session["assistant_history"] = history
        result = self.client.post("/", data={"form_token": "test-token", "action": "logout"})
        self.assertEqual(result.status_code, 303)
        with self.client.session_transaction() as session:
            self.assertEqual(session["assistant_history"], history)
            self.assertNotIn("access_token", session)

    @patch("parking_demo.store.record")
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value={"access_token": "token"})
    def test_chat_history_survives_login(self, call, get_user, record):
        history = [{"role": "user", "text": "서울역"}]
        with self.client.session_transaction() as session:
            session["assistant_history"] = history
        result = self.client.post("/account/login", data={"form_token": "test-token", "username": "test@example.com", "password": "password123"})
        self.assertEqual(result.status_code, 303)
        with self.client.session_transaction() as session:
            self.assertEqual(session["assistant_history"], history)
            self.assertEqual(session["access_token"], "token")

    @patch("parking_demo.store.is_admin", return_value=True)
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=[])
    def test_chat_widget_visible_on_admin_page(self, call, get_user, is_admin):
        self.login()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="floating-chat-form"', response.data)

    @patch("parking_demo.add_favorite")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "favorite_add", "number": 1, "target_name": ""})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_favorite_uses_existing_save_function(self, call, get_user, ai, add):
        self.login()
        place = {"name": "울산 주차장", "area": "울산", "lat": 35.54, "lon": 129.31, "distance_km": 0.1}
        with self.client.session_transaction() as session:
            session["search_view"] = {"location": "울산", "place_type": "all", "center": [35.54, 129.31], "places": [place]}
        result = self.request_chat("첫 번째 즐겨찾기해줘")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["favorites_changed"])
        add.assert_called_once_with(USER, "test-access-token", {k: place[k] for k in ("name", "area", "lat", "lon")})

    @patch("parking_demo.add_favorite")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "favorite_add", "number": 1, "target_name": ""})
    @patch("parking_demo.store.call", return_value=2)
    def test_guest_cannot_add_favorites(self, call, ai, add):
        result = self.request_chat("즐겨찾기에 추가해줘")
        self.assertIn("로그인", result.json["message"])
        add.assert_not_called()

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "favorite_remove", "number": 1, "target_name": ""})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call")
    def test_ambiguous_removal_does_not_delete_guessed_favorite(self, call, get_user, ai):
        self.login()
        call.side_effect = [2, [{"place_key": "a" * 64, "name": "A"}, {"place_key": "b" * 64, "name": "B"}]]
        result = self.request_chat("즐겨찾기 삭제해줘")
        self.assertEqual(result.status_code, 200)
        self.assertIn("번호", result.json["message"])
        self.assertEqual(call.call_count, 2)

    @patch("parking_demo.store.save_preferences")
    @patch("parking_demo.store.record")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "save_preferences", "location": "", "place_type": "all"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_saves_current_search_as_default(self, call, get_user, ai, record, save):
        self.login()
        with self.client.session_transaction() as session:
            session["search_view"] = {"location": "울산", "place_type": "off_street", "center": [35.5, 129.3], "places": []}
        result = self.request_chat("현재 검색을 기본으로 저장해줘")
        self.assertEqual(result.json["preferences"]["location"], "울산")
        save.assert_called_once_with("test-access-token", USER["id"], "울산", "off_street")

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "theme", "theme": "dark"})
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_can_change_theme(self, call, ai):
        result = self.request_chat("다크 모드로 바꿔줘")
        self.assertEqual(result.json["theme"], "dark")

    @patch("parking_demo.set_administrator")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "admin_grant", "user_id": "00000000-0000-0000-0000-000000000002"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_does_not_allow_non_owner_admin_grant(self, call, get_user, ai, set_admin):
        self.login()
        result = self.request_chat("00000000-0000-0000-0000-000000000002 관리자 권한 부여해줘")
        self.assertEqual(result.status_code, 200)
        self.assertIn("최고 관리자", result.json["message"])
        set_admin.assert_not_called()

    @patch("parking_demo.set_administrator")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "admin_grant", "user_id": "00000000-0000-0000-0000-000000000002"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_owner_admin_change_requires_explicit_user_id(self, call, get_user, ai, set_admin):
        self.login()
        self.owner.return_value = True
        result = self.request_chat("관리자 권한 부여해줘")
        self.assertIn("ID", result.json["message"])
        set_admin.assert_not_called()
        result = self.request_chat("00000000-0000-0000-0000-000000000002 관리자 권한 부여해줘")
        self.assertTrue(result.json["admin_changed"])
        set_admin.assert_called_once_with(USER, "test-access-token", "00000000-0000-0000-0000-000000000002", True)

    def test_previous_chat_search_can_render_on_home(self):
        with self.client.session_transaction() as session:
            session["search_view"] = {"location": "울산", "place_type": "all", "center": [35.54, 129.31], "places": []}
        result = self.client.get("/?from_chat=1")
        self.assertEqual(result.status_code, 200)
        self.assertIn('value="울산"'.encode(), result.data)

    @patch("parking_demo.add_favorite")
    @patch("parking_demo.store.record")
    @patch("parking_demo.nearby_parking")
    @patch("parking_demo.ai_assistant.respond")
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", side_effect=[2, 1])
    def test_chat_search_then_favorite_uses_same_result(self, call, get_user, ai, nearby, record, add):
        self.login()
        place = {"name": "울산 주차장", "area": "울산", "lat": 35.54, "lon": 129.31, "place_type": "all", "distance_km": 0.2}
        nearby.return_value = ([place], (35.54, 129.31))
        ai.side_effect = [{"action": "search", "location": "울산", "place_type": "all"}, {"action": "favorite_add", "number": 1, "target_name": ""}]
        search = self.request_chat("울산 무료 주차장 찾아줘")
        self.assertEqual(search.json["search"]["location"], "울산")
        self.assertEqual(search.json["search"]["center"], [35.54, 129.31])
        self.assertIn("favorite_token", search.json["search"]["places"][0])
        favorite = self.request_chat("첫 번째 즐겨찾기해줘")
        self.assertTrue(favorite.json["favorites_changed"])
        add.assert_called_once_with(USER, "test-access-token", {k: place[k] for k in ("name", "area", "lat", "lon")})

    @patch("parking_demo.ai_assistant.respond", return_value={"action": "map_select", "number": 2, "target_name": ""})
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_map_selection_refers_to_visible_search_results(self, call, ai):
        with self.client.session_transaction() as session:
            session["search_view"] = {"location": "울산", "place_type": "all", "center": [35.54, 129.31], "places": [{"name": "첫째", "lat": 35.54, "lon": 129.31}, {"name": "둘째", "lat": 35.55, "lon": 129.32}]}
        response = self.request_chat("2번 지도에서 보여줘")
        self.assertEqual(response.json["map_index"], 1)

    @patch("parking_demo.store.record")
    @patch("parking_demo.ai_assistant.respond", return_value={"action": "logout"})
    @patch("parking_demo.store.get_user", return_value=USER)
    @patch("parking_demo.store.call", return_value=2)
    def test_chat_refuses_negated_logout(self, call, get_user, ai, record):
        self.login()
        response = self.request_chat("로그아웃하지 마")
        self.assertNotIn("navigate", response.json)
        with self.client.session_transaction() as session:
            self.assertEqual(session["access_token"], "test-access-token")


if __name__ == "__main__":
    unittest.main()
