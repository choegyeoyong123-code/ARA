import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from agent import ask_ara
from database import init_db, update_conversation_feedback
import json
import re

app = FastAPI()
templates = Jinja2Templates(directory="templates")
init_db()

NAV_QUICK_REPLIES = [
    {"label": "🚌 190번(학교행)", "action": "message", "messageText": "190번 버스 IN"},
    {"label": "🚌 190번(역/대교행)", "action": "message", "messageText": "190번 버스 OUT"},
    {"label": "🌤️ 해양대 날씨", "action": "message", "messageText": "지금 학교 날씨 어때?"},
    {"label": "🍚 가성비 맛집", "action": "message", "messageText": "영도 착한가격 식당 추천해줘"},
    {"label": "🏥 약국/병원", "action": "message", "messageText": "학교 근처 약국이나 병원 알려줘"},
    {"label": "🎉 축제/행사", "action": "message", "messageText": "지금 부산에 하는 축제 있어?"},
]

def _build_quick_replies():
    """
    카카오 quickReplies는 모든 응답 하단에 상시 노출합니다.
    - 요구된 6개 네비게이션만 "항상" 포함(상시 메뉴)
    """
    return list(NAV_QUICK_REPLIES)

def _kakao_simple_text(text: str):
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": _build_quick_replies(),
        },
    }

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    user_msg = data.get("message")
    user_id = data.get("user_id")  # 선택: 프론트에서 전달 가능
    
    async def event_generator():
        res = await ask_ara(user_msg, user_id=user_id, return_meta=True)
        yield f"data: {json.dumps(res, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/feedback")
async def feedback_endpoint(request: Request):
    """
    대화 ID(conversation_id)에 대해 사용자 피드백을 기록합니다.
    payload 예시:
    {
      "conversation_id": "...",
      "user_feedback": 1,   # 1 또는 -1
      "is_gold_standard": false
    }
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "msg": "요청 JSON을 파싱할 수 없습니다."}

    conversation_id = (data.get("conversation_id") or "").strip()
    user_feedback = data.get("user_feedback")
    is_gold_standard = data.get("is_gold_standard", None)

    if not conversation_id:
        return {"ok": False, "msg": "conversation_id가 필요합니다."}
    if user_feedback not in (1, -1, 0):
        return {"ok": False, "msg": "user_feedback은 1(좋아요), -1(싫어요), 0(중립)만 허용합니다."}
    if is_gold_standard is not None and not isinstance(is_gold_standard, bool):
        return {"ok": False, "msg": "is_gold_standard는 boolean이어야 합니다."}

    changed = update_conversation_feedback(conversation_id, int(user_feedback), is_gold_standard=is_gold_standard)
    if not changed:
        return {"ok": False, "msg": "해당 conversation_id를 찾을 수 없습니다."}
    return {"ok": True}

@app.post("/query")
async def kakao_endpoint(request: Request):
    try:
        try:
            data = await request.json()
        except Exception:
            return _kakao_simple_text("요청 형식을 확인할 수 없습니다.")
        
        user_request = data.get("userRequest", {}) or {}
        user_msg = user_request.get("utterance") or ""
        kakao_user_id = ((user_request.get("user") or {}) or {}).get("id")
        
        if not user_msg:
            return _kakao_simple_text("말씀을 이해하지 못했습니다. 다시 한 번 입력해 주세요.")

        # 카카오에서 quickReplies로 돌아오는 피드백 발화 처리(선택 기능)
        # 예: "feedback:+1:<conversation_id>" 또는 "feedback:-1:<conversation_id>"
        m = re.match(r"^feedback:(?P<score>[+-]1):(?P<cid>[0-9a-fA-F-]{16,})$", user_msg.strip())
        if m:
            score = int(m.group("score"))
            cid = m.group("cid")
            ok = update_conversation_feedback(cid, score)
            return _kakao_simple_text("피드백이 반영되었습니다. 감사합니다." if ok else "피드백 대상을 찾지 못했습니다.")

        res = await ask_ara(user_msg, user_id=kakao_user_id, return_meta=True)
        response_text = res.get("content", "")

        return _kakao_simple_text(response_text)

    except Exception as e:
        print(f"Kakao Error: {e}")
        return _kakao_simple_text("시스템 오류가 발생했습니다.")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))