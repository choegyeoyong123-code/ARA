import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta
import urllib.parse

# [Master Key & Config]
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"
TIMEOUT_CONFIG = 30.0

# 공통: API 호출 및 에러 핸들링 래퍼 함수
async def fetch_api(url, params):
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
            if response.status_code != 200:
                return {"status": "error", "code": response.status_code, "msg": "API Server Error"}
            return {"status": "success", "data": response}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# 1. [Mobility] 버스 (구조화된 데이터 반환)
async def get_bus_arrival(bus_number: str = None):
    """버스 도착 정보를 분석 가능한 JSON 형태로 반환"""
    base_url = "https://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    params = f"?serviceKey={SERVICE_KEY}&arsno=04068&numOfRows=20&pageNo=1"
    
    res = await fetch_api(base_url + params, {})
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)

    try:
        root = ET.fromstring(res["data"].content)
        items = root.findall(".//item")
        if not items: return json.dumps({"status": "empty", "msg": "도착 예정 버스 없음"}, ensure_ascii=False)

        bus_list = []
        for item in items:
            line = item.findtext("lineno")
            min_left = item.findtext("min1")
            loc = item.findtext("station1")
            
            if bus_number and bus_number not in line: continue
            
            bus_list.append({
                "bus_no": line,
                "minutes_left": min_left, # 숫자 분석을 위해 원본 유지
                "current_loc": loc
            })
            
        return json.dumps({"status": "success", "buses": bus_list}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": f"Parsing Error: {e}"}, ensure_ascii=False)

# 2. [Dining] 맛집 (평점/가격 분석용 데이터)
async def get_cheap_eats(food_type: str = "한식"):
    url = "https://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 150, "pageNo": 1, "resultType": "json"}
    
    res = await fetch_api(url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)

    try:
        items = res["data"].json().get('getGoodPriceStore', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and food_type in i.get('induty', '한식'):
                targets.append({
                    "name": i.get('sj'),
                    "menu": i.get('menu'),
                    "price": i.get('price'), # 숫자형 분석 가능
                    "tel": i.get('tel'),
                    "addr": i.get('addr')
                })
        return json.dumps({"status": "success", "restaurants": targets[:5]}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 3. [Weather] 날씨 (수치 데이터 정밀 반환)
async def get_kmou_weather():
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    if now.minute < 45: now -= timedelta(hours=1)
    params = {
        "serviceKey": SERVICE_KEY, "pageNo": 1, "numOfRows": 10, "dataType": "JSON",
        "base_date": now.strftime("%Y%m%d"), "base_time": now.strftime("%H00"), "nx": "98", "ny": "75"
    }
    
    res = await fetch_api(url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)
    
    try:
        items = res["data"].json()['response']['body']['items']['item']
        data = {item['category']: item['obsrValue'] for item in items}
        
        # 분석용 딕셔너리 리턴
        weather_info = {
            "temp": float(data.get('T1H', 0)),
            "rain_type": int(data.get('PTY', 0)), # 0:없음, 1:비...
            "rain_amount": float(data.get('RN1', 0)),
            "humidity": float(data.get('REH', 0)),
            "wind_speed": float(data.get('WSD', 0))
        }
        return json.dumps({"status": "success", "weather": weather_info}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 4. [Healthcare] (기존 유지 - JSON 덤프만 적용)
async def get_medical_info(kind: str="약국"):
    url = "https://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 100, "pageNo": 1, "resultType": "json"}
    
    res = await fetch_api(url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)
    
    try:
        items = res["data"].json().get('MedicalInstitInfo', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', ''):
                targets.append({"name": i.get('instit_nm'), "tel": i.get('tel'), "addr": i.get('addr')})
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# 5. [Festival] (기존 유지)
async def get_festival_info():
    url = "https://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 5, "pageNo": 1, "resultType": "json"}
    res = await fetch_api(url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)
    
    try:
        items = res["data"].json().get('getFestivalKr', {}).get('item', [])
        targets = [{"title": i.get('MAIN_TITLE'), "place": i.get('MAIN_PLACE'), "date": i.get('USAGE_DAY_WEEK_AND_TIME')} for i in items]
        return json.dumps({"status": "success", "festivals": targets}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# TOOLS_SPEC (동일)
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "버스 데이터(JSON)", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "맛집 데이터(JSON)", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "날씨 데이터(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "병원 데이터(JSON)", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "축제 데이터(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}}
]