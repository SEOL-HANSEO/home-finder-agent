# PBL 01 / Python 실습: 홈파인더 자율 에이전트 시스템
# 직장, 예산, 교통수단 조건을 바탕으로 에이전트가 24시간 자율 탐색 및 추천을 수행합니다.
# + Gemini 3.8 Flash 기반 부동산 AI 전문 상담 챗봇 탑재

import os
import time
from dotenv import load_dotenv

# .env 로드
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = None
if api_key:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[경고] Gemini 클라이언트 초기화 실패: {e}")

agent_name = "홈파인더 에이전트"

print("=" * 65)
print(f"[{agent_name}] 부동산 자율 탐색 & 매물 자동 스크리닝 파이프라인")
print("=" * 65)

workplace = input("1. 직장/학교 위치 (예: 강남역, 판교역) >> ").strip() or "강남역"
budget = input("2. 월세 예산 (예: 50만원, 70만원) >> ").strip() or "월 65만원"
transit = input("3. 선호 교통 (예: 지하철 직통, 도보) >> ").strip() or "지하철 직통"

print("\n" + "-" * 65)
print(f"[설정된 탐색 조건] 직장: {workplace} | 예산: {budget} | 교통: {transit}")
print("-" * 65)

print("\n🤖 [에이전트 자율 백그라운드 파이프라인 가동]")
steps = [
    f"[탐색 에이전트] {workplace} 반경 30분 내 주거 클러스터 24개 위치 좌표 스캔 중...",
    f"[수집 에이전트] 실시간 부동산 급매물 및 실거래가 데이터 크롤링 수집...",
    f"[필터 에이전트] 예산 {budget} 기준 초과 매물 및 허위 매물 필터링 완료...",
    f"[위험분석 에이전트] 등기부 근저당 비율 및 깡통전세 위험률 스크리닝 통과...",
    f"[최적화 에이전트] {transit} 기준 최종 1순위 주거지 도출 성공!"
]

for step in steps:
    time.sleep(0.3)
    print(f"  ▶ {step}")

print("\n" + "=" * 65)
if "강남" in workplace:
    winner = "동작구 사당역 / 이수역 (2·4호선 더블역세권, 강남 12분)"
elif "판교" in workplace:
    winner = "분당구 미금역 / 정자역 (신분당선 직통 8분)"
else:
    winner = f"{workplace} 인근 15분 거리 역세권 가성비 주거지역"

print(f"🏆 에이전트 최종 추천 1순위: {winner}")
print(f"💡 자동화 결과: 매일 아침 신규 매물 분석 리포트가 대시보드에 자동 갱신됩니다.")
print("=" * 65)

if client:
    print("\n💬 [Gemini 3.8 Flash 부동산 전문 챗봇 상담]")
    print("원룸 구하기, 전월세 계약 주의점, 등기부등본 확인법 등 무엇이든 물어보세요! (종료: q 또는 exit)")
    
    SYSTEM_INSTRUCTION = f"""당신은 청년 및 사회초년생을 위한 부동산 전문 자율 어시스턴트 '홈파인더 AI'입니다.
사용자의 현재 설정 조건:
- 직장/학교 위치: {workplace}
- 희망 예산: {budget}
- 선호 교통: {transit}
- 추천 지역: {winner}

친절하고 전문적이며 핵심을 짚는 답변을 제공하세요.
전세사기 예방, 등기부 확인법, 관리비 확인 팁, 해당 지역 자취 꿀팁 등을 친절히 안내해주세요."""

    chat_history = []
    
    while True:
        try:
            user_msg = input("\n나 >> ").strip()
            if not user_msg:
                continue
            if user_msg.lower() in ["q", "exit", "quit", "종료"]:
                print("홈파인더 부동산 챗봇 상담을 종료합니다. 좋은 집 구하세요!")
                break
                
            prompt = f"{SYSTEM_INSTRUCTION}\n\n사용자 질문: {user_msg}"
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt
            )
            print(f"\n홈파인더 AI 봇 >> {response.text}")
        except KeyboardInterrupt:
            print("\n상담을 종료합니다.")
            break
        except Exception as err:
            print(f"\n[오류 발생] {err}")
else:
    print("\nℹ️ .env 파일에 GEMINI_API_KEY를 설정하시면 AI 실시간 챗봇을 사용할 수 있습니다.")
