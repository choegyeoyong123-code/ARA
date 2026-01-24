import sys
import os
import logging
import json
import traceback
from typing import Optional

# ==========================================
# 1. [Render 배포용] SQLite 버전 패치
# (LangChain/ChromaDB 로드 전에 반드시 실행되어야 함)
# ==========================================
try:
    __import__('pysqlite3')  # pyright: ignore[reportMissingImports]
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

# 2. 환경 변수 로드
from dotenv import load_dotenv
load_dotenv()

# 3. FastAPI 및 Agent 임포트
from fastapi import FastAPI, Request
# agent.py에서 비동기 함수 process_query를 가져옵니다.
from agent import process_query 

# 4. 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ARA_Main")

app = FastAPI()

# ==========================================
# 헬스 체크 엔드포인트
# ==========================================
@app.get("/")
def health_check():
    return {"status": "ok", "message": "ARA Server is running"}

# ==========================================
# 퀵 리플라이 버튼 생성 함수
# ==========================================
def _nav_quick_replies() -> list:
    """
    카카오톡 퀵 리플라이 버튼 8개를 반환합니다.
    """
    return [
        {
            "label": "🚌 190번 출발 (구본관)",
            "action": "message",
            "messageText": "190 해양대구본관 출발"
        },
        {
            "label": "🍱 오늘 학식 메뉴",
            "action": "message",
            "messageText": "오늘 학식 메뉴 알려줘"
        },
        {
            "label": "🚐 셔틀버스 시간",
            "action": "message",
            "messageText": "셔틀 시간"
        },
        {
            "label": "🌤 영도 날씨",
            "action": "message",
            "messageText": "영도 날씨"
        },
        {
            "label": "📜 학사/장학 공지",
            "action": "message",
            "messageText": "최신 공지사항 알려줘"
        },
        {
            "label": "💼 취업/정책",
            "action": "message",
            "messageText": "취업"
        },
        {
            "label": "📞 캠퍼스 연락처",
            "action": "message",
            "messageText": "캠퍼스 연락처"
        },
        {
            "label": "🏫 학교 홈피",
            "action": "message",
            "messageText": "KMOU 홈페이지"
        }
    ]

# ==========================================
# 유틸리티 함수: 사용자 발화 추출
# ==========================================
def _extract_user_utterance(payload: dict) -> str:
    """
    카카오톡 요청 payload에서 사용자 발화를 추출합니다.
    """
    # 1. 일반 텍스트 (가장 흔한 케이스)
    utterance = payload.get("userRequest", {}).get("utterance")
    if utterance:
        return utterance
    
    # 2. 퀵 리플라이/버튼 클릭 시 (action.params)
    # 카카오톡 챗봇 관리자센터 설정에 따라 파라미터 위치가 다를 수 있음
    action_params = payload.get("action", {}).get("params", {})
    if "sys_text" in action_params:
        return action_params["sys_text"]
    
    if "utterance" in action_params:
        return action_params["utterance"]
    
    # 3. 폴백 블록의 원문
    # 사용자가 입력했으나 봇이 못 알아들은 경우
    user_msg = payload.get("userRequest", {}).get("message", {}).get("text")
    if user_msg:
        return user_msg

    return "내용 없음"

