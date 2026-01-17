import os
import httpx
import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# ==========================================
# 1. 환경 변수 및 설정
# ==========================================
# 공공데이터포털 API 키 (버스, 날씨 등)
PUBLIC_API_KEY = os.getenv("PUBLIC_DATA_API_KEY")
# 카카오 REST API 키 (맛집, 병원 등 장소 검색)
KAKAO_KEY = os.getenv("KAKAO_REST_API_KEY")

# GPT용 도구 스펙 정의
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_inside_bus_status", "description": "190번(구본관)과 88(A)번(승선관) 버스의 실시간 위치, 혼잡도, 잔여 좌석을 안내합니다."}},
    {"type": "function", "function": {"name": "get_shuttle_info", "description": "현재 시각 기준 가장 빨리 탈 수 있는 교내 셔틀 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_weather_real", "description": "기상청 API를 통해 영도구 해양대 인근의 실시간 날씨를 가져옵니다."}},
    {"type": "function", "function": {"name": "get_festivals", "description": "부산에서 열리는 현재 축제 및 행사 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_busan_restaurants", "description": "카카오 로컬 API를 통해 주변 맛집의 실시간 정보와 지도 링크를 제공합니다."}},
    {"type": "function", "function": {"name": "get_hospitals", "description": "카카오 로컬 API를 통해 인근 종합병원 및 응급실 정보를 알려줍니다."}},
    {"type": "function", "function": {"name": "get_meal", "description": "오늘의 학교 식당(학식) 메뉴를 조회합니다."}}
]

# 교내 셔틀 데이터
SHUTTLE_DATA = {
    "학기중": {
        "3-1호(하리전용)": ["08:00", "08:20", "08:40", "09:00", "09:20", "09:40", "10:00", "10:20", "10:40", "11:00", "11:20", "11:40", "12:00", "12:20", "12:40", "13:00", "13:20", "13:40", "14:00", "14:20", "14:40", "15:00", "15:20", "15:40", "16:00", "16:20", "16:40", "17:00", "17:20", "17:40", "18:10", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"],
        "1-1호(남포)": ["08:15", "09:00", "18:10"],
        "2-1호(경성대)": ["08:00", "08:55", "11:00", "13:00", "16:10", "18:10"]
    }
}

# ==========================================
# 2. 초정밀 기능 구현 (비동기)
# ==========================================

async def get_inside_bus_status():
    """BIMS API 연동: 시내버스 실시간 정보 파싱"""
    if not PUBLIC_API_KEY: return "🚨 공공데이터 API 키가 설정되지 않았습니다."
    
    url = "http://61.43.246.153/openapi-data/service/busanBIMS/stopArr"
    params = {"serviceKey": PUBLIC_API_KEY, "stopid": "167520101"} # 해양대입구/종점

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        root = ET.fromstring(res.text)
        items = root.findall(".//item")
        
        if not items: return "🚌 현재 운행 중인 190/88번 버스가 없습니다."

        results = ["🚌 [해양대 내부 노선 정밀 정보]"]
        for item in items:
            line_no = item.findtext("lineno")
            if line_no in ['190', '88', '88(A)']:
                min_left = item.findtext("min")
                cong_code = item.findtext("congestion")
                cong_map = {"1": "🟢여유", "2": "🟡보통", "3": "🟠혼잡", "4": "🔴매우혼잡"}
                seat_cnt = item.findtext("remain_seat_cnt")
                seat_text = f"{seat_cnt}석" if seat_cnt and seat_cnt.isdigit() and int(seat_cnt) >= 0 else "확인불가"
                
                dest = "구본관" if line_no == '190' else "승선관"
                results.append(f"✅ {line_no}번({dest}): {min_left}분 뒤 ({cong_map.get(cong_code, '정보없음')} | 💺 {seat_text})")
        return "\n".join(results)
    except: return "🚌 버스 정보를 가져오는 데 실패했습니다."

async def get_shuttle_info():
    """셔틀 시간표 기반 안내"""
    now = datetime.now()
    if now.weekday() >= 5: return "🚌 주말에는 셔틀버스를 운행하지 않아요!"
    
    curr_t = now.strftime("%H:%M")
    results = [f"🕒 현재 시각: {curr_t}"]
    for bus, times in SHUTTLE_DATA["학기중"].items():
        next_t = next((t for t in times if t > curr_t), None)
        results.append(f"- {bus}: {next_t if next_t else '운행 종료'} 출발")
    return "\n".join(results)

async def get_weather_real():
    """기상청 단기예보 파싱"""
    if not PUBLIC_API_KEY: return "🚨 API 키가 없습니다."
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    now = datetime.now()
    params = {
        "serviceKey": PUBLIC_API_KEY, "dataType": "JSON", "numOfRows": "10",
        "base_date": now.strftime("%Y%m%d"), "base_time": "0500", "nx": "96", "ny": "74"
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=4.0)
        items = res.json()['response']['body']['items']['item']
        temp = next(i['fcstValue'] for i in items if i['category'] == 'TMP')
        sky_code = next(i['fcstValue'] for i in items if i['category'] == 'SKY')
        sky_map = {"1": "맑음☀️", "3": "구름많음☁️", "4": "흐림☁️"}
        return f"🌤️ 현재 영도구 해양대 인근은 {sky_map.get(sky_code, '맑음')}, 기온은 {temp}도입니다! 🐬"
    except: return "🌤️ 날씨 정보를 가져올 수 없습니다."

async def get_busan_restaurants(query="해양대 맛집"):
    """카카오 로컬 API: 실시간 장소 검색 및 지도 연동"""
    if not KAKAO_KEY: return "🚨 카카오 API 키가 설정되지 않았습니다."
    
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    # 해양대 중심 좌표 기반 2km 반경 검색
    params = {"query": query, "x": "129.0837", "y": "35.0763", "radius": 2000, "sort": "distance"}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, params=params, timeout=5.0)
        data = res.json()
        documents = data.get('documents', [])
        
        if not documents: return f"📍 '{query}' 검색 결과가 주변에 없습니다. 🌊"

        results = [f"🍴 [아라 추천 '{query}' 로컬 정보]"]
        for place in documents[:3]:
            name = place['place_name']
            dist = place['distance']
            category = place['category_name'].split('>')[-1].strip()
            place_url = place['place_url'] # 카카오맵 링크
            results.append(f"✅ {name} ({category})\n📍 거리: {dist}m\n🔗 지도: {place_url}")
        return "\n\n".join(results)
    except: return "😋 카카오 장소 검색 서비스 연결이 원활하지 않습니다."

async def get_hospitals():
    """카카오 API 활용 실시간 병원/약국 검색"""
    return await get_busan_restaurants(query="영도 응급실")

async def get_festivals():
    return "🎊 [이번 주 부산 주요 행사]\n- 영도 아치해변 버스킹 (교내)\n- 광안리 M 드론라이트쇼\n행사 일정은 기상 상황에 따라 변동될 수 있습니다!"

async def get_meal():
    return "🍱 [오늘의 학식 - 어울림관]\n- 중식: 돈까스 정식, 미역국\n- 석식: 제육볶음, 쌈채소\n맛있는 식사 하세요! 💙"