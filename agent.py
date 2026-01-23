import os
import sys
import logging
import traceback
import json
import asyncio
import datetime
from pathlib import Path
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

# 데이터 경로 설정
current_dir = Path(__file__).parent.absolute()
data_dir = current_dir / "university_data"

# OpenAI Client 초기화
api_key = os.getenv("OPENAI_API_KEY")
client = None
if api_key:
    client = AsyncOpenAI(api_key=api_key)
    logger.info("✅ OpenAI Client 초기화 완료")
else:
    logger.error("❌ OPENAI_API_KEY Missing!")

# ==========================================
# Tools 및 RAG 임포트
# ==========================================
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

# ==========================================
# [Tool] 파일 직접 읽기 도구 (RAG 보조)
# ==========================================
def read_text_file(filename: str) -> str:
    """university_data 폴더 내의 특정 텍스트 파일을 읽어옵니다."""
    try:
        file_path = data_dir / f"{filename}.txt"
        if not file_path.exists():
            return "해당 정보에 대한 데이터 파일이 아직 수집되지 않았습니다."
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return "데이터 파일이 비어 있습니다."
            return content[:3000]  # 토큰 제한을 위해 앞부분 3000자만 리턴
    except Exception as e:
        logger.error(f"❌ 파일 읽기 오류: {e}")
        return f"파일 읽기 중 오류 발생: {str(e)}"

# 파일 읽기 도구를 TOOLS_SPEC에 추가
FILE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "get_university_info",
        "description": "학교 생활 정보(학식, 공지사항, 학사일정 등)를 조회합니다. RAG 검색으로 찾지 못한 경우 이 도구를 사용하세요.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["cafeteria_menu", "notice_general", "academic_guide", "scholarship_guide", "events_seminar"],
                    "description": "조회할 정보의 카테고리 (학식, 공지, 학사, 장학, 행사)"
                }
            },
            "required": ["category"]
        }
    }
}

# 모든 도구 통합
ALL_TOOLS = TOOLS_SPEC + [FILE_READ_TOOL]

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
    - Chain of Thought: 단계별 사고 과정
    """
    if client is None:
        return "죄송합니다. 현재 AI 서버 연결 설정 문제로 답변할 수 없습니다."

    try:
        # 1. RAG: 학교 데이터베이스에서 관련 컨텍스트 검색
        university_context = None
        try:
            university_context = await get_university_context(user_input, top_k=5)
        except Exception as e:
            logger.warning(f"⚠️ RAG 검색 실패: {e}")
        
        # 2. [System Prompt 강화] 단계별 사고(CoT) 도입
        system_instruction = (
            "당신은 국립한국해양대학교(KMOU)의 지능형 학사 도우미 'ARA'입니다. "
            "학생들에게 친절하고 명확하게 답변하세요.\n\n"
            
            "[지시사항] "
            "답변을 생성하기 전에 내부적으로 다음 단계를 거치세요:\n"
            "1. 질문 의도 파악: 사용자가 원하는 핵심 정보(장학금, 학식, 일정, 버스, 날씨 등)가 무엇인지 분석한다.\n"
            "2. 정보 소스 결정: 실시간 정보(버스, 날씨, 셔틀)는 함수를 호출하고, 학교 규정/일정은 RAG 데이터를 참고한다.\n"
            "3. 제약 조건 확인: 날짜, 대상, 자격 요건 등 세부 조건을 확인한다.\n"
            "4. 답변 구성: 가장 최신의 정확한 정보를 바탕으로 답변을 요약한다.\n"
            "5. 검증: 불확실한 내용은 추측하지 않고 '정보가 부족하다'고 솔직히 말한다.\n\n"
            
            "[출력 제한]\n"
            "- 위의 사고 과정은 절대 출력하지 마세요.\n"
            "- 학생에게 필요한 최종 결론만 친절한 구어체로 500자 이내로 요약하여 답변하세요.\n\n"
            
            "[추가 원칙]\n"
            "- 한국해양대학교 관련 질문은 아래 [학교 데이터]를 우선 참고하세요.\n"
            "- 실시간 정보(버스, 날씨, 셔틀, 학식)는 반드시 제공된 함수를 사용하여 조회하세요.\n"
            "- 여러 함수를 조합하여 사용할 수 있습니다 (예: 날씨 + 학사일정).\n"
            "- 모르는 것은 추측하지 말고 '확인 중'이라고 솔직히 말하세요.\n"
            "- 답변은 구체적이고 실용적으로 작성하세요.\n"
        )
        
        if university_context:
            system_instruction += f"\n[학교 데이터]\n{university_context}\n"
        
        # 3. 메시지 구성
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_input}
        ]
        
        # 4. Function Calling을 포함한 첫 번째 LLM 호출
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=ALL_TOOLS if ALL_TOOLS else None,
            tool_choice="auto",
            temperature=0.3,  # 논리적 정확성을 위해 온도 낮춤
            max_tokens=1500
        )
        
        message = response.choices[0].message
        messages.append(message)
        
        # 5. Tool 호출 처리 (여러 턴 지원)
        max_iterations = 3  # 최대 3번의 tool 호출 라운드
        iteration = 0
        
        while message.tool_calls and iteration < max_iterations:
            iteration += 1
            logger.info(f"🔄 [Tool Round {iteration}] {len(message.tool_calls)}개 도구 호출")
            
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                try:
                    function_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    function_args = {}
                
                logger.info(f"🔧 [Tool Call] {function_name}({function_args})")
                
                tool_result = ""
                
                # Tool 실행
                if function_name == "get_university_info":
                    category = function_args.get("category")
                    tool_result = read_text_file(category)
                    
                elif function_name in TOOL_MAP:
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
                        
                        tool_result = result_str
                    except Exception as e:
                        logger.error(f"❌ [Tool Error] {function_name}: {e}")
                        tool_result = f"오류 발생: {str(e)}"
                else:
                    logger.warning(f"⚠️ [Tool Not Found] {function_name}")
                    tool_result = "해당 기능을 찾을 수 없습니다."
                
                # 도구 결과를 메시지에 추가
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            
            # Tool 결과를 바탕으로 다음 응답 생성
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=ALL_TOOLS if ALL_TOOLS else None,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=1500
            )
            
            message = response.choices[0].message
            messages.append(message)
        
        # 6. 최종 답변 반환
        final_content = message.content
        if not final_content:
            final_content = "죄송합니다. 답변을 생성할 수 없습니다."
        
        return final_content
        
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
                            "text": str(answer_text)
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
