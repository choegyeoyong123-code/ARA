import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta

# [Master Key] 승인된 공공데이터 포털 인증키
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"

# 1. [Mobility] 시내버스 실시간 도착 정보 (부산버스정보시스템)
async def get_bus_arrival(bus_number: str = None):
    """
    해양대 입구(04068) 정류소의 시내버스(190, 101, 88 등) 실시간 도착 정보를 조회합니다.
    """
    url = "https://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    params = {"serviceKey": SERVICE_KEY, "arsno": "04068", "numOfRows": 10, "pageNo": 1}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
        
        if response.status_code != 200: return "버스 데이터 통신 오류"
        
        root = ET.fromstring(response.content)
        items = root.findall(".//item")
        
        if not items: return "현재 도착 예정인 버스가 없습니다."

        results = []
        for item in items:
            line = item.findtext("lineno")
            min_left = item.findtext("min1")
            loc = item.findtext("station1")
            
            # 특정 버스 필터링
            if bus_number and bus_number not in line: continue
            
            results.append(f"🚌 **{line}번**: 약 {min_left}분 후 ({loc})")
            
        return "\n".join(results) if results else "해당 버스 정보가 없습니다."
    except Exception as e: return f"버스 정보 조회 실패: {e}"

# 2. [Dining] 영도구 착한가격업소 (가성비 식당)
async def get_cheap_eats(food_type: str = "한식"):
    """
    영도구 내의 가성비 식당(착한가격업소)을 조회합니다.
    """
    url = "https://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 50, "pageNo": 1, "resultType": "json"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            data = response.json()

        items = data.get('getGoodPriceStore', {}).get('item', [])
        
        # 영도구 & 음식 종류 필터링
        targets = [
            f"🍽️ **{i['sj']}** ({i['price']}원)\n   - 메뉴: {i['menu']}\n   - 위치: {i['addr']}"
            for i in items 
            if "영도구" in i.get('addr', '') and food_type in i.get('induty', '한식')
        ]
        
        if not targets: return "조건에 맞는 영도구 맛집을 찾지 못했습니다."
        return "\n\n".join(targets[:3]) # 3개만 추천
    except Exception as e: return f"맛집 검색 실패: {e}"

# 3. [Healthcare] 영도구 문 연 약국/병원
async def get_medical_info(kind: str = "약국"):
    """
    영도구 내 병원 또는 약국 정보를 조회합니다.
    """
    url = "https://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 100, "pageNo": 1, "resultType": "json"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            data = response.json()

        items = data.get('MedicalInstitInfo', {}).get('item', [])
        targets = [
            f"🏥 **{i['instit_nm']}**\n   - 전화: {i['tel']}\n   - 주소: {i['addr']}"
            for i in items 
            if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', '')
        ]

        if not targets: return f"근처에 조회된 {kind}이(가) 없습니다."
        return "\n\n".join(targets[:3])
    except Exception as e: return f"의료 정보 조회 실패: {e}"

# 4. [Weather] 해양대 캠퍼스 날씨
async def get_kmou_weather():
    """
    한국해양대(동삼동)의 실시간 기상 정보를 조회합니다.
    """
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    if now.minute < 45: now -= timedelta(hours=1)
    
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": 1, "numOfRows": 10, "dataType": "JSON",
        "base_date": now.strftime("%Y%m%d"), "base_time": now.strftime("%H00"),
        "nx": "98", "ny": "75" # 해양대 좌표
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            data = response.json()

        items = data['response']['body']['items']['item']
        weather = {}
        for item in items:
            if item['category'] == 'T1H': weather['temp'] = item['obsrValue']
            if item['category'] == 'RN1': weather['rain'] = item['obsrValue']
            if item['category'] == 'PTY': weather['code'] = item['obsrValue']

        status = "맑음 ☀️"
        if weather.get('code') != '0': status = "비/눈 🌧️"

        return f"🌡️ **현재 해양대 날씨**\n- 기온: {weather.get('temp')}℃\n- 상태: {status}\n- 강수량: {weather.get('rain')}mm"
    except Exception as e: return f"날씨 정보 오류: {e}"

# 5. [Culture] 부산 축제 정보
async def get_festival_info():
    """부산시 개최 축제 정보를 조회합니다."""
    url = "https://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 5, "pageNo": 1, "resultType": "json"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            items = response.json().get('getFestivalKr', {}).get('item', [])
            
        infos = [f"🎉 **{i['MAIN_TITLE']}**\n   - 장소: {i['MAIN_PLACE']}\n   - 기간: {i['USAGE_DAY_WEEK_AND_TIME']}" for i in items]
        return "\n\n".join(infos) if infos else "진행 중인 축제가 없습니다."
    except Exception as e: return f"축제 정보 오류: {e}"

# [Agent 도구 명세]
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "190번, 101번 등 시내버스 도착 정보 조회", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "영도구 가성비 식당 추천", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "영도구 약국/병원 조회", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "해양대 날씨 조회", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "부산 축제 정보 조회", "parameters": {"type": "object", "properties": {}, "required": []}}}
]