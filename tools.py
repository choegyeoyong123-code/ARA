import httpx
import json
from datetime import datetime, timedelta
import urllib.parse

# =========================================================
# [설정] ODsay Server Key (사용자님의 키 적용됨)
# =========================================================
ODSAY_KEY = "cQe1LyWbfr3Qk7M500yoQ8eMV/0gaC4LY7sfnGeCQ/k"
PUBLIC_DATA_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"
# =========================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# ---------------------------------------------------------
# 1. [Mobility] 버스 (ODsay - 정류장 타겟팅 수정)
# ---------------------------------------------------------
async def get_bus_arrival(bus_number: str = None):
    try:
        async with httpx.AsyncClient(verify=False) as client:
            # [수정 1] 검색어를 '한국해양대학교'로 변경 (가장 확실한 정류장)
            search_url = "https://api.odsay.com/v1/api/searchStation"
            search_params = {
                "apiKey": ODSAY_KEY,
                "stationName": "한국해양대학교",  # <--- 여기가 핵심 변경 사항
                "CID": "6",
            }
            
            search_res = await client.get(search_url, params=search_params, timeout=10.0)
            search_data = search_res.json()
            
            # [디버깅] ODsay가 찾은 정류장이 무엇인지 로그에 출력
            if search_data.get('result', {}).get('station'):
                found_station = search_data['result']['station'][0]
                print(f"[DEBUG] Found Station: {found_station['stationName']} (ID: {found_station['stationID']})")
                station_id = found_station['stationID']
            else:
                print("[DEBUG] Station Not Found")
                return json.dumps({"status": "empty", "msg": "정류장을 찾을 수 없습니다."}, ensure_ascii=False)

            # 2단계: 실시간 도착 정보 조회
            realtime_url = "https://api.odsay.com/v1/api/realtimeStation"
            realtime_params = {
                "apiKey": ODSAY_KEY,
                "stationID": station_id
            }

            arrival_res = await client.get(realtime_url, params=realtime_params, timeout=10.0)
            arrival_data = arrival_res.json()

            # 도착 정보가 아예 없는 경우
            if not arrival_data.get('result', {}).get('realtimeArrivalList'):
                # 190번 같은 경우 차고지 대기중일 수 있음
                return json.dumps({
                    "status": "empty", 
                    "msg": "현재 정류장에 도착 예정인 버스가 없습니다. (차고지 대기 중이거나 배차 간격 확인 필요)"
                }, ensure_ascii=False)

            # 3단계: 데이터 파싱
            arrival_list = arrival_data['result']['realtimeArrivalList']
            bus_results = []
            
            for bus in arrival_list:
                route_name = bus.get('routeNm')
                arrival_msg = bus.get('arrival1', {}).get('msg1', '정보없음')
                
                # 사용자가 콕 집어 물어본 버스만 필터링 (예: 190)
                if bus_number and bus_number not in route_name: continue

                bus_results.append({
                    "bus_no": route_name,
                    "status": arrival_msg,
                    "low_plate": "저상" if bus.get('lowPlate1') == '1' else "일반"
                })

            # 만약 필터링 후 결과가 없다면
            if not bus_results:
                 return json.dumps({"status": "empty", "msg": f"{bus_number}번 버스의 도착 정보를 찾을 수 없습니다."}, ensure_ascii=False)

            return json.dumps({"status": "success", "buses": bus_results}, ensure_ascii=False)

    except Exception as e:
        print(f"[ERROR] ODsay Logic: {e}")
        return json.dumps({"status": "error", "msg": f"시스템 오류: {e}"}, ensure_ascii=False)

# ---------------------------------------------------------
# [Dining] 맛집 (기존 유지)
# ---------------------------------------------------------
async def get_cheap_eats(food_type: str = "한식"):
    base_url = "http://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    query = f"?serviceKey={PUBLIC_DATA_KEY}&numOfRows=100&pageNo=1&resultType=json"
    full_url = base_url + query

    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(full_url, timeout=15.0)
        
        items = response.json().get('getGoodPriceStore', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and food_type in i.get('induty', '한식'):
                targets.append({
                    "name": i.get('sj'), "menu": i.get('menu'), "price": i.get('price'),
                    "tel": i.get('tel'), "addr": i.get('addr')
                })
        return json.dumps({"status": "success", "restaurants": targets[:5]}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# ---------------------------------------------------------
# [Weather] 날씨 (기존 유지)
# ---------------------------------------------------------
async def get_kmou_weather():
    base_url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    if now.minute < 45: now -= timedelta(hours=1)
    
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H00")
    query = f"?serviceKey={PUBLIC_DATA_KEY}&pageNo=1&numOfRows=10&dataType=JSON&base_date={date_str}&base_time={time_str}&nx=98&ny=75"
    full_url = base_url + query
    
    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(full_url, timeout=15.0)
        items = response.json()['response']['body']['items']['item']
        data = {item['category']: item['obsrValue'] for item in items}
        weather_info = {
            "temp": float(data.get('T1H', 0)), "rain_type": int(data.get('PTY', 0)),
            "rain_amount": float(data.get('RN1', 0)), "humidity": float(data.get('REH', 0)),
            "wind_speed": float(data.get('WSD', 0))
        }
        return json.dumps({"status": "success", "weather": weather_info}, ensure_ascii=False)
    except Exception as e: return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# ---------------------------------------------------------
# [Healthcare] 병원 (기존 유지)
# ---------------------------------------------------------
async def get_medical_info(kind: str="약국"):
    base_url = "http://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    query = f"?serviceKey={PUBLIC_DATA_KEY}&numOfRows=100&pageNo=1&resultType=json"
    full_url = base_url + query
    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(full_url, timeout=15.0)
        items = response.json().get('MedicalInstitInfo', {}).get('item', [])
        targets = []
        for i in items:
            if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', ''):
                targets.append({"name": i.get('instit_nm'), "tel": i.get('tel'), "addr": i.get('addr')})
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# ---------------------------------------------------------
# [Festival] 축제 (기존 유지)
# ---------------------------------------------------------
async def get_festival_info():
    base_url = "http://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    query = f"?serviceKey={PUBLIC_DATA_KEY}&numOfRows=5&pageNo=1&resultType=json"
    full_url = base_url + query
    try:
        async with httpx.AsyncClient(verify=False, headers=HEADERS) as client:
            response = await client.get(full_url, timeout=15.0)
        items = response.json().get('getFestivalKr', {}).get('item', [])
        targets = [{"title": i.get('MAIN_TITLE'), "place": i.get('MAIN_PLACE'), "date": i.get('USAGE_DAY_WEEK_AND_TIME')} for i in items]
        return json.dumps({"status": "success", "festivals": targets}, ensure_ascii=False)
    except: return json.dumps({"status": "empty"}, ensure_ascii=False)

# Tools Spec
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "버스 도착 정보(ODsay)", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "영도구 맛집 정보", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "해양대 실시간 날씨", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "영도구 병원/약국", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "부산 축제 정보", "parameters": {"type": "object", "properties": {}, "required": []}}}
]