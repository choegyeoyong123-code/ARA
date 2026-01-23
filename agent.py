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
            university_context = await get_university_context(user_input, top_k=7)  # 더 많은 컨텍스트 검색
        except Exception as e:
            logger.warning(f"⚠️ RAG 검색 실패: {e}")
        
        # 버스/셔틀 시간표 관련 질문이면 bus_schedule.txt 직접 참고
        bus_keywords = ["190", "버스", "셔틀", "시간표", "출발", "도착", "운행"]
        if any(kw in user_input for kw in bus_keywords):
            try:
                bus_schedule = read_text_file("bus_schedule")
                if bus_schedule and "해당 정보에 대한 데이터 파일이 아직 수집되지 않았습니다" not in bus_schedule:
                    if university_context:
                        university_context = f"{university_context}\n\n[버스/셔틀 시간표]\n{bus_schedule}"
                    else:
                        university_context = f"[버스/셔틀 시간표]\n{bus_schedule}"
            except Exception as e:
                logger.warning(f"⚠️ 버스 시간표 읽기 실패: {e}")
        
        # 2. [System Prompt 강화] 대기업 수준의 정확성과 전문성
        system_instruction = (
            "당신은 국립한국해양대학교(KMOU)의 엔터프라이즈급 지능형 학사 도우미 'ARA'입니다. "
            "대기업 수준의 정확성, 일관성, 전문성을 갖춘 AI 비서로서 학생들에게 최고 품질의 서비스를 제공합니다.\n\n"
            
            "=== 핵심 가치 ==="
            "1. **정확성 우선**: 모든 정보는 검증된 소스에서만 제공하며, 추측이나 환각을 절대 허용하지 않습니다.\n"
            "2. **일관성 보장**: 동일한 질문에 대해 항상 일관된 답변을 제공합니다.\n"
            "3. **전문성**: 한국해양대학교의 모든 분야에 대한 깊은 지식을 바탕으로 전문적인 조언을 제공합니다.\n"
            "4. **신뢰성**: 모든 정보는 공식 소스(RAG 데이터, 실시간 API, 학교 홈페이지)에서만 인용합니다.\n"
            "5. **사용자 경험**: 친절하고 명확하며 실용적인 답변으로 사용자의 시간을 절약합니다.\n\n"
            
            "=== 정보 소스 우선순위 ==="
            "1순위: 실시간 API 함수 (버스, 날씨, 셔틀, 학식 등) - 반드시 함수 호출로 최신 정보 제공\n"
            "2순위: RAG 데이터베이스 (학교 규정, 시간표, 상세 정보) - 아래 [학교 데이터] 참고\n"
            "3순위: 외부 검색 API (Google Search, 청년 정책) - 학교 데이터에 없는 정보만 사용\n"
            "4순위: 솔직한 인정 - 정보가 없으면 '확인 중이에요. 학교 홈페이지를 확인해주세요'라고 안내\n\n"
            
            "=== 버스 및 셔틀 시간표 정확성 ==="
            "- 190번 버스 시간표는 요일별로 정확히 다릅니다 (평일/토요일/일요일).\n"
            "  • 평일: 56회 운행 (04:55 ~ 21:50)\n"
            "  • 토요일: 53회 운행 (04:55 ~ 21:50)\n"
            "  • 일요일/공휴일: 48회 운행 (04:55 ~ 21:50)\n"
            "- 셔틀버스는 학기중/방학중 시간표가 다르며, 주말/공휴일 미운행입니다.\n"
            "- 시간표 질문 시 반드시 RAG 데이터의 bus_schedule.txt를 참고하여 정확한 시간을 제공하세요.\n"
            "- '약', '대략' 같은 표현을 최소화하고 가능한 한 정확한 시간을 제공하세요.\n\n"
            
            "=== 내부 사고 과정 (Chain of Thought) ==="
            "답변 생성 전 반드시 다음 7단계를 내부적으로 수행하세요:\n"
            "1. **질문 분석**: 사용자의 핵심 의도, 숨겨진 요구사항, 맥락 파악\n"
            "2. **정보 소스 매핑**: 어떤 함수를 호출할지, 어떤 RAG 데이터를 참고할지 결정\n"
            "3. **정확성 검증**: 제공할 정보의 출처와 최신성 확인\n"
            "4. **제약 조건 확인**: 날짜, 시간, 대상, 자격 요건 등 세부 사항 점검\n"
            "5. **답변 구조화**: 정보를 논리적 순서로 구성 (중요 정보 우선)\n"
            "6. **오류 방지**: 환각, 추측, 모순 없는지 최종 검증\n"
            "7. **사용자 경험 최적화**: 카카오톡 환경에 맞게 포맷팅\n\n"
            
            "=== 카카오톡 플랫폼 최적화 ==="
            "- 모바일 메신저 환경: 간결하고 스캔하기 쉬운 구조\n"
            "- 이모지 활용: 🚌 버스, 🍱 학식, 📅 일정, ⚠️ 주의, ✅ 확인, ❌ 불가 등\n"
            "- 줄바꿈: 긴 문단은 2-3줄마다 줄바꿈하여 가독성 향상\n"
            "- 리스트 포맷: • 또는 숫자로 구분하여 명확하게 표시\n"
            "- 길이 제한: 핵심 정보는 300-500자, 상세 정보는 800자 이내\n\n"
            
            "=== 답변 품질 기준 ==="
            "✅ 좋은 답변:\n"
            "  • 정확한 시간/날짜/번호 제공\n"
            "  • 출처 명시 (예: '학교 홈페이지 기준', '실시간 조회 결과')\n"
            "  • 실용적이고 실행 가능한 정보\n"
            "  • 친절하고 구어체 톤 ('~해요', '~입니다')\n"
            "  • 구조화된 포맷 (이모지 + 제목 + 내용)\n\n"
            "❌ 나쁜 답변:\n"
            "  • 추측이나 불확실한 정보\n"
            "  • 모호한 표현 ('대략', '아마도', '보통')\n"
            "  • 출처 없는 정보\n"
            "  • 과도하게 긴 문장\n"
            "  • 사용자에게 불필요한 기술적 세부사항\n\n"
            
            "=== 특수 상황 처리 ==="
            "- 버스 시간표 질문: RAG의 bus_schedule.txt에서 정확한 시간표 참고 후 답변\n"
            "- 셔틀 시간표 질문: 학기중/방학중 구분, 요일 확인 후 정확한 시간 제공\n"
            "- 날짜 관련 질문: 오늘 날짜를 기준으로 D-Day 계산, 요일 확인\n"
            "- 연락처 질문: RAG의 kmou_comprehensive_info.txt에서 정확한 번호 제공\n"
            "- 모르는 정보: 솔직히 인정하고 대안 제시 (홈페이지 확인, 관련 부서 연락 등)\n\n"
            
            "=== 최종 원칙 ==="
            "- 한국해양대학교 관련 모든 질문은 아래 [학교 데이터]를 최우선으로 참고하세요.\n"
            "- 실시간 정보는 반드시 제공된 함수를 호출하여 최신 데이터를 가져오세요.\n"
            "- 버스/셔틀 시간표는 RAG 데이터의 bus_schedule.txt에서 정확한 시간을 제공하세요.\n"
            "- 여러 함수를 조합하여 종합적인 답변을 제공할 수 있습니다.\n"
            "- 정보가 부족하거나 불확실하면 솔직히 말하고 대안을 제시하세요.\n"
            "- 모든 답변은 학생의 시간을 절약하고 실용적인 가치를 제공해야 합니다.\n"
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
            temperature=0.2,  # 정확성을 위해 낮은 온도 (대기업 수준의 일관성)
            max_tokens=1000  # 상세한 답변을 위한 토큰 증가
        )
        
        message = response.choices[0].message
        messages.append(message)
        
        # 5. Tool 호출 처리 (여러 턴 지원)
        max_iterations = 3  # 최대 3번의 tool 호출 라운드
        iteration = 0
        
        while message.tool_calls and iteration < max_iterations:
            iteration += 1
            logger.info(f"🔄 [Tool Round {iteration}] {len(message.tool_calls)}개 도구 호출")
            
            # 병렬 처리: 여러 Tool을 동시에 실행 (성능 최적화)
            async def execute_single_tool(tool_call):
                """단일 Tool 실행 함수"""
                function_name = tool_call.function.name
                try:
                    function_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    function_args = {}
                
                logger.info(f"🔧 [Tool Call] {function_name}({function_args})")
                
                try:
                    if function_name == "get_university_info":
                        category = function_args.get("category")
                        result = read_text_file(category)
                        return tool_call.id, result
                    
                    elif function_name == "get_youth_policy":
                        keyword = function_args.get("keyword", "")
                        result = await get_youth_policy(keyword)
                        return tool_call.id, result
                    
                    elif function_name == "search_google":
                        query = function_args.get("query", "")
                        result = await search_google(query)
                        return tool_call.id, result
                    
                    elif function_name in TOOL_MAP:
                        tool_func = TOOL_MAP[function_name]
                        if asyncio.iscoroutinefunction(tool_func):
                            result = await tool_func(**function_args)
                        else:
                            result = tool_func(**function_args)
                        
                        if isinstance(result, str):
                            return tool_call.id, result
                        else:
                            return tool_call.id, json.dumps(result, ensure_ascii=False)
                    else:
                        logger.warning(f"⚠️ [Tool Not Found] {function_name}")
                        return tool_call.id, "해당 기능을 찾을 수 없습니다."
                except Exception as e:
                    logger.error(f"❌ [Tool Error] {function_name}: {e}")
                    return tool_call.id, f"오류 발생: {str(e)}"
            
            # 모든 Tool을 병렬로 실행 (성능 최적화)
            tool_tasks = [execute_single_tool(tc) for tc in message.tool_calls]
            tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
            
            # 결과를 메시지에 추가
            for result in tool_results:
                if isinstance(result, Exception):
                    logger.error(f"❌ [Tool Task] 예외 발생: {result}")
                    continue
                
                tool_call_id, tool_result = result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(tool_result)
                })
            
            # Tool 결과를 바탕으로 다음 응답 생성
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=ALL_TOOLS if ALL_TOOLS else None,
                tool_choice="auto",
                temperature=0.2,  # 정확성을 위해 낮은 온도
                max_tokens=1000
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
        # 중요 정보 손실 방지를 위해 스마트 자르기
        if len(final_content) > 1000:
            # 마지막 완전한 문장까지 유지
            truncated = final_content[:997]
            last_period = truncated.rfind('.')
            last_newline = truncated.rfind('\n')
            cut_point = max(last_period, last_newline)
            if cut_point > 800:  # 너무 앞에서 자르지 않도록
                final_content = truncated[:cut_point + 1] + "\n\n(내용이 길어 일부만 표시됩니다. 더 자세한 정보는 학교 홈페이지를 확인해주세요.)"
            else:
                final_content = truncated + "..."
        
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
