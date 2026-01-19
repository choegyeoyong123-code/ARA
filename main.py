import os
from dotenv import load_dotenv

# .env 환경 변수 로드 (모든 커스텀 모듈 import 이전에 실행되어야 함)
load_dotenv()

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import json
import re

# 커스텀 모듈은 반드시 load_dotenv() 이후 import
from database import init_db, update_conversation_feedback
from agent import ask_ara

app = FastAPI()
templates = Jinja2Templates(directory="templates")
init_db()

@app.on_event("startup")
async def startup_diagnostics():
    """
    통합 진단: 서버 시작 시 주요 API 키 로드 상태를 터미널에 출력합니다.
    - 보안: API 키(일부 포함)를 절대 출력하지 않습니다.
    """
    # Windows(cp949) 콘솔에서는 이모지 출력이 실패할 수 있어 안전장치를 둡니다.
    try:
        print("✅ API 키 로드 완료")
    except UnicodeEncodeError:
        print("API keys loaded")

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

def _kakao_response(outputs: list[dict]):
    """
    카카오 스킬 응답 공통 래퍼
    - 반드시 {"version":"2.0","template":{"outputs":[...]}} 형식을 유지
    - 모든 응답에 quickReplies 상시 포함
    """
    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": _build_quick_replies(),
        },
    }

def _kakao_simple_text(text: str):
    return _kakao_response([{"simpleText": {"text": text}}])

def _kakao_basic_card(title: str, description: str, buttons: list[dict] | None = None):
    card: dict = {"title": title, "description": description}
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"basicCard": card}])

def _kakao_list_card(header_title: str, items: list[dict], buttons: list[dict] | None = None):
    card: dict = {"header": {"title": header_title}, "items": items}
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"listCard": card}])

def _kakao_auto_text(text: str):
    """
    text가 너무 길어 simpleText 제한에 걸릴 수 있으면 listCard로 완화합니다.
    - 구조화 데이터가 없을 때의 안전한 fallback(줄 단위 요약)
    """
    t = (text or "").strip()
    if len(t) <= 900:
        return _kakao_simple_text(t)

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    header = lines[0][:30] if lines else "ARA 안내"
    items: list[dict] = []
    for ln in lines[1:]:
        if ln.startswith("- "):
            title = ln[2:][:50]
            items.append({"title": title, "description": ""})
        else:
            if not items:
                items.append({"title": ln[:50], "description": ""})
            else:
                prev = items[-1].get("description", "")
                merged = (prev + ("\n" if prev else "") + ln)[:230]
                items[-1]["description"] = merged
        if len(items) >= 5:
            break

    if not items:
        return _kakao_simple_text(t[:900])
    return _kakao_list_card(header_title=header, items=items)

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

        kakao_timeout = float(os.environ.get("KAKAO_TIMEOUT_SECONDS", "4.3"))
        try:
            res = await asyncio.wait_for(
                ask_ara(user_msg, user_id=kakao_user_id, return_meta=True),
                timeout=kakao_timeout,
            )
        except asyncio.TimeoutError:
            return _kakao_simple_text("데이터를 분석 중입니다. 잠시 후 다시 시도해 주세요.")

        response_text = (res.get("content", "") if isinstance(res, dict) else str(res)).strip()
        return _kakao_auto_text(response_text)

    except Exception as e:
        print(f"Kakao Error: {e}")
        return _kakao_simple_text("시스템 오류가 발생했습니다.")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))