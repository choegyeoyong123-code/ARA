import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta
import urllib.parse

# [사용자님의 인증키 적용 완료]
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"

# 공통 헤더 (봇 탐지 회피용)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

async def fetch_api(url, params):
    """일반 API 호출용 함수 (맛집, 병원, 축제용)"""
    try:
        # 구형 서버 호환성을 위해 http 사용 및 verify=False
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(url, params=params, timeout=30.0)
            if response.status_code != 200:
                return {"status": "error", "code": response.status_code}
            return {"status": "success", "data": response}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# 1. [Mobility] 버스 (가장 중요: 수동 URL 조립 방식)
async def get_bus_arrival(bus_number: str = None):
    """
    부산시 버스 정보 시스템 - 수동 URL 조립으로 500 에러 완벽 회피
    """
    # 1. http 프로토콜 사용 (https 아님)
    base_url = "http://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    
    # 2. 파라미터 수동 조립 (파이썬의 자동 인코딩 개입 차단)
    # 사용자 키에 특수문자가 없으므로 그대로 넣어도 안전합니다.
    query = f"?serviceKey={SERVICE_KEY}&arsno=04068&numOfRows=20&pageNo=1"
    full_url = base_url + query

    print(f"[DEBUG] Bus URL: {full_url}") # 로그 확인용

    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(full_url, timeout=30.0)
            
        if response.status_code != 200:
            return json.dumps({"status": "error", "msg": f"서버 응답 코드: {response.status_code}"}, ensure_ascii=False)
        
        # XML 파싱
        root = ET.fromstring(response.content)
        items = root.findall(".//item")
        
        if not items:
            return json.dumps({"status": "empty", "msg": "현재 도착 예정인 버스가 없습니다."}, ensure_ascii=False)

        bus_list = []
        for item in items:
            line = item.findtext("lineno")
            min_left = item.findtext("min1")
            loc = item.findtext("station1")
            
            # 도착 시간 숫자 추출 (정렬용)
            try:
                min_val = int(''.join(filter(str.isdigit, min_left)))
            except:
                min_val = 99

            if bus_number and bus_number not in line: continue
            
            bus_list.append({
                "bus_no": line,
                "minutes_left": min_left,
                "current_loc": loc,
                "sort_key": min_val
            })
            
        bus_list.sort(key=lambda x: x["sort_key"])
        return json.dumps({"status": "success", "buses": bus_list}, ensure_ascii=False)

    except Exception as e:
        print(f"[ERROR] Bus API: {e}")
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 2. [Dining] 맛집 (상세 정보 채굴)
async def get_cheap_eats(food_type: str = "한식"):
    url = "http://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": "100", "pageNo": "1", "resultType": "json"}
    
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
                    "price": i.get('price'),
                    "tel": i.get('tel'),
                    "addr": i.get('addr'),
                    "desc": i.get('cn', '') # 소개글
                })
        return json.dumps({"status": "success", "restaurants": targets[:5]}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 3. [Weather] 날씨 (수치 데이터 정밀 채굴)
async def get_kmou_weather():
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    if now.minute < 45: now -= timedelta(hours=1)
    params = {
        "serviceKey": SERVICE_KEY, "pageNo": "1", "numOfRows": "10", "dataType": "JSON",
        "base_date": now.strftime("%Y%m%d"), "base_time": now.strftime("%H00"), "nx": "98", "ny": "75"
    }
    
    res = await fetch_api(url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)
    
    try:
        items = res["data"].json()['response']['body']['items']['item']
        data = {item['category']: item['obsrValue'] for item in items}
        
        weather_info = {
            "temp": float(data.get('T1H', 0)),
            "rain_type": int(data.get('PTY', 0)), # 0:없음, 1:비, 2:비/눈, 3:눈...
            "rain_amount": float(data.get('RN1', 0)),
            "humidity": float(data.get('REH', 0)),
            "wind_speed": float(data.get('WSD', 0))
        }
        return json.dumps({"status": "success", "weather": weather_info}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 4. [Healthcare] 병원/약국
async def get_medical_info(kind: str="약국"):
    url = "http://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": "100", "pageNo": "1", "resultType": "json"}
    
    res = await fetch_api(url, params)
    try:
        items = res["data"].json().get('MedicalInstitInfo', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', ''):
                targets.append({"name": i.get('instit_nm'), "tel": i.get('tel'), "addr": i.get('addr'), "time": i.get('trtm_mon_end')})
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# 5. [Festival] 축제
async def get_festival_info():
    url = "http://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": "5", "pageNo": "1", "resultType": "json"}
    res = await fetch_api(url, params)
    try:
        items = res["data"].json().get('getFestivalKr', {}).get('item', [])
        targets = [{"title": i.get('MAIN_TITLE'), "place": i.get('MAIN_PLACE'), "date": i.get('USAGE_DAY_WEEK_AND_TIME')}]
        return json.dumps({"status": "success", "festivals": targets}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# Tool Specification
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "버스 도착 정보(JSON)", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "맛집 정보(JSON)", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "날씨 정보(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "병원/약국 정보(JSON)", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "축제 정보(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}}
]