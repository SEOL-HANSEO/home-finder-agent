import os
from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = None
if api_key:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"Gemini 초기화 오류: {e}")

app = Flask(__name__, static_folder='.', static_url_path='')

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    if not client:
        return jsonify({
            'error': 'API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.'
        }), 500

    data = request.json or {}
    user_message = data.get('message', '').strip()
    context = data.get('context', {})

    if not user_message:
        return jsonify({'error': '메시지를 입력해주세요.'}), 400

    workplace = context.get('workplace', '선택 안됨')
    budget = context.get('budget', '선택 안됨')
    transit = context.get('transit', '선택 안됨')
    selected_area = context.get('selectedArea', '선택 안됨')

    system_instruction = f"""당신은 청년 및 사회초년생을 위한 친절하고 똑똑한 부동산 전문 AI 어시스턴트 '홈파인더 봇'입니다.
사용자의 현재 탐색 조건:
- 직장/학교: {workplace}
- 예산: {budget}
- 선호 교통: {transit}
- 현재 살펴보고 있는 추천 동네: {selected_area}

사용자가 부동산, 전월세 계약, 등기부등본 보는 법, 추천 자취지역, 숨은 관리비나 원룸 구할 때 체크리스트 등을 물어보면,
마크다운 형식(줄바꿈, 불릿포인트 등)을 활용해 핵심을 명확하고 읽기 쉽게 2~4문단 내외로 설명해주세요."""

    prompt = f"{system_instruction}\n\n사용자 질문: {user_message}"

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return jsonify({'reply': response.text})
    except Exception as e:
        return jsonify({'error': f'AI 응답 생성 중 오류: {str(e)}'}), 500

@app.route('/api/recommend', methods=['POST'])
def recommend():
    """전국 어떤 지역(예: 목포, 여수, 원주, 제주 등)이든 AI가 실제 좌표와 추천 동네를 자동 분석"""
    data = request.json or {}
    workplace = data.get('workplace', '').strip()
    budget_text = data.get('budget', '월 50~70만원')
    transit_text = data.get('transit', '대중교통 직통')

    if not workplace:
        return jsonify({'error': '지역/직장명을 입력해주세요.'}), 400

    prompt = f"""직장/학교 위치: "{workplace}".
사용자가 대한민국 "{workplace}" 위치로 매일 출퇴근/통학할 실제 자취방(원룸/오피스텔) 추천 동네 5곳을 엄선해주세요.
조건: 예산 {budget_text}, 통근 스타일 {transit_text}.
중요: 각 동네의 위도(lat)와 경도(lon)는 반드시 실제 "{workplace}" 주변의 실제 한국 지도 좌표여야 합니다.

반드시 다른 부연설명 없이 순수 JSON 배열([ ... ]) 형식으로만 응답하세요:
[
  {{
    "name": "동네/역 이름 (예: 목포 하당지구 / 전주 효자동)",
    "keyword": "검색 키워드 (예: 목포 하당 원룸)",
    "lat": 34.81,
    "lon": 126.42,
    "time": "통근 소요시간 (예: 버스 10분)",
    "price": "예상 월세 (예: 월 35~45만)",
    "rentNum": 40,
    "manageNum": 6,
    "transitNum": 4,
    "pro": "자취 환경 장점",
    "con": "자취 환경 단점",
    "route": "출퇴근 상세 이동 경로"
  }}
]"""

    if client:
        for m in ['gemini-2.5-flash', 'gemini-1.5-flash']:
            try:
                res = client.models.generate_content(model=m, contents=prompt)
                raw_text = res.text.strip()
                start = raw_text.find('[')
                end = raw_text.rfind(']')
                if start != -1 and end != -1:
                    import json
                    items = json.loads(raw_text[start:end+1])
                    return jsonify({'items': items})
            except Exception as ex:
                print(f"Gemini 호출 시도 실패 ({m}): {ex}")

    return jsonify({'error': 'AI 모델 연동 불가, 프론트엔드 지오코딩으로 전환'}), 503

if __name__ == '__main__':
    port = 5000
    print(f"🚀 홈파인더 웹 & AI 챗봇 서버 실행: http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
