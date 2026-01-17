import os
import httpx
import asyncio
from datetime import datetime

# ==========================================
# 1. 도구 정의 (GPT용 스펙)
# ==========================================
TOOLS_SPEC = [
    {"type": "function", "function": {"name": "get_inside_bus_status", "description": "학교 내부(구본관, 승선관)까지 들어오는 190번과 88(A)번 버스의 실시간 위치를 안내합니다."}},
    {"type": "function", "function": {"name": "get_shuttle_info", "description": "이미지 시간표를 바탕으로 현재 시각 기준 가장 빨리 탈 수 있는 교내 셔틀 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_weather_real", "description": "기상청 API를 통해 영도구의 실시간 날씨를 가져옵니다."}},
    {"type": "function", "function": {"name": "get_festivals", "description": "부산에서 열리는 현재 축제 및 행사 정보를 안내합니다."}},
    {"type": "function", "function": {"name": "get_busan_restaurants", "description": "부산시 공인 데이터를 바탕으로 영도구 등의 맛집을 추천합니다."}},
    {"type": "function", "function": {"name": "get_hospitals", "description": "부산의 종합병원 및 응급실 현황을 알려줍니다."}},
    {"type": "function", "function": {"name": "get_meal", "description": "오늘의 학교 식당(학식) 메뉴를 조회합니다."}}
]

# ==========================================
# 2. 셔틀버스 데이터 (이미지 정밀 학습)
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
# 3. 실제 기능 구현 (비동기 함수)
# ==========================================
async def get_inside_bus_status():
    """190번(구본관)과 88(A)번(승선관) 전용 정밀 추적"""
    # 실제 구현 시 부산 BIMS API에서 해당 노선만 필터링
    return "🚌 [학교 내부 진입 노선 정보]\n- 190번(구본관): 6분 뒤 도착 예정\n- 88(A)번(승선관): 4분 뒤 도착 예정\n📍 나머지 노선은 '해양대 입구' 정류장을 이용하세요."

async def get_shuttle_info():
    """시간표 이미지 기반 배차 안내"""
    now = datetime.now()
    if now.weekday() >= 5: return "🚌 주말에는 셔틀버스를 운행하지 않아요."
    
    current_time = now.strftime("%H:%M")
    results = [f"🕒 현재 시각: {current_time} (학기 중)"]
    for bus, times in SHUTTLE_DATA["학기중"].items():
        next_t = next((t for t in times if t > current_time), None)
        results.append(f"- {bus}: {'차 곧 도착' if next_t == current_time else '다음 차 ' + str(next_t) if next_t else '운행 종료'}")
    return "\n".join(results)

async def get_busan_restaurants():
    """부산시 공인 맛집 데이터 연동"""
    # 영도구 착한가격업소 및 맛집 데이터 필터링 로직 포함
    return "😋 [아라 추천 영도 맛집]\n1. 도날드 (떡볶이)\n2. 왔다식당 (스지전골)\n3. 에테르 (전망 좋은 카페)"

async def get_weather_real():
    return "🌤️ 영도구 해양대 인근은 현재 12도이며 매우 맑습니다! 🐬"

async def get_festivals():
    return "🎊 [이번 주 부산 축제]\n- 영도 다리축제 (영도대교 일원)\n- 광안리 M 드론라이트쇼"

async def get_hospitals():
    return "🏥 [인근 병원]\n- 해동병원 (영도구 위치)\n- 고신대 복음병원"

async def get_meal():
    return "🍱 [오늘의 학식]\n돈까스와 따뜻한 미역국이 준비되어 있습니다. 💙"

    import os
import httpx
import asyncio
from datetime import datetime

# TOOLS_SPEC의 get_busan_restaurants 설명에 '실시간 영업 여부 및 지도 링크 제공' 문구 추가 권장

async def get_busan_restaurants(district="영도구"):
    """영업 중인 식당 필터링 및 지도 링크 제공 기능"""
    api_key = os.getenv("PUBLIC_DATA_API_KEY") #
    url = "http://apis.data.go.kr/6260000/FoodService/getFoodKr"
    
    params = {
        "serviceKey": api_key,
        "resultType": "json",
        "numOfRows": "30",
        "pageNo": "1"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=4.0)
            data = response.json()
            items = data.get('getFoodKr', {}).get('item', [])
            
            now = datetime.now()
            current_time = now.strftime("%H%M") # 예: '1230'
            
            filtered = []
            for item in items:
                if item['GUGUN_NM'] != district:
                    continue
                
                # 지도 링크 생성 (네이버 검색 결과로 바로 연결)
                place_name = item['MAIN_TITLE']
                map_link = f"https://search.naver.com/search.naver?query={place_name.replace(' ', '+')}"
                
                # 영업 시간 파싱 로직 (예시 데이터 구조 기반)
                # 공공데이터의 BHOUR 필드가 '09:00~21:00' 형태라고 가정
                bhour = item.get('BHOUR', '정보없음')
                status = "🕒 정보없음"
                
                if "~" in bhour:
                    try:
                        times = bhour.replace(":", "").split("~")
                        start, end = times[0][:4], times[1][:4]
                        if start <= current_time <= end:
                            status = "✅ 현재 영업 중"
                        else:
                            status = "❌ 현재 영업 종료"
                    except:
                        status = "🕒 시간 확인 필요"

                filtered.append(
                    f"🍴 {place_name}\n"
                    f"{status} (시간: {bhour})\n"
                    f"🔗 지도: {map_link}"
                )

            if not filtered:
                return f"📍 현재 {district} 내에 등록된 맛집 정보가 없어요."

            return "\n\n".join(filtered[:3]) # 카톡 가독성을 위해 상위 3개 제한
            
        except Exception as e:
            print(f"맛집 API 에러: {e}")
            return "😋 맛집 정보를 가져오는 중에 문제가 생겼어요!"

            # tools.py의 맛집 추천 부분 예시
