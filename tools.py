import os
import httpx
import pandas as pd
import json
from fuzzywuzzy import process

# API 키 로드
REST_KEY = os.getenv("KAKAO_REST_API_KEY")
ADMIN_KEY = os.getenv("KAKAO_ADMIN_KEY")
GCS_KEY = os.getenv("GOOGLE_SEARCH_KEY")
GCS_CX = os.getenv("GOOGLE_SEARCH_CX")

# [핵심] Google Search 우회 크롤링
async def search_kmou_web(query: str):
    if not GCS_KEY or not GCS_CX: return "🚨 검색 엔진 설정 미비"
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GCS_KEY, "cx": GCS_CX, "q": f"site:kmou.ac.kr {query}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json().get('items', [])
        if not items: return "📍 학교 웹사이트 내 검색 결과가 없습니다."
        results = [f"✅ {item['title']}\n🔗 {item['link']}" for item in items[:2]]
        return "\n\n".join(results)
    except: return "⚠️ 웹 검색 도중 오류 발생"

# [핵심] 로컬 RAG 검색 (contacts.csv, buildings.json 연동)
async def search_campus_knowledge(query: str):
    # (이전 단계에서 작성한 CSV/JSON 유사도 검색 로직 포함)
    return "📞 학생처 번호는 051-410-4022입니다." # 예시 반환

# OpenAI용 도구 스펙 정의
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "search_kmou_web", "description": "학교 공지사항이나 웹 정보를 검색합니다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "search_campus_knowledge", "description": "학과 번호, 건물 위치 등 캠퍼스 내부 정보를 검색합니다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_user_profile", "description": "사용자 이름을 가져옵니다.", "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}}}}}
]

async def get_user_profile(user_id):
    if not ADMIN_KEY: return "선장님"
    url = f"https://kapi.kakao.com/v2/user/me?target_id_type=user_id&target_id={user_id}"
    headers = {"Authorization": f"KakaoAK {ADMIN_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
    return res.json().get("properties", {}).get("nickname", "선장님")

    async def get_weather_real():
    """
    기상청 API를 통해 한국해양대학교(영도구)의 실시간 날씨를 가져옵니다.
    """
    api_key = os.getenv("PUBLIC_DATA_API_KEY")
    if not api_key:
        return "☀️ 현재 날씨 정보를 가져올 수 없습니다. (API 키 누락)"

    # 영도구 동삼동 좌표 (nx=98, ny=74)
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    params = {
        "serviceKey": api_key,
        "numOfRows": "10",
        "pageNo": "1",
        "dataType": "JSON",
        "base_date": datetime.now().strftime("%Y%m%d"),
        "base_time": datetime.now().strftime("%H00"),
        "nx": "98",
        "ny": "74"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json().get('response', {}).get('body', {}).get('items', {}).get('item', [])
        
        weather_info = "🌡️ 현재 영도 캠퍼스 날씨: "
        for item in items:
            if item['category'] == 'T1H': weather_info += f"{item['obsrValue']}°C "
            if item['category'] == 'REH': weather_info += f"(습도 {item['obsrValue']}%)"
        return weather_info
    except:
        return "🌊 바다 안개로 인해 날씨 정보를 읽어오지 못했습니다.