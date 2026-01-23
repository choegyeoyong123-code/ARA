import os
import sys
import logging
import traceback
import json
import asyncio
import datetime
import requests
import xml.etree.ElementTree as ET
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

# 외부 API 키 로드
GOOGLE_SEARCH_KEY = os.getenv("Google_Search_KEY") or os.getenv("GOOGLE_SEARCH_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")
YOUTH_CENTER_API_KEY = os.getenv("YOUTH_CENTER_API_KEY")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")

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

# ==========================================
# 외부 API 도구 함수 구현
# ==========================================

async def get_youth_policy(keyword: str) -> str:
    """
    온라인청년센터 API를 통해 청년 정책 조회
    """
    if not YOUTH_CENTER_API_KEY:
        return "청년 정책 API 키가 설정되지 않았어요. 😅"
    
    try:
        url = "https://www.youthcenter.go.kr/opi/empList.do"
        params = {
            "openApiVlak": YOUTH_CENTER_API_KEY,
            "pageIndex": 1,
            "display": 3,
            "query": keyword
        }
        
        # 비동기 처리를 위해 별도 스레드에서 실행
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        
        if response.status_code != 200:
            return f"청년 정책 API 요청 실패 (HTTP {response.status_code})"
        
        # XML 파싱
        try:
            root = ET.fromstring(response.text)
            results = []
            
            # XML에서 정책 정보 추출
            for item in root.findall(".//item")[:3]:
                title = item.find("polyBizSjnm")
                content = item.find("polyItcnCn")
                
                title_text = title.text if title is not None else "제목 없음"
                content_text = content.text if content is not None else "내용 없음"
                
                # 내용이 너무 길면 자르기
                if len(content_text) > 200:
                    content_text = content_text[:200] + "..."
                
                results.append(f"📋 {title_text}\n{content_text}")
            
            if results:
                return "\n\n---\n\n".join(results)
            else:
                return f"'{keyword}' 관련 청년 정책을 찾지 못했어요. 😅"
                
        except ET.ParseError as e:
            logger.error(f"XML 파싱 오류: {e}")
            return "청년 정책 정보를 파싱하는 중 오류가 발생했어요."
            
    except requests.exceptions.Timeout:
        return "청년 정책 API 요청 시간이 초과되었어요. 잠시 후 다시 시도해주세요."
    except Exception as e:
        logger.error(f"❌ [get_youth_policy] 오류: {e}")
        return f"청년 정책 조회 중 오류가 발생했어요: {str(e)}"

async def search_google(query: str) -> str:
    """
    Google Custom Search API를 통해 실시간/외부 정보 검색
    """
    if not GOOGLE_SEARCH_KEY or not GOOGLE_CX:
        return "Google 검색 API 키가 설정되지 않았어요. 😅"
    
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_SEARCH_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": 3
        }
        
        # 비동기 처리를 위해 별도 스레드에서 실행
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        
        if response.status_code != 200:
            return f"Google 검색 API 요청 실패 (HTTP {response.status_code})"
        
        data = response.json()
        items = data.get("items", [])
        
        if not items:
            return f"'{query}'에 대한 검색 결과를 찾지 못했어요. 😅"
        
        results = []
        for item in items[:3]:
            title = item.get("title", "제목 없음")
            snippet = item.get("snippet", "요약 없음")
            link = item.get("link", "")
            
            # 요약이 너무 길면 자르기
            if len(snippet) > 150:
                snippet = snippet[:150] + "..."
            
            result_text = f"🔍 {title}\n{snippet}"
            if link:
                result_text += f"\n🔗 {link}"
            
            results.append(result_text)
        
        return "\n\n---\n\n".join(results)
        
    except requests.exceptions.Timeout:
        return "Google 검색 API 요청 시간이 초과되었어요. 잠시 후 다시 시도해주세요."
    except Exception as e:
        logger.error(f"❌ [search_google] 오류: {e}")
        return f"Google 검색 중 오류가 발생했어요: {str(e)}"

