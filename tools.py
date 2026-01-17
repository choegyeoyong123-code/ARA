import os
import httpx
import json
import pandas as pd
from datetime import datetime, timedelta
from fuzzywuzzy import process

# API 설정 값 로드
GCS_KEY = os.getenv("GOOGLE_SEARCH_KEY")
GCS_CX = os.getenv("GOOGLE_SEARCH_CX")
PUBLIC_KEY = os.getenv("PUBLIC_DATA_API_KEY")
ADMIN_KEY = os.getenv("KAKAO_ADMIN_KEY")
DATA_DIR = "data"

async def get_weather_real():
    """영도구 실시간 날씨 조회 (기상청 API)"""
    if not PUBLIC_KEY: return "🌡️ 날씨 API 키가 설정되지 않았습니다."
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    # 현재 시간에서 30분을 빼서 안정적인 데이터 수집
    base_time = (datetime.now() - timedelta(minutes=30)).strftime("%H00")
    params = {
        "serviceKey": PUBLIC_KEY, "dataType": "JSON", "numOfRows": "10", "pageNo": "1",
        "base_date": datetime.now().strftime("%Y%m%d"),
        "base_time": base_time, "nx": "98", "ny": "74"
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json()['response']['body']['items']['item']
        temp = next(i['obsrValue'] for i in items if i['category'] == 'T1H')
        return f"🌡️ 현재 영도 캠퍼스 기온은 {temp}°C입니다."
    except: return "🌊 현재 날씨 정보 서버에 접속할 수 없습니다."

async def search_kmou_web(query: str):
    """Google Custom Search를 통한 웹 검색"""
    if not GCS_KEY or not GCS_CX: return "🚨 구글 검색 설정(KEY/CX)을 확인해주세요."
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GCS_KEY, "cx": GCS_CX, "q": f"site:kmou.ac.kr {query}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        items = res.json().get('items', [])
        if not items: return "📍 학교 홈페이지에서 관련 정보를 찾지 못했습니다."
        results = [f"✅ {item['title']}\n🔗 {item['link']}" for item in items[:2]]
        return "\n\n".join(results)
    except: return "⚠️ 웹 검색 도중 오류가 발생했습니다."

async def search_campus_knowledge(query: str):
    """로컬 CSV/JSON 기반 캠퍼스 RAG 검색"""
    try:
        contacts_path = os.path.join(DATA_DIR, "contacts.csv")
        if os.path.exists(contacts_path):
            df = pd.read_csv(contacts_path)
            match = process.extractOne(query, df['name'].tolist(), score_cutoff=70)
            if match:
                row = df[df['name'] == match[0]].iloc[0]
                return f"📞 {row['name']} 번호는 {row['phone']}입니다. ({row['description']})"
        return "📍 로컬 데이터에 정보가 부족합니다. 웹 검색을 시도해 보세요."
    except: return "⚠️ 로컬 데이터를 읽는 중 오류가 발생했습니다."

async def get_user_profile(user_id: str):
    """사용자 닉네임 조회"""
    if not ADMIN_KEY: return "선장님"
    url = f"https://kapi.kakao.com/v2/user/me?target_id_type=user_id&target_id={user_id}"
    headers = {"Authorization": f"KakaoAK {ADMIN_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
        return res.json().get("properties", {}).get("nickname", "선장님")
    except: return "선장님"

TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_weather_real", "description": "해양대 실시간 날씨 조회"}},
    {
        "type": "function", 
        "function": {
            "name": "search_kmou_web", 
            "description": "학교 공지사항, 입학, 학사일정 등 웹 정보 검색",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "search_campus_knowledge", 
            "description": "학교 전화번호, 건물 위치 등 내부 DB 검색",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    }
]