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
사용자가 대화를 이어가면 이전 문맥을 활용하세요. 사용자가 검색을 요청하지 않았다면 action은 reply입니다.
공공데이터 검색 결과를 아직 받은 적이 없으므로 주차장 이름, 주소, 거리, 이용 가능 여부를 만들어내지 마세요.
reply에는 친절하고 짧은 한국어 답변을 담고, search일 때는 검색 예정이라는 간단한 말만 적으세요.
정확한 정보는 공공데이터를 통해 조회되며 실시간 잔여 공간은 확인할 수 없습니다."""
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "reply"]},
        "reply": {"type": "string"},
        "location": {"type": "string"},
        "place_type": {"type": "string", "enum": ["all", "off_street", "on_street"]},
    },
    "required": ["action", "reply", "location", "place_type"],
}


class AssistantError(Exception):
    pass


def configured():
    # This is an operator attestation, not a technical check of Google's billing tier.
    return bool(setting("GEMINI_API_KEY")) and setting("GEMINI_FREE_TIER_CONFIRMED").lower() == "true"


def respond(message, history):
    if not configured():
        raise AssistantError("AI 서비스가 아직 설정되지 않았습니다. 관리자에게 문의해 주세요.")
    contents = []
    for entry in history[-6:]:
        if isinstance(entry, dict) and entry.get("role") in ("user", "model") and isinstance(entry.get("text"), str):
            contents.append({"role": entry["role"], "parts": [{"text": entry["text"][:500]}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
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
        candidate = data["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            raise ValueError("Incomplete model response")
        parts = candidate["content"]["parts"]
        raw = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        answer = json.loads(raw)
        action, reply = answer["action"], answer["reply"]
        location, kind = answer["location"], answer["place_type"]
        if (action not in ("search", "reply") or not isinstance(reply, str) or not reply.strip()
                or not isinstance(location, str) or len(location.strip()) > 100
                or kind not in ("all", "off_street", "on_street")
                or (action == "search" and not location.strip())):
            raise ValueError("Invalid model response")
        return {"action": action, "reply": reply.strip()[:500], "location": location.strip(), "place_type": kind}
    except HTTPError as error:
        if error.code == 429:
            raise AssistantError("AI 무료 사용 한도에 도달했거나 요청이 많습니다. 잠시 후 다시 시도해 주세요.") from None
        raise AssistantError("AI 서비스에 연결하지 못했습니다. 관리자에게 설정을 확인해 달라고 요청해 주세요.") from None
    except (URLError, OSError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        raise AssistantError("AI 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
