"""Gemini-backed parking conversation; parking facts come from the provider."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from supabase_store import setting

MODEL = "gemini-3.1-flash-lite"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
SYSTEM = """당신은 한국의 무료 주차장 검색을 돕는 친절한 대화 도우미입니다.
사용자가 목적지를 알려주고 검색을 원하면 action을 search로, 검색할 실제 지역이나 주소를 location에,
원하는 유형을 place_type에 담으세요. 검색 위치가 모호하면 먼저 구체적인 위치를 질문하세요.
사용자가 대화를 이어가면 이전 문맥과 현재 검색 결과를 활용하세요.
한 문장에 주차장 검색과 즐겨찾기 추가가 함께 요청되면 action은 search로 하고 favorite_after_search를 true로 설정하세요. 검색을 마친 뒤 검색 결과 중 요청한 주차장을 찜합니다. 검색만 요청했으면 false로 설정하세요.
주차장 검색이면 search, 검색 결과를 즐겨찾기에 추가하면 favorite_add, 저장한 즐겨찾기를 지우면 favorite_remove,
기본 검색 설정을 저장하면 save_preferences, 화면 모드 변경이면 theme, 화면 이동이면 navigate,
로그아웃이면 logout, 관리자 권한 부여/해제면 admin_grant/admin_revoke를 선택하세요.
지도에서 검색 결과를 보여 달라는 요청은 map_select, 지도 확대/축소는 map_zoom,
즐겨찾기 목록을 열어 달라는 요청은 favorite_view, 데이터 출처를 열어 달라는 요청은 open_source,
서비스 안내 재생·이전·다음 요청은 guide로 처리하고 그 외에는 reply로 답하세요.
즐겨찾기 요청에 번호가 있으면 number에 숫자를, 이름이 있으면 target_name에 이름을 적으세요.
번호나 이름 없이 현재 검색 결과를 즐겨찾기에 추가해 달라면 가장 가까운 첫 번째 결과를 선택하세요.
찜 취소, 즐겨찾기 취소, 찜 풀기는 favorite_remove입니다. 해제할 곳을 물은 뒤 번호만 답해도 favorite_remove로 처리하세요. 해제하지 말라는 요청은 reply입니다. 저장한 목록의 번호나 이름은 사용자가 직접 지정한 경우에만 넣으세요. 삭제 대상이 모호하면 number를 0으로 하고 target_name을 비워서 사용자에게 다시 물어보도록 하세요.
theme은 dark 또는 light, navigate의 page는 home, login, signup, admin 중 하나입니다.
지도 조작 direction은 in 또는 out, 서비스 안내 direction은 next, previous 또는 play입니다.
관리자 권한 변경 시 user_id는 사용자가 메시지에 직접 쓴 UUID만 그대로 넣고 없으면 빈 문자열로 두세요.
가입 이메일을 직접 적었다면 email에 그대로 넣고 없으면 빈 문자열로 두세요.
비밀번호나 인증정보를 요청하지 말고 로그인이나 가입은 해당 화면으로 안내하세요.
공공데이터 검색 결과를 아직 받은 적이 없으므로 주차장 이름, 주소, 거리, 이용 가능 여부를 만들어내지 마세요.
reply에는 친절하고 짧은 한국어 답변을 담고, search일 때는 검색 예정이라는 간단한 말만 적으세요.
정확한 정보는 공공데이터를 통해 조회되며 실시간 잔여 공간은 확인할 수 없습니다."""
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "reply", "favorite_add", "favorite_remove", "favorite_view", "save_preferences", "theme", "navigate", "logout", "admin_grant", "admin_revoke", "map_select", "map_zoom", "guide", "open_source"]},
        "reply": {"type": "string"},
        "location": {"type": "string"},
        "place_type": {"type": "string", "enum": ["all", "off_street", "on_street"]},
        "favorite_after_search": {"type": "boolean"},
        "number": {"type": "integer"},
        "target_name": {"type": "string"},
        "theme": {"type": "string", "enum": ["dark", "light", ""]},
        "page": {"type": "string", "enum": ["home", "login", "signup", "admin", ""]},
        "user_id": {"type": "string"},
        "email": {"type": "string"},
        "direction": {"type": "string", "enum": ["in", "out", "next", "previous", "play", ""]},
    },
    "required": ["action", "reply", "location", "place_type", "favorite_after_search"],
}


class AssistantError(Exception):
    pass


class SafetyBlockedError(AssistantError):
    """The model's safety filters explicitly blocked a chatbot input."""


def configured():
    # This is an operator attestation, not a technical check of Google's billing tier.
    return bool(setting("GEMINI_API_KEY")) and setting("GEMINI_FREE_TIER_CONFIRMED").lower() == "true"


