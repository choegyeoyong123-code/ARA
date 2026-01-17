import os
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

# 1. 서버 시작 시 DB 초기화 및 테이블 생성
@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        utterance = payload.get('userRequest', {}).get('utterance', '')
        user_id = payload.get('userRequest', {}).get('user', {}).get('id', 'unknown')

        # [비동기 처리] 반드시 await를 붙여 응답을 기다립니다
        response_text = await ask_ara(utterance, user_id)

        return {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": response_text}}]
            }
        }
    except Exception as e:
        print(f"🚨 서버 에러: {e}")
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "잠시 후 다시 시도해주세요! 🌊"}}]}}

if __name__ == "__main__":
    # Render의 PORT 환경 변수를 읽어오고, 없으면 10000번을 사용합니다
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)