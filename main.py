import os
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

@app.on_event("startup")
def startup():
    init_db()

# [추가] 네비게이션 버튼(퀵 리플) 생성 함수
def get_navigation_buttons():
    """사용자가 자주 묻는 질문을 버튼 형태로 제공합니다."""
    return [
        {"label": "🌡️ 현재 날씨", "action": "message", "messageText": "오늘 학교 날씨 어때?"},
        {"label": "📞 전화번호 찾기", "action": "message", "messageText": "학과 사무실 번호 알려줘"},
        {"label": "🌐 최신 공지사항", "action": "message", "messageText": "학교 홈페이지 최신 공지 검색해줘"},
        {"label": "🍱 오늘 학식", "action": "message", "messageText": "오늘 학식 메뉴 알려줘"},
        {"label": "🚌 셔틀 시간표", "action": "message", "messageText": "순환셔틀 시간표 알려줘"}
    ]

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        user_id = payload['userRequest']['user']['id']
        utterance = payload['userRequest']['utterance']
        
        # 아라의 답변 생성
        response_text = await ask_ara(utterance, user_id)
        
        # [수정] 퀵 리플(QuickReplies)을 포함한 응답 구조
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": response_text
                        }
                    }
                ],
                "quickReplies": get_navigation_buttons() # 👈 네비게이션 버튼 추가
            }
        }
    except Exception as e:
        print(f"🚨 메인 에러: {e}")
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": "아라가 잠시 파도에 휩쓸렸습니다. 다시 시도해주세요! 🌊"}}],
                "quickReplies": get_navigation_buttons() # 에러 시에도 메뉴 노출
            }
        }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)