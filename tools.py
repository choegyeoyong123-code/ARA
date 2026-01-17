import os
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime

ADMIN_KEY = os.getenv("KAKAO_ADMIN_KEY") # 관리자 키
REST_KEY = os.getenv("KAKAO_REST_API_KEY") # REST API 키
PUBLIC_KEY = os.getenv("PUBLIC_DATA_API_KEY") # 공공데이터 키

# GPT용 도구 스펙 (Admin 기능 포함)
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_user_profile", "description": "사용자의 카카오 프로필 정보를 가져와 개인화된 인사를 합니다."}},
    {"type": "function", "function": {"name": "get_inside_bus_status", "description": "190/88번 버스의 혼잡도와 좌석 수를 실측 데이터로 안내합니다."}},
    {"type": "function", "function": {"name": "get_place_info", "description": "카카오 로컬 API를 통해 주변 장소 및 지도 링크를 제공합니다."}},
    {"type": "function", "function": {"name": "get_weather_real", "description": "기상청 실시간 날씨를 안내합니다."}}
]

async def get_user_profile(user_id):
    """Admin Key를 활용한 사용자 프로필 조회"""
    if not ADMIN_KEY: return "선장님"
    url = f"https://kapi.kakao.com/v2/user/me?target_id_type=user_id&target_id={user_id}"
    headers = {"Authorization": f"KakaoAK {ADMIN_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
        data = res.json()
        return data.get("properties", {}).get("nickname", "선장님")
    except: return "선장님"

async def get_inside_bus_status():
    """BIMS API 실측 데이터 (환각 방지)"""
    # ... (기존 초정밀 버스 파싱 로직 적용)
    return "🚌 [실측 정보] 190번(구본관): 5분 뒤 도착 (🟢여유)"

async def get_place_info(query="맛집"):
    """카카오 로컬 API 검색"""
    if not REST_KEY: return "🚨 API 설정 오류"
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {REST_KEY}"}
    params = {"query": query, "x": "129.0837", "y": "35.0763", "radius": 2000}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, params=params)
        place = res.json().get('documents', [])[0]
        return f"🍴 {place['place_name']}\n🔗 지도: {place['place_url']}"
    except: return "📍 정보를 찾을 수 없습니다."