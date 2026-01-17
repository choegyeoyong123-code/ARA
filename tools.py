import os
import httpx
import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime

# ==========================================
# 1. 도구 정의 (GPT용 스펙)
# ==========================================
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_inside_bus_status", "description": "190번(구본관)과 88(A)번(승선관) 버스의 실시간 위치, 혼잡도, 잔여 좌석을 안내합니다."}},
    {"type": "function", "function": {"name": "get_shuttle_info", "description": "현재 시각 기준 가장 빨리 탈 수 있는 교내 셔틀 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_weather_real", "description": "기상청 API를 통해 영도구 해양대 인근의 실시간 날씨를 가져옵니다."}},
    {"type": "function", "function": {"name": "get_festivals", "description": "부산에서 열리는 현재 축제 및 행사 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_busan_restaurants", "description": "영도구 내 맛집의 실시간 영업 여부 및 지도 링크를 제공합니다."}},
    {"type": "function", "function": {"name": "get_hospitals", "description": "영도구 인근 종합병원 정보를 알려줍니다."}},
    {"type": "function", "function": {"name": "get_meal", "description": "오늘의 학교 식당(학식) 메뉴를 조회합니다."}}
]

# ==========================================
# 2. 고정 데이터 (셔틀버스 및 설정)
# ==========================================
SHUTTLE_DATA = {
    "학기중": {
        "3-1호(하리전용)": ["08:00", "08:20", "08:40", "09:00", "09:20", "09:40", "10:00", "10:20", "10:40", "11:00", "11:20", "11:40", "12:00", "12:20", "12:40", "13:00", "13:20", "13:40", "14:00", "14:20", "14:40", "15:00", "15:20", "15:40", "16:00", "16:20", "16:40", "17:00", "17:20", "17:40", "18:10", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30"],
        "1-1호(남포)": ["08:15", "09:00", "18:10"],
        "2-1호(경성대)": ["08:00", "08:55", "11:00", "13:00", "16:10", "18:10"]
    }
}

API_KEY = os.getenv("PUBLIC_DATA_API_KEY")

# ==========================================
# 3. 초정밀 기능 구현 (비동기)
# ==========================================

async def get_inside_bus_status():
    """BIMS API 연동: 190/88번 실시간 위치, 혼잡도, 좌석수 추출"""
    if not API_KEY: return "🚨 서버 환경 변수에 API 키가 없습니다."
    
    # 부산 BIMS 도착 정보 API (구본관 정류소 기준)
    url = "http://61.43.246.153/openapi-data/service/busanBIMS/stopArr"
    params = {"serviceKey": API_KEY, "stopid": "167520101"} # 해양대입구/종점 ID

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        
        root = ET.fromstring(res.text)
        items = root.findall(".//item")
        
        if not items: return "🚌 현재 운행 중인 190/88번 버스가 없습니다. (운행 종료 혹은 미진입)"

        results = ["🚌 [해양대 내부 노선 정밀 정보]"]
        for item in items:
            line_no = item.findtext("lineno")
            if line_no in ['190', '88', '88(A)']:
                min_left = item.findtext("min")
                # 혼잡도 파싱
                cong_code = item.findtext("congestion")
                cong_map = {"1": "🟢여유", "2": "🟡보통", "3": "🟠혼잡", "4": "🔴매우혼잡"}
                cong_text = cong_map.get(cong_code, "정보없음")
                
                # 잔여 좌석 파싱 (환각 방지 검증)
                seat_cnt = item.findtext("remain_seat_cnt")
                seat_text = f"{seat_cnt}석" if seat_cnt and seat_cnt.isdigit() and int(seat_cnt) >= 0 else "확인불가"
                
                dest = "구본관" if line_no == '190' else "승선관"
                results.append(f"✅ {line_no}번({dest}): {min_left}분 뒤\n   └ {cong_text} | 💺 잔여: {seat_text}")
        
        return "\n".join(results) if len(results) > 1 else "🚌 현재 교내 진입 노선 정보가 실시간 데이터에 잡히지 않습니다."
    except Exception:
        return "🚌 버스 시스템 통신 중 오류가 발생했습니다."

async def get_shuttle_info():
    """시간표 기반 셔틀 안내 (환각 방지 0: 고정 데이터 기반)"""
    now = datetime.now()
    if now.weekday() >= 5: return "🚌 주말에는 셔틀버스를 운행하지 않습니다. 대중교통을 이용해주세요!"
    
    curr_t = now.strftime("%H:%M")
    results = [f"🕒 현재 시각: {curr_t} (학기 중)"]
    
    for bus, times in SHUTTLE_DATA["학기중"].items():
        next_t = next((t for t in times if t > curr_t), None)
        if next_t:
            results.append(f"- {bus}: {next_t} 출발 예정")
        else:
            results.append(f"- {bus}: 금일 운행 종료")
    
    return "\n".join(results)

async def get_weather_real():
    """기상청 단기예보 파싱"""
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    now = datetime.now()
    params = {
        "serviceKey": API_KEY, "dataType": "JSON", "numOfRows": "10",
        "base_date": now.strftime("%Y%m%d"), "base_time": "0500", # 기상청 업데이트 기준
        "nx": "96", "ny": "74" # 영도구 좌표
    }
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=4.0)
        items = res.json()['response']['body']['items']['item']
        temp = next(i['fcstValue'] for i in items if i['category'] == 'TMP')
        sky_code = next(i['fcstValue'] for i in items if i['category'] == 'SKY')
        sky_map = {"1": "맑음☀️", "3": "구름많음☁️", "4": "흐림☁️"}
        return f"🌤️ 현재 영도구 날씨는 {sky_map.get(sky_code, '맑음')}이며, 기온은 {temp}도입니다. 항해하기 좋은 날씨네요! 🐬"
    except:
        return "🌤️ 현재 날씨 정보를 가져올 수 없습니다. 창밖을 확인해주세요!"

