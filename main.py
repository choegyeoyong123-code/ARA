import os
import uvicorn
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

@app.on_event("startup")
def startup():
    init_db()

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    payload = await request.json()
    u_id = payload['userRequest']['user']['id']
    text = payload['userRequest']['utterance']
    
    # 비동기 호출 시 await 누락 주의
    response = await ask_ara(text, u_id)
    return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": response}}]}}

if __name__ == "__main__":
    # Render의 PORT 환경변수 우선 적용
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)