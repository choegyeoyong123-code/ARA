import os
import re
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

# 서버 시작 시 DB 초기화
@app.on_event("startup")
def startup_event():
    init_db()

def build_kakao_response(text, max_len=300):
    """카카오톡 전용 UI 빌더 (링크 버튼 자동 생성)"""
    display_text = text[:max_len]
    url_pattern = r'(https?://\S+)'
    urls = re.findall(url_pattern, display_text)
    clean_text = re.sub(url_pattern, '', display_text).strip()

    outputs = []
    if urls:
        buttons = [{"action": "webLink", "label": "지도/상세보기 🔗", "webLinkUrl": urls[0]}]
        outputs.append({
            "basicCard": {
                "title": "🐬 아라의 실시간 안내",
                "description": clean_text if clean_text else "아래 링크를 확인하세요.",
                "buttons": buttons
            }
        })
    else:
        outputs.append({"simpleText": {"text": display_text}})

    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": [
                {"action": "message", "label": "🚌 190번 버스", "messageText": "190번 버스 어디야?"},
                {"action": "message", "label": "🍱 오늘 학식", "messageText": "오늘 학식 뭐야?"}
            ]
        }
    }

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        utterance = payload.get('userRequest', {}).get('utterance', '')
        user_id = payload.get('userRequest', {}).get('user', {}).get('id', 'unknown')
        
        # 파라미터 기반 AI 사용 여부 결정 (기본값 True)
        params = payload.get('action', {}).get('params', {})
        use_ai = params.get('use_ai_engine', 'true').lower() in ['true', 't', '1']

        if use_ai:
            # 비동기 호출 시 반드시 await 사용
            response_text = await ask_ara(utterance, user_id)
        else:
            response_text = "현재 AI 엔진이 비활성화 상태입니다."

        return build_kakao_response(response_text)
    except Exception as e:
        print(f"🚨 Server Error: {e}")
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "아라가 잠시 응답하기 어려워요. 🌊"}}]}}

if __name__ == "__main__":
    # Render 환경의 PORT 변수를 우선 사용
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)