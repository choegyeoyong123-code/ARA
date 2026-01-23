import os
import sys
import logging
import traceback
import json
import asyncio
from typing import Any, Optional, Dict
from dotenv import load_dotenv
from openai import AsyncOpenAI

# ==========================================
# SQLite 패치 (Render 배포용)
# ==========================================
try:
    __import__('pysqlite3')  # pyright: ignore[reportMissingImports]
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

# 환경 설정 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARA_Agent")

# OpenAI Client 전역 초기화
api_key = os.getenv("OPENAI_API_KEY")
client = None

if api_key:
    client = AsyncOpenAI(api_key=api_key)
    logger.info("✅ OpenAI Client(Async) 초기화 완료")
else:
    logger.error("❌ OPENAI_API_KEY가 환경 변수에 없습니다!")

# Tools 및 RAG 임포트
from tools import (
    TOOLS_SPEC,
    get_bus_arrival,
    get_bus_190_tracker_busbusinfo,
    get_cheap_eats,
    get_kmou_weather,
    get_weather_info,
    get_shuttle_next_buses,
    search_restaurants,
    get_youth_center_info,
    get_calendar_day_2026,
    get_astronomy_data,
    get_campus_contacts,
    get_academic_schedule,
)
from rag import get_university_context

# Tool 매핑
TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_bus_190_tracker_busbusinfo": get_bus_190_tracker_busbusinfo,
    "get_cheap_eats": get_cheap_eats,
    "get_kmou_weather": get_kmou_weather,
    "get_weather_info": get_weather_info,
    "get_shuttle_next_buses": get_shuttle_next_buses,
    "search_restaurants": search_restaurants,
    "get_youth_center_info": get_youth_center_info,
    "get_calendar_day_2026": get_calendar_day_2026,
    "get_astronomy_data": get_astronomy_data,
    "get_campus_contacts": get_campus_contacts,
    "get_academic_schedule": get_academic_schedule,
}

# 면책 조항 텍스트 (가독성을 위해 축소)
DISCLAIMER_TEXT = (
    "\n\n─\n"
    "⚠️ [면책 고지] 본 답변은 AI가 실시간으로 수집·요약한 정보로 부정확할 수 있습니다. "
    "중요 사항은 학교 공식 홈페이지를 교차 확인하시기 바랍니다."
)

# ==========================================
# 핵심 LLM 호출 함수 (RAG + Function Calling)
# ==========================================
async def ask_ara(
    user_input: str,
    user_id: Optional[str] = None,
    return_meta: bool = False,
    session_lang: str = "ko"
) -> str:
    """
    한국해양대학교 전용 AI 비서 ARA의 핵심 함수
    - RAG: 학교 데이터베이스 검색
    - Function Calling: 외부 API 호출 (버스, 날씨, 학식 등)
    """
    if client is None:
        return "죄송합니다. 현재 AI 서버 연결 설정 문제로 답변할 수 없습니다."

    try:
        # 1. RAG: 학교 데이터베이스에서 관련 컨텍스트 검색
        university_context = await get_university_context(user_input, top_k=5)
        
        # 2. System 메시지 구성
        system_prompt = (
            "당신은 국립한국해양대학교(KMOU)의 지능형 학사 도우미 'ARA'입니다.\n\n"
            "**핵심 역할:**\n"
            "- 학생들의 학사 일정, 장학금, 규정 등 모든 학교 관련 질문에 정확하게 답변\n"
            "- 실시간 정보(버스, 날씨, 학식)는 반드시 제공된 함수를 사용하여 조회\n"
            "- 친절하고 명확하게 답변하며, 모르는 것은 추측하지 말고 '확인 중'이라고 말하기\n\n"
            "**답변 원칙:**\n"
            "- 한국해양대학교 관련 질문은 아래 [학교 데이터]를 우선 참고\n"
            "- 실시간 정보(버스, 날씨, 셔틀, 학식)는 함수를 호출하여 조회\n"
            "- 답변은 500자 이내로 간결하게 작성\n"
        )
        
        if university_context:
            system_prompt += f"\n[학교 데이터]\n{university_context}\n"
        
        # 3. 메시지 구성
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
        
        # 4. Function Calling을 포함한 첫 번째 LLM 호출
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS_SPEC if TOOLS_SPEC else None,
            tool_choice="auto",
            temperature=0.7,
            max_tokens=1000
        )
        
        message = response.choices[0].message
        messages.append(message)
        
        # 5. Tool 호출 처리
        tool_calls = message.tool_calls
        if tool_calls:
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                logger.info(f"🔧 [Tool Call] {function_name}({function_args})")
                
                # Tool 실행
                if function_name in TOOL_MAP:
                    tool_func = TOOL_MAP[function_name]
                    try:
                        # 비동기 함수인지 확인
                        if asyncio.iscoroutinefunction(tool_func):
                            tool_result = await tool_func(**function_args)
                        else:
                            tool_result = tool_func(**function_args)
                        
                        # 결과를 문자열로 변환
                        if isinstance(tool_result, str):
                            result_str = tool_result
                        else:
                            result_str = json.dumps(tool_result, ensure_ascii=False)
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_str
                        })
                    except Exception as e:
                        logger.error(f"❌ [Tool Error] {function_name}: {e}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"오류 발생: {str(e)}"
                        })
                else:
                    logger.warning(f"⚠️ [Tool Not Found] {function_name}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": "해당 기능을 찾을 수 없습니다."
                    })
            
            # 6. Tool 결과를 바탕으로 최종 답변 생성
            final_response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            final_content = final_response.choices[0].message.content
        else:
            # Tool 호출이 없으면 첫 번째 응답 사용
            final_content = message.content
        
        return final_content if final_content else "죄송합니다. 답변을 생성할 수 없습니다."
        
    except Exception as e:
        logger.error(f"❌ [ask_ara] 오류 발생: {e}")
        logger.error(traceback.format_exc())
        return "죄송합니다. 처리 중 오류가 발생했습니다."

# ==========================================
# 카카오톡 연동 메인 함수
# ==========================================
async def process_query(user_utterance: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    사용자 발화를 받아 AI 답변을 생성하고,
    카카오톡 JSON 포맷으로 반환합니다.
    """
    try:
        logger.info(f"🤖 [Agent] 질문 수신: {user_utterance}")
        
        # AI 답변 생성
        answer_text = await ask_ara(
            user_input=user_utterance,
            user_id=user_id,
            return_meta=False,
            session_lang="ko"
        )
        
        # 면책 조항 추가
        final_answer = answer_text + DISCLAIMER_TEXT
        
        # 퀵 리플라이 버튼 생성
        quick_replies = [
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
        
        # 카카오톡 JSON 응답 생성
        response_payload = {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": final_answer
                        }
                    }
                ],
                "quickReplies": quick_replies
            }
        }
        
        logger.info("✅ [Agent] 응답 생성 완료")
        return response_payload
        
    except Exception as e:
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
