import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta
import urllib.parse

# [Master Key]
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"
# 혹시 몰라 인코딩된 키도 준비 (필요시 교체)
SERVICE_KEY_ENCODED = urllib.parse.quote(SERVICE_KEY)

# 공통 클라이언트 설정 (타임아웃 30초, SSL 검증 무시)
TIMEOUT_CONFIG = 30.0

async def get_bus_arrival(bus_number: str = None):
    url = "https://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    params = {
        "serviceKey": SERVICE_KEY, # 디코딩된 키 사용
        "arsno": "04068",
        "numOfRows": 10,
        "pageNo": 1
    }

    try:
        # verify=False 옵션이 핵심입니다 (SSL 에러 방지)
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
        
        # [디버깅용] 실제 응답 내용을 로그에 출력
        print(f"[DEBUG] Bus API Status: {response.status_code}")
        print(f"[DEBUG] Bus API Body: {response.text[:200]}") # 앞부분만 출력

        if response.status_code != 200:
            return f"버스 서버 점검 중입니다. (코드: {response.status_code})"
        
        root = ET.fromstring(response.content)
        
        # 에러 메시지가 담겨있는지 확인 (SERVICE_KEY_IS_NOT_REGISTERED_ERROR 등)
        header_msg = root.findtext(".//headerMsg")
        if header_msg and "Normal Service" not in header_msg:
             return f"API 키 에러: {header_msg}"

        items = root.findall(".//item")
        if not items: return "도착 예정인 버스가 없습니다."

        results = []
        for item in items:
            line = item.findtext("lineno")
            min_left = item.findtext("min1")
            loc = item.findtext("station1")
            if bus_number and bus_number not in line: continue
            results.append(f"🚌 {line}번: {min_left}분 후 ({loc})")
            
        return "\n".join(results) if results else f"{bus_number}번 버스 정보가 없습니다."

    except Exception as e:
        print(f"[ERROR] Bus API Fail: {e}") # 로그에 에러 출력
        return f"통신 장애 발생: {str(e)}"

# ... (나머지 맛집, 병원, 날씨 함수들도 동일하게 verify=False 추가 권장) ...
# 아래는 예시로 맛집 함수만 수정
async def get_cheap_eats(food_type: str = "한식"):
    url = "https://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 10, "pageNo": 1, "resultType": "json"}

    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
            
        print(f"[DEBUG] Food API Status: {response.status_code}") # 디버깅
        data = response.json()
        
        items = data.get('getGoodPriceStore', {}).get('item', [])
        targets = [f"🍽️ {i['sj']} ({i['price']}원) - {i['menu']}" for i in items if "영도구" in i.get('addr', '')][:3]
        
        return "\n".join(targets) if targets else "검색 결과가 없습니다."
    except Exception as e: return f"맛집 검색 에러: {e}"

# ... (병원, 날씨, 축제 함수는 생략하지만 verify=False 꼭 넣으세요) ...

# 3, 4, 5번 함수는 기존 코드에서 async with httpx.AsyncClient(verify=False) 로만 바꾸시면 됩니다.
# -----------------------------------------------------------
async def get_medical_info(kind: str = "약국"):
    url = "https://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 100, "pageNo": 1, "resultType": "json"}
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
        items = response.json().get('MedicalInstitInfo', {}).get('item', [])
        targets = [f"🏥 {i['instit_nm']} ({i['tel']})" for i in items if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', '')][:3]
        return "\n".join(targets) if targets else "정보 없음"
    except Exception as e: return f"의료 정보 에러: {e}"

async def get_kmou_weather():
    url = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    if now.minute < 45: now -= timedelta(hours=1)
    params = {
        "serviceKey": SERVICE_KEY, "pageNo": 1, "numOfRows": 10, "dataType": "JSON",
        "base_date": now.strftime("%Y%m%d"), "base_time": now.strftime("%H00"), "nx": "98", "ny": "75"
    }
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
        # 날씨 파싱 로직 (기존 동일)
        items = response.json()['response']['body']['items']['item']
        temp = next((i['obsrValue'] for i in items if i['category']=='T1H'), '-')
        return f"🌡️ 기온: {temp}℃"
    except Exception as e: return f"날씨 에러: {e}"

async def get_festival_info():
    url = "https://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 5, "pageNo": 1, "resultType": "json"}
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, params=params, timeout=TIMEOUT_CONFIG)
        items = response.json().get('getFestivalKr', {}).get('item', [])
        infos = [f"🎉 {i['MAIN_TITLE']}" for i in items]
        return "\n".join(infos) if infos else "축제 없음"
    except Exception as e: return f"축제 에러: {e}"

# TOOLS_SPEC은 기존과 동일하게 유지
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "190번, 101번 등 시내버스 도착 정보 조회", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "영도구 가성비 식당 추천", "parameters": {"type": "object", "properties": {"food_type": {"type": "string", "enum": ["한식", "중식", "일식", "경양식"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "영도구 약국/병원 조회", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "해양대 날씨 조회", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "부산 축제 정보 조회", "parameters": {"type": "object", "properties": {}, "required": []}}}
]