async def ocr_image(image_url: str) -> str:
    """
    카카오 Vision API를 통해 이미지 내 텍스트 추출
    """
    if not KAKAO_REST_API_KEY:
        return None
    
    try:
        # 1. 이미지 다운로드
        img_response = await asyncio.to_thread(requests.get, image_url, timeout=10)
        if img_response.status_code != 200:
            logger.error(f"이미지 다운로드 실패: HTTP {img_response.status_code}")
            return None
        
        img_data = img_response.content
        
        # 2. 카카오 Vision API 호출
        ocr_url = "https://dapi.kakao.com/v2/vision/text/ocr"
        headers = {
            "Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"
        }
        files = {
            "image": img_data
        }
        
        ocr_response = await asyncio.to_thread(
            requests.post, ocr_url, headers=headers, files=files, timeout=10
        )
        
        if ocr_response.status_code != 200:
            logger.error(f"OCR API 호출 실패: HTTP {ocr_response.status_code}")
            return None
        
        data = ocr_response.json()
        result = data.get("result", {})
        recognition_words = result.get("recognition_words", [])
        
        if not recognition_words:
            return None
        
        # 인식된 단어들을 공백으로 연결
        ocr_text = " ".join(recognition_words)
        return ocr_text
        
    except requests.exceptions.Timeout:
        logger.error("OCR API 요청 시간 초과")
        return None
    except Exception as e:
        logger.error(f"❌ [ocr_image] 오류: {e}")
        return None

# ==========================================
# OpenAI Tool 스키마에 새 도구 추가
# ==========================================

NEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_youth_policy",
            "description": "💼 청년 정책 조회: 온라인청년센터 API를 통해 청년 정책 정보를 검색합니다. 취업, 창업, 주거, 교육 등 청년 정책 관련 질문에 사용하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "검색할 청년 정책 키워드 (예: 취업, 창업, 주거, 교육)"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_google",
            "description": "🔍 Google 검색: 실시간 정보나 외부 웹 정보를 검색합니다. 최신 뉴스, 일반 지식, 학교 홈페이지에 없는 정보를 찾을 때 사용하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 쿼리 (예: 한국해양대학교 최신 뉴스, 부산 날씨)"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# 모든 도구 통합
ALL_TOOLS = TOOLS_SPEC + [FILE_READ_TOOL] + NEW_TOOLS

