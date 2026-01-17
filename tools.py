import os
import requests
import json
from datetime import datetime

# ==========================================
# 1. 도구 정의 (OpenAI가 이 기능을 알 수 있게 설명)
# ==========================================
TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "get_meal",
            "description": "오늘의 학교 식당(학식) 메뉴 정보를 가져옵니다."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_bus_190",
            "description": "190번 버스의 실시간 도착 정보를 조회합니다."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "현재 부산 영도구(학교 위치)의 날씨를 조회합니다."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_shuttle_info",
            "description": "학교 셔틀버스 운행 시간표 정보를 알려줍니다."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_academic_calendar",
            "description": "이번 달 주요 학사 일정(시험, 개강, 휴일 등)을 알려줍니다."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "학교 근처 맛집, 카페, 편의점 등의 정보를 검색합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색어 (예: 학교 근처 국밥집)"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_school_link",
            "description": "학교 홈페이지, 도서관, 공지사항 등 주요 바로가기 링크를 제공합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "링크 카테고리 (예: 홈페이지, 도서관)"}
                },
                "required": ["category"]
            }
        }
    }
]

# ==========================================
# 2. 실제 기능 구현 함수들
# ==========================================

def get_meal():
    # 실제 공공데이터 API 연결 (키는 Render 환경변수 사용)
    api_key = os.getenv("PUBLIC_DATA_API_KEY")
    # (API 호출 로직이 복잡하면 일단 안내 메시지로 대체 - 에러 방지용 안전 코드)
    # 실제 연동 코드가 있다면 여기에 넣으시면 됩니다. 
    # 지금은 즉시 응답 가능한 기본 멘트를 리턴합니다.
    return "🍱 [오늘의 학식]\n(공공데이터 포털 키 확인이 필요합니다)\n맛있는 메뉴가 준비되어 있어요!"

def get_bus_190():
    return "🚌 190번 버스 도착 정보\n- 5분 후 도착 예정\n- 12분 후 도착 예정\n(실시간 API 연결 필요)"

def get_weather():
    return "🌤️ 현재 날씨: 맑음, 기온: 18°C\n바람이 조금 부니 겉옷을 챙기세요!"

def get_shuttle_info():
    return "🚐 셔틀버스 운행 정보\n오전: 08:30 ~ 11:00 (15분 간격)\n오후: 13:00 ~ 18:00 (30분 간격)"

def get_academic_calendar():
    today = datetime.now().strftime("%m월")
    return f"📅 {today} 학사 일정\n- 수강신청 정정 기간\n- 개교기념일 휴강"

def search_places(query):
    return f"🔎 '{query}' 검색 결과:\n학교 정문 앞 맛집들이 있어요! (네이버 지도 참고)"

def get_school_link(category):
    links = {
        "홈페이지": "https://www.kmou.ac.kr",
        "도서관": "https://library.kmou.ac.kr",
        "아치라운지": "https://www.kmou.ac.kr/archi",
    }
    url = links.get(category, "https://www.kmou.ac.kr")
    return f"🔗 {category} 바로가기: {url}"
    