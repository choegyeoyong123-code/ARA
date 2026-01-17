import os
import uvicorn
import re
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

@app.on_event("startup")
def startup_event():
    # 서버 시작 시 DB 테이블이 없으면 생성합니다.
    init_db()

def build_kakao_response(text):
    """카카오톡 UI 빌더: 링크 존재 시 버튼 카드로 자동 변환"""
    url_pattern = r'(https?://\S+)'
    urls = re.findall(url_pattern, text)
    
    outputs = []
    if urls:
        outputs.append({
            "basicCard": {
                "title": "🐬 아라의 안내",
                "description": re.sub(url_pattern, '', text).strip()[:400],
                "buttons": [{"action": "webLink", "label": "상세 보기/지도 🔗", "webLinkUrl": urls[0]}]
            }
        })
    else:
        outputs.append({"simpleText": {"text": text[:400]}})

    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": [{"action": "message", "label": "🚌 190번 정보", "messageText": "190번 버스 어디야?"}]
        }
    }

@app.post("/kakao")
async def handle_kakao(request: Request):
    try:
        body = await request.json()
        utterance = body.get('userRequest', {}).get('utterance', '')
        user_id = body.get('userRequest', {}).get('user', {}).get('id', 'unknown')

        # [핵심] 반드시 await를 붙여 비동기 처리를 완료해야 합니다.
        response_text = await ask_ara(utterance, user_id)
        
        return build_kakao_response(response_text)
    except Exception as e:
        print(f"🚨 Server Error: {e}")
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잠시 후 다시 시도해주세요! 🌊"}}]}}

if __name__ == "__main__":
    # Render는 PORT 환경 변수를 사용합니다.
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)

    # ... (기존 포트 설정 및 비동기 처리 로직 동일)
@app.post("/kakao")
async def handle_kakao(request: Request):
    payload = await request.json()
    # 카카오톡 고유 user.id를 Admin 기능에 활용하기 위해 전달합니다.
    user_id = payload['userRequest']['user']['id'] 
    utterance = payload['userRequest']['utterance']
    
    answer = await ask_ara(utterance, user_id)
    return build_kakao_response(answer)