# ==========================================
# 카카오톡 응답 포맷팅 유틸리티
# ==========================================
def format_for_kakaotalk(text: str) -> str:
    """
    카카오톡 플랫폼에 최적화된 텍스트 포맷팅
    - 긴 문단을 줄바꿈으로 구분
    - 리스트 항목을 이모지와 함께 표시
    - 가독성 향상
    """
    if not text:
        return text
    
    # 이미 포맷팅된 텍스트는 그대로 반환
    if "\n" in text or "•" in text or "✅" in text or "❌" in text:
        return text
    
    # 긴 문장을 적절히 줄바꿈
    # 문장 끝(마침표, 느낌표, 물음표) 뒤에 공백이 있으면 줄바꿈 고려
    lines = text.split(". ")
    if len(lines) > 3:
        # 여러 문장이 있으면 줄바꿈으로 구분
        formatted = ".\n\n".join(lines)
        if not formatted.endswith("."):
            formatted += "."
        return formatted
    
    return text

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
        return "죄송해요. 현재 AI 서버 연결에 문제가 있어 답변을 드릴 수 없어요. 😢"

    try:
        # 1. RAG: 학교 데이터베이스에서 관련 컨텍스트 검색
        university_context = None
        try:
            university_context = await get_university_context(user_input, top_k=5)
        except Exception as e:
            logger.warning(f"⚠️ RAG 검색 실패: {e}")
        
        # 2. [System Prompt 강화] 카카오톡 플랫폼 최적화 + 단계별 사고(CoT)
        system_instruction = (
            "당신은 국립한국해양대학교(KMOU)의 지능형 학사 도우미 'ARA'입니다. "
            "카카오톡에서 학생들과 대화하는 친근한 비서입니다.\n\n"
            
            "[카카오톡 플랫폼 특성]\n"
            "- 모바일 메신저 환경이므로 답변은 간결하고 읽기 쉬워야 합니다.\n"
            "- 이모지를 적절히 활용하여 시각적 효과를 높이세요 (예: 🚌 🍱 📅 ⚠️).\n"
            "- 긴 문단은 줄바꿈을 활용하여 가독성을 높이세요.\n"
            "- 중요한 정보는 강조 표시(예: **굵게**)를 사용하세요.\n\n"
            
            "[지시사항 - 내부 사고 과정]\n"
            "답변을 생성하기 전에 내부적으로 다음 단계를 거치세요:\n"
            "1. 질문 의도 파악: 사용자가 원하는 핵심 정보(장학금, 학식, 일정, 버스, 날씨 등)가 무엇인지 분석한다.\n"
            "2. 정보 소스 결정: 실시간 정보(버스, 날씨, 셔틀)는 함수를 호출하고, 학교 규정/일정은 RAG 데이터를 참고한다.\n"
            "3. 제약 조건 확인: 날짜, 대상, 자격 요건 등 세부 조건을 확인한다.\n"
            "4. 답변 구성: 가장 최신의 정확한 정보를 바탕으로 답변을 요약한다.\n"
            "5. 검증: 불확실한 내용은 추측하지 않고 '정보가 부족하다'고 솔직히 말한다.\n\n"
            
            "[출력 형식 - 카카오톡 최적화]\n"
            "- 위의 사고 과정은 절대 출력하지 마세요.\n"
            "- 답변은 친근하고 구어체로 작성하세요 (예: '~해요', '~입니다').\n"
            "- 답변 길이는 300-500자 이내로 간결하게 유지하세요.\n"
            "- 리스트나 여러 항목은 줄바꿈과 이모지로 구분하세요.\n"
            "- 예시:\n"
            "  ❌ '190번 버스는 10분 후 도착 예정입니다. 다음 버스는 25분 후입니다.'\n"
            "  ✅ '🚌 190번 버스 도착 정보\n\n"
            "  • 다음 버스: 약 10분 후\n"
            "  • 다다음 버스: 약 25분 후'\n\n"
            
            "[추가 원칙]\n"
            "- 한국해양대학교 관련 질문은 아래 [학교 데이터]를 우선 참고하세요.\n"
            "- 실시간 정보(버스, 날씨, 셔틀, 학식)는 반드시 제공된 함수를 사용하여 조회하세요.\n"
            "- 여러 함수를 조합하여 사용할 수 있습니다 (예: 날씨 + 학사일정).\n"
            "- 모르는 것은 추측하지 말고 '확인 중이에요'라고 솔직히 말하세요.\n"
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
            temperature=0.4,  # 카카오톡 친근한 톤을 위해 약간 상향
            max_tokens=800  # 카카오톡 메시지 길이 제한 고려
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
                    
                elif function_name == "get_youth_policy":
                    keyword = function_args.get("keyword", "")
                    tool_result = await get_youth_policy(keyword)
                    
                elif function_name == "search_google":
                    query = function_args.get("query", "")
                    tool_result = await search_google(query)
                    
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
                temperature=0.4,
                max_tokens=800
            )
            
            message = response.choices[0].message
            messages.append(message)
        
        # 6. 최종 답변 반환 (카카오톡 최적화)
        final_content = message.content
        if not final_content:
            final_content = "죄송해요. 답변을 생성할 수 없었어요. 😅"
        
        # 카카오톡 플랫폼에 맞게 포맷팅
        final_content = format_for_kakaotalk(final_content)
        
        # 카카오톡 메시지 길이 제한 고려 (최대 1000자)
        if len(final_content) > 1000:
            final_content = final_content[:997] + "..."
        
        return final_content
        
    except Exception as e:
        logger.error(f"❌ [ask_ara] 오류 발생: {e}")
        logger.error(traceback.format_exc())
        return "죄송해요. 처리 중 오류가 발생했어요. 잠시 후 다시 시도해주세요. 😅"

# ==========================================
# 카카오톡 연동 메인 함수
# ==========================================
async def process_query(
    user_utterance: str, 
    user_id: Optional[str] = None,
    image_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    사용자 발화를 받아 AI 답변을 생성하고,
    카카오톡 JSON 포맷으로 반환합니다.
    
    Args:
        user_utterance: 사용자 발화 텍스트
        user_id: 사용자 ID (선택)
        image_url: 이미지 URL (선택, OCR 처리용)
    """
    try:
        logger.info(f"🤖 [Agent] 질문 수신: {user_utterance}")
        
        # OCR 처리 (이미지 URL이 있는 경우)
        final_user_input = user_utterance
        if image_url:
            logger.info(f"📷 [OCR] 이미지 처리 시작: {image_url}")
            ocr_text = await ocr_image(image_url)
            if ocr_text:
                final_user_input = f"[이미지 내용]: {ocr_text}\n\n{user_utterance}"
                logger.info(f"✅ [OCR] 텍스트 추출 완료: {ocr_text[:50]}...")
            else:
                logger.warning("⚠️ [OCR] 텍스트 추출 실패")
        
        # AI 답변 생성
        answer_text = await ask_ara(
            user_input=final_user_input,
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
                            "text": "⚠️ 시스템 내부 오류가 발생했어요.\n\n잠시 후 다시 시도해주세요. 😅"
                        }
                    }
                ],
                "quickReplies": [
                    {
                        "label": "🔄 다시 시도",
                        "action": "message",
                        "messageText": "안녕하세요"
                    }
                ]
            }
        }
