import sys
import os
import logging
import json
import traceback

# [Render 배포용] SQLite 패치
try:
    __import__('pysqlite3')  # pyright: ignore[reportMissingImports]
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from agent import process_query  # agent.py에 이 함수가 있다고 가정합니다.

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARA_Main")

app = FastAPI()

@app.get("/")
def health_check():
    return {"status": "ok", "message": "ARA Server is running"}

# ==========================================
# 카카오톡 연동 메인 엔드포인트
# ==========================================

def _extract_user_utterance(payload: dict) -> str:
    """
    카카오톡 요청 payload에서 사용자 발화를 추출합니다.
    여러 가능한 경로를 시도합니다.
    """
    # 경로 1: userRequest.utterance (일반적인 경우)
    utterance = payload.get("userRequest", {}).get("utterance")
    if utterance:
        return utterance
    
    # 경로 2: userRequest.message.text (메시지 형식)
    utterance = payload.get("userRequest", {}).get("message", {}).get("text")
    if utterance:
        return utterance
    
    # 경로 3: action.params (퀵 플라이 버튼 클릭 시)
    utterance = payload.get("action", {}).get("params", {}).get("utterance")
    if utterance:
        return utterance
    
    # 경로 4: action.params.messageText (퀵 플라이 버튼)
    utterance = payload.get("action", {}).get("params", {}).get("messageText")
    if utterance:
        return utterance
    
    # 경로 5: 직접 utterance 필드
    utterance = payload.get("utterance")
    if utterance:
        return utterance
    
    # 기본값
    return "안녕하세요"

@app.post("/message")
async def message(request: Request):
    """
    카카오톡 스킬 서버 메인 엔드포인트 (표준)
    """
    try:
        # 1. 요청 파싱
        payload = await request.json()
        
        # 디버깅: 요청 payload 전체 로깅 (처음 500자만)
        payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.info(f"📥 [카톡 요청] Payload (처음 500자):\n{payload_str[:500]}")
        
        # 2. 사용자 발화 추출 (여러 경로 시도)
        user_utterance = _extract_user_utterance(payload)
        logger.info(f"📥 [카톡 요청] 추출된 사용자 발화: {user_utterance}")
        
        # 3. 사용자 ID 추출 (있는 경우)
        user_id = None
        user_info = payload.get("userRequest", {}).get("user", {})
        if user_info:
            user_id = user_info.get("id")
        
        # 4. 에이전트 로직 수행
        response = await process_query(user_utterance, user_id=user_id)
        
        # 5. 응답 검증
        if not isinstance(response, dict):
            logger.error(f"❌ [응답 형식 오류] response가 dict가 아님: {type(response)}")
            raise ValueError(f"process_query가 dict를 반환하지 않음: {type(response)}")
        
        if "version" not in response or "template" not in response:
            logger.error(f"❌ [응답 형식 오류] 필수 필드 누락: {list(response.keys())}")
            raise ValueError("카카오톡 응답 형식이 올바르지 않음")
        
        logger.info(f"📤 [서버 응답] 성공 - 데이터 타입: {type(response)}")
        return response

    except Exception as e:
        # 예외 처리: 상세 로깅 및 안전한 응답
        error_msg = traceback.format_exc()
        logger.error(f"❌ [치명적 오류]: {error_msg}")
        
        # 카카오톡이 이해할 수 있는 에러 메시지 포맷
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": f"🔧 [시스템 에러]\n서버 내부에서 오류가 발생했습니다.\n\n[원인]\n{str(e)}\n\n개발자에게 로그를 전달해주세요."
                        }
                    }
                ]
            }
        }

@app.post("/query")
async def query(request: Request):
    """
    카카오톡 오픈빌더 요청을 처리하는 메인 함수 (하위 호환성)
    /message로 리다이렉트
    """
    return await message(request)