import os
import sys
import logging
import traceback
import json
from dotenv import load_dotenv
from openai import AsyncOpenAI  # 비동기 클라이언트 사용

# 1. 환경 설정 로드
load_dotenv()

# 2. 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARA_Agent")

# 3. [핵심 수정] OpenAI Client 전역 초기화
# 함수 밖에서 미리 선언해야 'UnboundLocalError'가 발생하지 않습니다.
api_key = os.getenv("OPENAI_API_KEY")
client = None

if api_key:
    # 비동기 처리를 위해 AsyncOpenAI 사용
    client = AsyncOpenAI(api_key=api_key)
    logger.info("✅ OpenAI Client(Async) 초기화 완료")
else:
    logger.error("❌ OPENAI_API_KEY가 환경 변수에 없습니다!")

# 4. 면책 조항 텍스트 정의
DISCLAIMER_TEXT = (
    "\n\n━━━━━━━━━━━━━━\n"
    "⚠️ [면책 고지]\n"
    "본 답변은 AI가 실시간으로 수집·요약한 정보로 부정확할 수 있습니다. "
    "중요한 학사 일정이나 장학금 정보는 반드시 학교 공식 홈페이지를 교차 확인하시기 바랍니다."
)

# ==========================================
# 핵심 LLM 호출 함수
# ==========================================
async def ask_ara(user_query: str) -> str:
    """
    OpenAI GPT에게 질문을 보내고 답변을 받습니다.
    (추후 RAG 로직을 여기에 통합하면 됩니다.)
    """
    # 전역 변수 client 사용 (global 키워드 없어도 읽기 가능)
    if client is None:
        logger.error("Client가 None 상태입니다.")
        return "죄송합니다. 현재 AI 서버 연결 설정 문제로 답변할 수 없습니다."

    try:
        # 비동기 호출 (await 필수)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",  # 비용 효율적인 모델 사용
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "당신은 국립한국해양대학교의 학사 도우미 'ARA'입니다. "
                        "학생들에게 친절하고 명확하게 답변하세요. "
                        "답변은 400자 이내로 요약해서 말하세요."
                    )
                },
                {"role": "user", "content": user_query}
            ],
            temperature=0.7,
            max_tokens=500
        )
        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"❌ GPT 호출 중 오류 발생: {e}")
        return "죄송합니다. AI가 답변을 생각하는 도중 오류가 발생했습니다."

# ==========================================
# 카카오톡 연동 메인 함수
# ==========================================
async def process_query(user_utterance: str) -> dict:
    """
    사용자 발화를 받아 AI 답변을 생성하고,
    카카오톡 JSON 포맷으로 반환합니다.
    """
    try:
        logger.info(f"🤖 [Agent] 질문 수신: {user_utterance}")

        # 1. AI 답변 생성 (비동기 대기)
        answer_text = await ask_ara(user_utterance)

        # 2. 면책 조항 부착 (String 결합)
        final_answer = answer_text + DISCLAIMER_TEXT

        # 3. 카카오톡 JSON 응답 생성
        response_payload = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": final_answer
                        }
                    }
                ]
            }
        }
        
        logger.info("✅ [Agent] 응답 생성 완료")
        return response_payload

    except Exception as e:
        # 치명적 오류 발생 시 로그 출력 및 안내 메시지
        logger.error(f"❌ [Agent] 처리 중 치명적 오류: {e}")
        logger.error(traceback.format_exc())

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": "⚠️ 시스템 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
                        }
                    }
                ]
            }
        }