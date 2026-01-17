import os
import re
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara  # 아라의 두뇌 기능을 가져옵니다.
from database import init_db # 데이터베이스 초기화 함수

app = FastAPI()

# 서버 시작 시 데이터베이스 초기화 (startup_event 통합)
@app.on_event("startup")
def startup_event():
    init_db()

def build_kakao_response(text, max_len=300):
    """텍스트를 분석하여 적절한 카카오톡 UI(카드/링크/퀵리플라이)를 생성합니다."""
    # 설정된 최대 길이만큼 텍스트 자르기
    display_text = text[:max_len]
    
    # 1. 지도/웹 링크 추출 (정규식)
    url_pattern = r'(https?://\S+)'
    urls = re.findall(url_pattern, display_text)
    clean_text = re.sub(url_pattern, '', display_text).strip()

    outputs = []
    if urls:
        # 링크가 있는 경우 BasicCard 생성
        buttons = []
        for url in urls[:3]: # 버튼은 최대 3개
            label = "지도 보기 🔗" if "search.naver" in url or "kakaomap" in url else "자세히 보기 🔗"
            buttons.append({"action": "webLink", "label": label, "webLinkUrl": url})
        
        outputs.append({
            "basicCard": {
                "title": "🐬 아라의 실시간 안내",
                "description": clean_text if clean_text else "아래 링크를 확인해주세요.",
                "buttons": buttons
            }
        })
    else:
        # 일반 텍스트 응답
        outputs.append({"simpleText": {"text": display_text}})

    # 2. 하단 퀵 리플라이 (고정 메뉴 UX 강화)
    quick_replies = [
        {"action": "message", "label": "🚌 190번 버스", "messageText": "190번 버스 어디야?"},
        {"action": "message", "label": "🚐 교내 셔틀", "messageText": "셔틀버스 언제 와?"},
        {"action": "message", "label": "🍱 오늘 학식", "messageText": "오늘 학식 뭐야?"}
    ]

    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": quick_replies
        }
    }

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        # 1. 카카오톡 요청 데이터 파싱
        payload = await request.json()
        user_request = payload.get('userRequest', {})
        utterance = user_request.get('utterance', '')
        user_id = user_request.get('user', {}).get('id', 'unknown_user')

        # 2. 파라미터 추출 및 AI 엔진 스위치 로직
        params = payload.get('action', {}).get('params', {})
        campus_id = str(params.get('campus_id', 'yeongdo_main'))
        max_len = int(params.get('max_response_len', 300))
        
        # [수정] AI 엔진 기본값 True 설정 (선장님의 요청 반영)
        use_ai_param = params.get('use_ai_engine')
        if use_ai_param is None or use_ai_param in [True, "true", "True", "T"]:
            use_ai = True
        else:
            use_ai = False

        # 3. 답변 생성 (비동기 처리 필수)
        if use_ai:
            # await를 빠뜨리면 안 됩니다!
            answer = await ask_ara(utterance, user_id)
        else:
            answer = f"[{campus_id} 알림] 현재 AI 엔진이 꺼져있어 답변이 어렵습니다."

        # 4. 최종 응답 빌드 및 반환
        return build_kakao_response(answer, max_len)

    except Exception as e:
        print(f"🚨 서버 에러 발생: {e}")
        return {
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "아라가 잠시 응답하기 어려운 상태예요. 다시 시도해주세요! 🌊"}}] }
        }

if __name__ == "__main__":
    # Render 환경에 맞는 포트 설정
    uvicorn.run(app, host="0.0.0.0", port=10000)