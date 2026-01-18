import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

    # [main.py 기존 import 문 아래에 추가]
# 카카오톡 요청 처리를 위한 Pydantic 모델은 굳이 정의 안 하고 dict로 처리해도 됩니다.

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        # 1. 카카오톡이 보낸 JSON 데이터 받기
        data = await request.json()
        
        # 2. 사용자 발화(질문) 추출
        # 카카오 스킬 페이로드 구조: userRequest -> utterance
        user_msg = data.get("userRequest", {}).get("utterance", "")
        
        if not user_msg:
            return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "말씀을 이해하지 못했어요."}}]}}

        # 3. ARA에게 질문 (Web 채팅과 동일한 로직 공유)
        # ask_ara는 텍스트를 반환하므로 바로 사용 가능
        response_text = await ask_ara(user_msg)
        
        # 4. 카카오톡 출력 형식(JSON)으로 변환
        kakao_response = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": response_text
                        }
                    }
                ]
            }
        }
        
        return kakao_response

    except Exception as e:
        print(f"Kakao Error: {e}")
        # 에러 발생 시 카카오톡에 에러 메시지 전송
        return {
            "version": "2.0", 
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "시스템 오류가 발생했습니다."
                        }
                    }
                ]
            }
        }