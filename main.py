import os
import re
from fastapi import FastAPI, Request
from agent import ask_ara 

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Ara is Running!"}

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        user_request = payload.get('userRequest', {})
        utterance = user_request.get('utterance', '')
        user_id = user_request.get('user', {}).get('id', 'test_user')

        # GPT 응답 받기
        response_text = ask_ara(utterance, user_id)

        # URL 링크 버튼 생성 로직
        url_match = re.search(r'(https?://\S+)', response_text)
        outputs = []
        
        if url_match:
            outputs.append({
                "basicCard": {
                    "description": response_text,
                    "buttons": [{"action": "webLink", "label": "자세히 보기", "webLinkUrl": url_match.group(1)}]
                }
            })
        else:
            outputs.append({"simpleText": {"text": response_text}})

        return {
            "version": "2.0",
            "template": {"outputs": outputs}
        }

    except Exception as e:
        print(f"Server Error: {e}")
        return {
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": "오류가 발생했어. 다시 시도해줘!"}}]}
        }