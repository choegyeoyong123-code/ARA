import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta
import urllib.parse

# [중요] 여기에 '일반 인증키(Decoding)'을 넣으세요. (현재 쓰시는 bba099... 키가 맞습니다)
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"

# [핵심] 구형 서버를 속이기 위한 헤더 (나는 봇이 아니라 크롬이다!)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# 공통 API 호출 함수 (타임아웃 넉넉히 30초)
async def fetch_api(url, params):
    try:
        # verify=False는 유지하되, http로 변경된 URL을 호출
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(url, params=params, timeout=30.0)
            
            # [디버깅] 서버가 뱉은 진짜 이유를 로그에 찍음
            if response.status_code != 200:
                print(f"[DEBUG] Error Code: {response.status_code}")
                print(f"[DEBUG] Error Body: {response.text[:200]}")
                return {"status": "error", "code": response.status_code, "msg": "통신 오류"}
            
            return {"status": "success", "data": response}
    except Exception as e:
        print(f"[DEBUG] Exception: {e}")
        return {"status": "error", "msg": str(e)}

# 1. [Mobility] 버스 (구형 서버 맞춤형 수정)
async def get_bus_arrival(bus_number: str = None):
    # [핵심 변경 1] https -> http (보안 제거)
    base_url = "http://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    
    # [핵심 변경 2] 파라미터 딕셔너리 방식 복귀 (httpx가 알아서 인코딩하도록 맡김)
    params = {
        "serviceKey": SERVICE_KEY, # Decoding 키를 넣으면 httpx가 알아서 Encoding해서 보냄
        "arsno": "04068",
        "numOfRows": "20",
        "pageNo": "1"
    }
    
    res = await fetch_api(base_url, params)
    if res["status"] == "error": return json.dumps(res, ensure_ascii=False)

    try:
        # XML 파싱
        root = ET.fromstring(res["data"].content)
        
        # 결과 코드 확인 (NORMAL_CODE가 아니면 에러)
        header_code = root.findtext(".//headerCd")
        if header_code != "00" and header_code != "NORMAL_SERVICE":
             msg = root.findtext(".//headerMsg")
             return json.dumps({"status": "error", "msg": f"API Error: {msg}"}, ensure_ascii=False)

        items = root.findall(".//item")
        if not items: return json.dumps({"status": "empty", "msg": "도착 예정 버스 없음"}, ensure_ascii=False)

        bus_list = []
        for item in items:
            line = item.findtext("lineno")
            min_left = item.findtext("min1")
            loc = item.findtext("station1")
            
            # 숫자만 추출 (예: '5분' -> 5)
            try:
                min_val = int(''.join(filter(str.isdigit, min_left)))
            except:
                min_val = 99 # 파싱 불가시 끝으로 보냄

            if bus_number and bus_number not in line: continue
            
            bus_list.append({
                "bus_no": line,
                "minutes_left": min_left,
                "current_loc": loc,
                "sort_key": min_val
            })
            
        # 빨리 오는 순서대로 정렬
        bus_list.sort(key=lambda x: x["sort_key"])
            
        return json.dumps({"status": "success", "buses": bus_list}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": f"XML Parsing Error: {e}"}, ensure_ascii=False)

# 2. [Dining] 맛집 (기존 유지 + http 변경)
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
                    "addr": i.get('addr')
                })
        return json.dumps({"status": "success", "restaurants": targets[:5]}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 3. [Weather] 날씨 (http 변경)
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
            "rain_type": int(data.get('PTY', 0)),
            "rain_amount": float(data.get('RN1', 0)),
            "humidity": float(data.get('REH', 0)),
            "wind_speed": float(data.get('WSD', 0))
        }
        return json.dumps({"status": "success", "weather": weather_info}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# 4. [Healthcare] (http 변경)
async def get_medical_info(kind: str="약국"):
    url = "http://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": "100", "pageNo": "1", "resultType": "json"}
    
    res = await fetch_api(url, params)
    try:
        items = res["data"].json().get('MedicalInstitInfo', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', ''):
                targets.append({"name": i.get('instit_nm'), "tel": i.get('tel'), "addr": i.get('addr')})
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# 5. [Festival] (http 변경)
async def get_festival_info():
    url = "http://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": "5", "pageNo": "1", "resultType": "json"}
    res = await fetch_api(url, params)
    try:
        items = res["data"].json().get('getFestivalKr', {}).get('item', [])
        targets = [{"title": i.get('MAIN_TITLE'), "place": i.get('MAIN_PLACE'), "date": i.get('USAGE_DAY_WEEK_AND_TIME')} for i in items]
        return json.dumps({"status": "success", "festivals": targets}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# TOOLS_SPEC (유지)
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "버스 데이터(JSON)", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "맛집 데이터(JSON)", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "날씨 데이터(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "병원 데이터(JSON)", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "축제 데이터(JSON)", "parameters": {"type": "object", "properties": {}, "required": []}}}
]