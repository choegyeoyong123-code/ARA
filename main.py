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
        from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

# 서버 시작 시 데이터베이스 초기화
@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    payload = await request.json()
    utterance = payload['userRequest']['utterance']
    user_id = payload['userRequest']['user']['id']

    # [수정] 비동기 함수이므로 await를 붙여줍니다.
    response_text = await ask_ara(utterance, user_id)

    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": response_text}}]}
    }
    from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

# 서버 시작 시 DB 초기화 (기억력 세팅)
@app.on_event("startup")
def startup_event():
    init_db()

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    try:
        payload = await request.json()
        utterance = payload['userRequest']['utterance']
        user_id = payload['userRequest']['user']['id']

        # 아라에게 질문 (동기 방식 유지하되 예외 처리 포함)
        response_text = ask_ara(utterance, user_id)

        return {
            "version": "2.0",
            "template": {"outputs": [{"simpleText": {"text": response_text}}]}
        }
    except:
        return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "아라가 응답하는 중 오류가 났어요!"}}]}}

        # main.py
import re
from fastapi import FastAPI, Request
from agent import ask_ara
from database import init_db

app = FastAPI()

@app.on_event("startup")
def startup_event():
    init_db()

def build_kakao_response(text):
    """텍스트를 분석하여 적절한 카카오톡 UI를 생성합니다."""
    # 1. 지도/웹 링크 추출 (정규식)
    url_pattern = r'(https?://\S+)'
    urls = re.findall(url_pattern, text)
    clean_text = re.sub(url_pattern, '', text).strip() # 링크 제거된 본문

    # 2. 결과가 여러 개이고 링크가 있는 경우 (예: 맛집 추천) -> 캐러셀 구성 가능
    # 여기서는 가장 범용적인 'BasicCard' 구조로 업그레이드합니다.
    outputs = []
    if urls:
        buttons = []
        for i, url in enumerate(urls[:3]): # 버튼은 최대 3개
            label = "지도 보기 🔗" if "search.naver" in url else "자세히 보기 🔗"
            buttons.append({"action": "webLink", "label": label, "webLinkUrl": url})
        
        outputs.append({
            "basicCard": {
                "description": clean_text,
                "buttons": buttons
            }
        })
    else:
        # 일반 텍스트 응답
        outputs.append({"simpleText": {"text": text}})

    # 3. 하단 퀵 리플라이 (고정 메뉴)
    quick_replies = [
        {"action": "message", "label": "실시간 버스 🚌", "messageText": "학교 버스 알려줘"},
        {"action": "message", "label": "교내 셔틀 🚐", "messageText": "셔틀버스 언제 와?"},
        {"action": "message", "label": "오늘 학식 🍱", "messageText": "오늘 학식 뭐야?"}
    ]

    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": quick_replies
        }
    }

@app.post("/kakao")
async def kakao_endpoint(request: Request):
    payload = await request.json()
    utterance = payload['userRequest']['utterance']
    user_id = payload['userRequest']['user']['id']

    # AI 엔진 호출 (Temperature=0 및 틈새 전략 적용됨)
    response_text = await ask_ara(utterance, user_id)

    return build_kakao_response(response_text)