import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from agent import ask_ara
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    user_msg = data.get("message")
    
    async def event_generator():
        response = await ask_ara(user_msg)
        yield f"data: {json.dumps({'content': response})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/query")
async def kakao_endpoint(request: Request):
    def _simple_text(text: str):
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text}}]}}

    try:
        try:
            data = await request.json()
        except Exception:
            return _simple_text("요청 형식을 확인할 수 없습니다.")
        
        user_msg = (data.get("userRequest", {}) or {}).get("utterance") or ""
        
        if not user_msg:
            return _simple_text("말씀을 이해하지 못했습니다. 다시 한 번 입력해 주세요.")

        response_text = await ask_ara(user_msg)
        
        return _simple_text(response_text)

    except Exception as e:
        print(f"Kakao Error: {e}")
        return _simple_text("시스템 오류가 발생했습니다.")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))