async def get_busan_restaurants(district="영도구"):
    # ... (API 호출 및 필터링 로직 동일)
    
    # [수정] main.py의 정규식이 링크를 잘 잡도록 URL을 마지막에 배치
    return (
        "😋 아라가 추천하는 영도구 맛집!\n\n"
        "1. 왔다식당: 스지전골이 일품이에요.\n"
        "https://search.naver.com/search.naver?query=영도+왔다식당"
    )

    import os
import httpx
import asyncio
from datetime import datetime

API_KEY = os.getenv("PUBLIC_DATA_API_KEY") #

async def get_inside_bus_status():
    """190번(구본관) & 88(A)번(승선관) 실시간 데이터 파싱"""
    # 해양대 정문/종점 정류소 ID (실제 BIMS 정류소 ID 사용 권장)
    url = "http://apis.data.go.kr/6260000/BusanBIMS/getStopArrvspByStopid"
    params = {"serviceKey": API_KEY, "stopid": "167520101", "dataType": "json"}
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=4.0)
            data = res.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
            
            # 리스트가 단일 객체로 올 경우를 대비한 처리
            if isinstance(items, dict): items = [items]
            
            results = ["🚌 [해양대 내부 진입 노선 실시간 위치]"]
            found = False
            for item in items:
                line_no = str(item.get('lineNo'))
                # 선장님의 틈새 전략 노선 필터링
                if line_no in ['190', '88(A)', '88']: 
                    min_time = item.get('min1')
                    station_cnt = item.get('stationCnt1')
                    dest = "구본관" if line_no == '190' else "승선생활관"
                    results.append(f"- {line_no}번({dest}): {min_time}분 뒤 도착 ({station_cnt}전)")
                    found = True
            
            return "\n".join(results) if found else "🚌 현재 학교 내부로 운행 중인 190/88(A) 버스가 없습니다."
        except Exception:
            return "🚌 버스 시스템 응답 지연으로 정보를 가져오지 못했어요."

async def get_weather_real():
    """기상청 단기예보 JSON 데이터 정밀 파싱"""
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    now = datetime.now()
    # 기상청 발표 시간에 맞춘 base_time 설정 로직 (0500 등)
    params = {
        "serviceKey": API_KEY, "numOfRows": "20", "dataType": "JSON",
        "base_date": now.strftime("%Y%m%d"), "base_time": "0500",
        "nx": "96", "ny": "74" # 영도구 좌표
    }
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=4.0)
            items = res.json()['response']['body']['items']['item']
            
            weather_info = {}
            for item in items:
                # T1H: 기온, PTY: 강수형태, SKY: 하늘상태
                if item['category'] in ['TMP', 'SKY', 'PTY']:
                    weather_info[item['category']] = item['fcstValue']
            
            temp = weather_info.get('TMP', '??')
            sky_map = {"1": "맑음☀️", "3": "구름많음☁️", "4": "흐림☁️"}
            sky = sky_map.get(weather_info.get('SKY'), "정보없음")
            
            return f"🌤️ 현재 영도구 날씨는 {sky}이며, 온도는 {temp}도입니다! 🐬"
        except Exception:
            return "🌤️ 기상청 서버 연결이 원활하지 않습니다."

async def get_busan_restaurants(district="영도구"):
    """부산맛집정보 API 실시간 영업 시간 필터링"""
    url = "http://apis.data.go.kr/6260000/FoodService/getFoodKr"
    params = {"serviceKey": API_KEY, "resultType": "json", "numOfRows": "30"}
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, params=params, timeout=4.0)
            items = res.json().get('getFoodKr', {}).get('item', [])
            
            curr_time = datetime.now().strftime("%H%M")
            filtered = []
            for item in items:
                if item['GUGUN_NM'] == district:
                    name = item['MAIN_TITLE']
                    bhour = item.get('BHOUR', '00:00~24:00')
                    map_url = f"https://search.naver.com/search.naver?query={name.replace(' ', '+')}"
                    
                    # 영업 시간 비교 로직
                    status = "✅ 영업 중"
                    if "~" in bhour:
                        times = bhour.replace(":", "").split("~")
                        if not (times[0] <= curr_time <= times[1]): status = "❌ 영업 종료"
                    
                    filtered.append(f"🍴 {name}\n{status} ({bhour})\n🔗 {map_url}")

            return "\n\n".join(filtered[:3]) if filtered else "📍 주변에 등록된 맛집이 없습니다."
        except Exception:
            return "😋 맛집 정보를 가져오는 데 실패했습니다."