def respond(message, history, places=None, authenticated=False):
    if not configured():
        raise AssistantError("AI 서비스가 아직 설정되지 않았습니다. 관리자에게 문의해 주세요.")
    contents = []
    for entry in history[-6:]:
        if isinstance(entry, dict) and entry.get("role") in ("user", "model") and isinstance(entry.get("text"), str):
            contents.append({"role": entry["role"], "parts": [{"text": entry["text"][:500]}]})
    current = [{"number": i + 1, "name": p.get("name", ""), "area": p.get("area", "")} for i, p in enumerate((places or [])[:30])]
    contents.append({"role": "user", "parts": [{"text": json.dumps({"message": message, "authenticated": authenticated, "current_search_results": current}, ensure_ascii=False)}]})
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": contents,
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                             "maxOutputTokens": 1024},
    }
    req = Request(URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json", "x-goog-api-key": setting("GEMINI_API_KEY")}, method="POST")
    try:
        with urlopen(req, timeout=25) as response:
            data = json.load(response)
        block_reason = (data.get("promptFeedback") or {}).get("blockReason")
        if block_reason:
            raise SafetyBlockedError("AI 안전 필터가 요청을 차단했습니다.")
        candidate = data["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            if candidate.get("finishReason") in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
                raise SafetyBlockedError("AI 안전 필터가 요청을 차단했습니다.")
            raise ValueError("Incomplete model response")
        parts = candidate["content"]["parts"]
        raw = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        answer = json.loads(raw)
        action, reply = answer["action"], answer["reply"]
        location, kind = answer["location"], answer["place_type"]
        favorite_after_search = answer.get("favorite_after_search", False)
        if (action not in ("search", "reply", "favorite_add", "favorite_remove", "favorite_view", "save_preferences", "theme", "navigate", "logout", "admin_grant", "admin_revoke", "map_select", "map_zoom", "guide", "open_source") or not isinstance(reply, str) or not reply.strip()
                or not isinstance(location, str) or len(location.strip()) > 100
                or kind not in ("all", "off_street", "on_street")
                or not isinstance(favorite_after_search, bool)
                or (action == "search" and not location.strip())):
            raise ValueError("Invalid model response")
        result = {"action": action, "reply": reply.strip()[:500], "location": location.strip(), "place_type": kind}
        if action == "search" and favorite_after_search:
            number = answer.get("number", 0)
            target_name = answer.get("target_name", "")
            if type(number) is not int or not 0 <= number <= 200 or not isinstance(target_name, str) or len(target_name) > 254:
                raise ValueError("Invalid chained favorite selection")
            result.update(favorite_after_search=True, number=number, target_name=target_name)
        if action not in ("search", "reply"):
            number = answer.get("number", 0)
            if type(number) is not int or not 0 <= number <= 200:
                raise ValueError("Invalid selection")
            result.update(number=number, target_name=answer.get("target_name", ""), theme=answer.get("theme", ""), page=answer.get("page", ""), user_id=answer.get("user_id", ""), email=answer.get("email", ""), direction=answer.get("direction", ""))
            if any(not isinstance(result[key], str) or len(result[key]) > 254 for key in ("target_name", "theme", "page", "user_id", "email", "direction")):
                raise ValueError("Invalid action arguments")
        return result
    except SafetyBlockedError:
        raise
    except HTTPError as error:
        if error.code == 429:
            raise AssistantError("AI 무료 사용 한도에 도달했거나 요청이 많습니다. 잠시 후 다시 시도해 주세요.") from None
        raise AssistantError("AI 서비스에 연결하지 못했습니다. 관리자에게 설정을 확인해 달라고 요청해 주세요.") from None
    except (URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        raise AssistantError("AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def select_favorite(message, places):
    """Choose an existing result only; zero means no unambiguous add request."""
    if not configured():
        raise AssistantError("AI 서비스가 아직 설정되지 않았습니다.")
    choices = [{"number": i + 1, "name": p["name"], "area": p["area"]} for i, p in enumerate(places)]
    payload = {
        "systemInstruction": {"parts": [{"text": "사용자의 요청이 목록의 주차장 하나를 즐겨찾기에 추가하는 뜻일 때만 해당 번호를 반환하세요. 그 외, 삭제 요청, 모호한 요청은 0을 반환하세요. 목록에 없는 장소를 만들지 마세요."}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps({"request": message, "places": choices}, ensure_ascii=False)}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}, "maxOutputTokens": 512},
    }
    req = Request(URL, data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": setting("GEMINI_API_KEY")}, method="POST")
    try:
        with urlopen(req, timeout=25) as response:
            data = json.load(response)
        candidate = data["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            raise ValueError("Incomplete response")
        result = json.loads("".join(part.get("text", "") for part in candidate["content"]["parts"] if not part.get("thought")))
        number = result["number"]
        if type(number) is not int or not 0 <= number <= len(places):
            raise ValueError("Invalid selection")
        return number
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        raise AssistantError("AI 선택을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