def _extract_image_url(payload: dict) -> Optional[str]:
    """
    카카오톡 요청 payload에서 이미지 URL을 추출합니다.
    
    로직:
    1. payload['userRequest']['params']['media']['url'] 경로에서 secureImage 확인
    2. 없으면 payload['userRequest']['utterance']가 "http"로 시작하고 "kakaocdn"을 포함하는지 확인
    3. 유효한 URL이 있으면 반환하고, 없으면 None 반환
    """
    # 1. 카카오톡 이미지 전송 표준 경로: userRequest.params.media.url
    try:
        params = payload.get("userRequest", {}).get("params", {})
        media = params.get("media", {})
        if isinstance(media, dict):
            url = media.get("url")
            if url and isinstance(url, str) and ("secureImage" in url or "kakaocdn" in url):
                return url
    except (KeyError, AttributeError, TypeError):
        pass
    
    # 2. 텍스트로 넘어오는 경우: utterance가 http로 시작하고 kakaocdn을 포함하는지 확인
    try:
        utterance = payload.get("userRequest", {}).get("utterance", "")
        if isinstance(utterance, str) and utterance.startswith("http") and "kakaocdn" in utterance:
            return utterance
    except (KeyError, AttributeError, TypeError):
        pass
    
    # 3. 추가 경로: userRequest.message.photo.url (하위 호환성)
    try:
        photo = payload.get("userRequest", {}).get("message", {}).get("photo")
        if photo and isinstance(photo, dict):
            url = photo.get("url")
            if url and isinstance(url, str):
                return url
    except (KeyError, AttributeError, TypeError):
        pass
    
    return None

# ==========================================
# 메인 메시지 처리 핸들러
# ==========================================
@app.post("/message")
async def message(request: Request):
    """
    카카오톡 스킬 서버 메인 엔드포인트
    """
    try:
        # 1. 요청 파싱 (비동기)
        payload = await request.json()
        
        # 로그: 요청 내용 일부 확인
        # logger.info(f"📥 [Payload]: {json.dumps(payload, ensure_ascii=False)[:200]}...")
        
        # 2. 사용자 발화 추출
        user_utterance = _extract_user_utterance(payload)
        logger.info(f"📥 [User Input] 발화: {user_utterance}")
        
        # 2-1. 이미지 URL 추출 (OCR 처리용)
        image_url = _extract_image_url(payload)
        if image_url:
            logger.info(f"📸 [Image Detected] URL: {image_url}")
            # 이미지가 있으면 사용자 발화를 이미지 전송 메시지로 변경 (OCR 처리는 agent가 수행)
            user_utterance = "사용자가 이미지를 보냈습니다."
        
        # 3. [핵심] Agent 로직 호출 (비동기 await 필수!)
        # agent.py의 process_query가 async def로 정의되었으므로 반드시 await를 써야 합니다.
        # 응답 시간 제한: 3.5초 (카카오톡 타임아웃 방지)
        import asyncio
        try:
            response = await asyncio.wait_for(
                process_query(user_utterance, image_url=image_url),
                timeout=3.5
            )
        except asyncio.TimeoutError:
            logger.error("❌ [Timeout] 응답 시간 초과 (3.5초)")
            response = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "simpleText": {
                                "text": "죄송해요. 응답 시간이 초과되었어요. 잠시 후 다시 시도해주세요. 😅"
                            }
                        }
                    ],
                    "quickReplies": _nav_quick_replies()
                }
            }
        
        # 4. 응답 검증 (Dict 타입 확인)
        if not isinstance(response, dict):
            logger.error(f"❌ [Error] 응답이 딕셔너리가 아님: {type(response)}")
            raise ValueError("Agent returned non-dict response")
        
        # 5. 필수 필드 검증 (카카오톡 규격)
        if "version" not in response or "template" not in response:
            logger.error(f"❌ [Error] 카카오톡 JSON 규격 불일치: {response.keys()}")
            raise ValueError("Invalid KakaoTalk JSON format")
        
        logger.info("📤 [Server Output] 정상 응답 반환")
        return response

    except Exception as e:
        # 예외 처리: 서버가 죽지 않고 에러 메시지를 카톡으로 반환
        error_msg = traceback.format_exc()
        logger.error(f"❌ [Critical Error]: {error_msg}")
        
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"🔧 [시스템 에러]\n서버 내부 처리 중 오류가 발생했습니다.\n\n{str(e)}\n(잠시 후 다시 시도해주세요)"
                        }
                    }
                ]
            }
        }

# ==========================================
# 하위 호환성 (Legacy) 엔드포인트
# ==========================================
@app.post("/query")
async def query(request: Request):
    """
    기존에 설정된 /query 경로로 들어오는 요청도 처리합니다.
    """
    return await message(request)