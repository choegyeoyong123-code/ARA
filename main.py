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
async def kakao(request: Request):
    try:
        body = await request.json()
        u_id = body['userRequest']['user']['id']
        query = body['userRequest']['utterance']
        res = await ask_ara(query, u_id)
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": res}}]}}
    except:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "아라가 잠시 응답하기 어렵습니다. 🌊"}}]}}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)