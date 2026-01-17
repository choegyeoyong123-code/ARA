from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db
import uvicorn

app = FastAPI()

@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/kakao")
async def handle_kakao(request: Request):
    try:
        payload = await request.json()
        utterance = payload.get('userRequest', {}).get('utterance', '')
        user_id = payload.get('userRequest', {}).get('user', {}).get('id', 'unknown')
        
        # 파라미터 추출 및 안전장치
        params = payload.get('action', {}).get('params', {})
        # 기본적으로 AI 엔진 사용 활성화
        use_ai = params.get('use_ai_engine', 'true') in [True, 'true', 'True', 'T']
        
        if use_ai:
            # 실측 데이터 기반 답변 획득
            response_text = await ask_ara(utterance, user_id)
        else:
            response_text = "현재 AI 모드가 비활성화되어 있습니다."

        # 카카오톡 응답 형식 반환
        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": response_text}}],
                "quickReplies": [
                    {"action": "message", "label": "🚌 190번 버스", "messageText": "190번 버스 혼잡도 알려줘"},
                    {"action": "message", "label": "🚐 셔틀버스", "messageText": "셔틀버스 시간표"}
                ]
            }
        }
    except Exception as e:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "아라가 통신 중 오류가 발생했습니다."}}]}}