import os
import httpx
import json
from datetime import datetime

# API 키 및 검색 엔진 ID 로드
GCS_KEY = os.getenv("GOOGLE_SEARCH_KEY")
GCS_CX = os.getenv("GOOGLE_SEARCH_CX")
PUBLIC_KEY = os.getenv("PUBLIC_DATA_API_KEY")

async def get_weather_real():
    """기상청 API 기반 영도 캠퍼스 실시간 날씨"""
    if not PUBLIC_KEY: return "🌡️ 날씨 API 키가 설정되지 않았습니다."
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    params = {
        "serviceKey": PUBLIC_KEY, "dataType": "JSON", "numOfRows": "10", "pageNo": "1",
        "base_date": datetime.now().strftime("%Y%m%d"),
        "base_time": datetime.now().strftime("%H00"), "nx": "98", "ny": "74"
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json()['response']['body']['items']['item']
        temp = next(i['obsrValue'] for i in items if i['category'] == 'T1H')
        return f"🌡️ 현재 영도 캠퍼스 기온은 {temp}°C입니다."
    except: return "🌊 현재 날씨 정보를 가져올 수 없습니다."

async def search_kmou_web(query: str):
    """Google Search API를 통한 우회 크롤링"""
    if not GCS_KEY or not GCS_CX: return "🚨 구글 검색 설정이 미비합니다."
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GCS_KEY, "cx": GCS_CX, "q": f"site:kmou.ac.kr {query}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json().get('items', [])
        if not items: return "📍 검색 결과가 없습니다."
        return f"🌐 [검색 결과] {items[0]['title']}\n🔗 {items[0]['link']}"
    except: return "⚠️ 웹 검색 중 오류가 발생했습니다."

# GPT 도구 정의 스펙
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_weather_real", "description": "해양대 날씨를 확인합니다."}},
    {"type": "function", "function": {"name": "search_kmou_web", "description": "학교 공지사항을 검색합니다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}
]