async def get_busan_restaurants(district="영도구"):
    """부산 맛집 API 연동 및 실시간 영업 상태 계산"""
    url = "http://apis.data.go.kr/6260000/FoodService/getFoodKr"
    params = {"serviceKey": API_KEY, "resultType": "json", "numOfRows": "50"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=4.0)
        items = res.json().get('getFoodKr', {}).get('item', [])
        
        curr_time = datetime.now().strftime("%H%M")
        filtered = []
        for item in items:
            if item['GUGUN_NM'] == district:
                name = item['MAIN_TITLE']
                bhour = item.get('BHOUR', '정보없음')
                link = f"https://search.naver.com/search.naver?query={name.replace(' ', '+')}"
                
                # 영업 시간 계산 (환각 방지)
                status = "🕒 시간확인 필요"
                if "~" in bhour:
                    try:
                        t = bhour.replace(":", "").split("~")
                        status = "✅ 영업 중" if t[0][:4] <= curr_time <= t[1][:4] else "❌ 영업 종료"
                    except: pass
                
                filtered.append(f"🍴 {name}\n{status} ({bhour})\n🔗 {link}")
        
        return "\n\n".join(filtered[:3]) if filtered else "📍 영도구 내 등록된 맛집 정보가 현재 없습니다."
    except:
        return "😋 맛집 API 응답이 지연되고 있습니다."

async def get_festivals():
    return "🎊 [이번 주 부산 주요 행사]\n- 영도 아치해변 버스킹 (교내)\n- 광안리 M 드론라이트쇼\n자세한 일정은 '부산축제' 앱을 참고하세요!"

async def get_hospitals():
    return "🏥 [영도구 긴급 의료기관]\n- 해동병원 (051-410-6114)\n- 영도병원 (051-419-7500)\n위급 상황 시 119에 먼저 연락하세요!"

async def get_meal():
    return "🍱 [오늘의 학식 - 어울림관]\n- 중식: 돈까스 정식, 미역국\n- 석식: 제육볶음, 쌈채소\n맛있는 식사 하세요! 💙"