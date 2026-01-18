import httpx
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timedelta

# [Master Key] 모든 API에 공통 적용
SERVICE_KEY = "bba09922567b209dcda0109a61683d9bfe53aba55655018555f073fb7d4d67fe"

# ==========================================
# 1. [Mobility] 버스 (기존 유지)
# ==========================================
async def get_bus_arrival(bus_number: str = None):
    """해양대 입구(04068) 실시간 버스 정보"""
    url = "https://apis.data.go.kr/6260000/BusanBims/bitArrByArsno"
    params = {"serviceKey": SERVICE_KEY, "arsno": "04068", "numOfRows": 10, "pageNo": 1}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
        root = ET.fromstring(response.content)
        items = root.findall(".//item")
        if not items: return "도착 정보 없음"
        
        res = []
        for item in items:
            line = item.findtext("lineno")
            min1 = item.findtext("min1")
            if bus_number and bus_number not in line: continue
            res.append(f"🚌 {line}번: {min1}분 후 ({item.findtext('station1')})")
        return "\n".join(res) if res else "해당 버스 정보 없음"
    except Exception as e: return f"버스 오류: {e}"

# ==========================================
# 2. [Dining] 맛집 통합 (착한가격 + 부산맛집)
# ==========================================
async def get_food_recommendation(type: str = "cheap"):
    """
    type='cheap': 가성비 착한가격업소 (돈 없을 때)
    type='famous': 부산 맛집 서비스 (맛있는 거 먹고 싶을 때)
    """
    if type == "cheap":
        url = "https://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    else:
        url = "https://apis.data.go.kr/6260000/FoodService/getFoodKr" #

    params = {"serviceKey": SERVICE_KEY, "numOfRows": 100, "pageNo": 1, "resultType": "json"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            data = response.json()
        
        # API 별 응답 구조에 따라 파싱
        key = 'getGoodPriceStore' if type == "cheap" else 'getFoodKr'
        items = data.get(key, {}).get('item', [])
        
        # 영도구 필터링
        targets = [item for item in items if "영도구" in item.get('addr', '') or "영도구" in item.get('GUGUN_NM', '')]
        
        if not targets: return "학교 근처(영도구)에 데이터가 없습니다."
        
        res = []
        for item in targets[:3]: # 3개만 추천
            name = item.get('sj') or item.get('MAIN_TITLE')
            menu = item.get('menu') or item.get('RPRSNTV_MENU')
            tel = item.get('tel') or item.get('CNTCT_TEL')
            res.append(f"🍽️ **{name}**\n - 메뉴: {menu}\n - 전화: {tel}")
            
        return "\n\n".join(res)
    except Exception as e: return f"맛집 검색 실패: {e}"

# ==========================================
# 3. [Safety] 병원/약국 (의료기관)
# ==========================================
async def get_medical_info(kind: str = "약국"):
    """영도구 내 병원/약국 조회"""
    url = "https://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 100, "pageNo": 1, "resultType": "json"}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=5.0)
            data = response.json()
            
        items = data.get('MedicalInstitInfo', {}).get('item', [])
        # 영도구이면서 사용자가 요청한 종류(약국/병원) 필터링
        targets = [i for i in items if "영도구" in i.get('addr', '') and kind in i.get('instit_kind', '')]
        
        if not targets: return f"근처에 조회된 {kind}이(가) 없습니다."
        
        res = [f"🏥 **{t['instit_nm']}** ({t['tel']})\n - 주소: {t['addr']}" for t in targets[:3]]
        return "\n\n".join(res)
    except Exception as e: return f"의료 정보 오류: {e}"

# ==========================================
# 4. [Leisure] 부산 축제 정보
# ==========================================
async def get_festival_info():
    """진행 중이거나 예정된 부산 축제 조회"""
    url = "https://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": SERVICE_KEY, "numOfRows": 10, "pageNo": 1, "resultType": "json"}
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
            items = res.json().get('getFestivalKr', {}).get('item', [])
            
        # 간단히 최근 3개만 표시 (날짜 필터링 로직 추가 가능)
        infos = [f"🎉 **{i['MAIN_TITLE']}** ({i['USAGE_DAY_WEEK_AND_TIME']})\n - 장소: {i['MAIN_PLACE']}" for i in items[:3]]
        return "\n\n".join(infos) if infos else "현재 예정된 축제 정보가 없습니다."
    except Exception as e: return f"축제 정보 오류: {e}"

# ==========================================
# 5. [Info] 대학알리미 & 날씨
# ==========================================
async def get_kmou_weather():
    """날씨 정보 (기존 유지)"""
    # ... (이전 답변의 날씨 코드와 동일, 공간 절약 위해 생략하지만 실제 구현시 포함해야 함)
    return "🌡️ 해양대 날씨: 맑음 (예시)" 

async def get_univ_stats():
    """대학알리미 기본 정보 조회"""
    # 대학알리미 API는 보통 학교코드가 필요하거나 복잡하므로 예시로 구조만 잡음
    return "🎓 한국해양대학교 공시 정보: 취업률 70% (데이터 연동 필요)"

# ==========================================
# Final TOOLS_SPEC
# ==========================================
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_bus_arrival", "description": "학교 버스 도착 정보 조회", "parameters": {"type": "object", "properties": {"bus_number": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "get_food_recommendation", "description": "맛집 추천 (cheap=가성비/착한가격, famous=유명맛집)", "parameters": {"type": "object", "properties": {"type": {"type": "string", "enum": ["cheap", "famous"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "영도구 내 병원/약국 찾기", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["약국", "병원", "의원"]}}, "required": []}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "부산 축제 정보 조회", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "현재 학교 날씨 조회", "parameters": {"type": "object", "properties": {}, "required": []}}}
]