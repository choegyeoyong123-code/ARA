import os
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime

# API 키 설정
PUBLIC_API_KEY = os.getenv("PUBLIC_DATA_API_KEY")
KAKAO_KEY = os.getenv("KAKAO_REST_API_KEY") # 선장님이 주신 f59f... 사용

async def get_inside_bus_status():
    """부산 BIMS API 실측 데이터만 파싱 (추측 답변 금지)"""
    if not PUBLIC_API_KEY: return "🚨 시스템 설정 오류: 버스 API 키가 없습니다."
    
    url = "http://61.43.246.153/openapi-data/service/busanBIMS/stopArr"
    params = {"serviceKey": PUBLIC_API_KEY, "stopid": "167520101"} # 해양대입구/종점

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params, timeout=5.0)
        
        root = ET.fromstring(res.text)
        items = root.findall(".//item")
        
        if not items: return "🚌 [데이터 확인] 현재 운행 중인 190/88번 버스가 없습니다. (BIMS 실시간 정보 없음)"

        results = ["🚌 [해양대 내부 노선 정밀 정보]"]
        for item in items:
            line_no = item.findtext("lineno")
            if line_no in ['190', '88', '88(A)']:
                min_left = item.findtext("min")
                # 혼잡도: API 원본 데이터만 사용
                cong_map = {"1": "🟢여유", "2": "🟡보통", "3": "🟠혼잡", "4": "🔴매우혼잡"}
                cong_text = cong_map.get(item.findtext("congestion"), "정보없음")
                
                # 잔여 좌석: 숫자 검증 (환각 방지)
                seat_cnt = item.findtext("remain_seat_cnt")
                seat_text = f"{seat_cnt}석" if seat_cnt and seat_cnt.isdigit() and int(seat_cnt) >= 0 else "확인불가"
                
                results.append(f"✅ {line_no}번: {min_left}분 뒤 ({cong_text} | 💺 {seat_text})")
        
        return "\n".join(results) if len(results) > 1 else "🚌 현재 교내 진입 노선의 실시간 정보가 제공되지 않습니다."
    except Exception:
        return "⚠️ 버스 정보 서버 통신 실패 (API 응답 지연)"

async def get_busan_restaurants(query="해양대 맛집"):
    """카카오 로컬 API 기반 실측 장소 정보 (폐업/가짜 정보 차단)"""
    if not KAKAO_KEY: return "🚨 카카오 API 키가 설정되지 않았습니다."
    
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    # 해양대 좌표 고정 (환각 방지: 엉뚱한 지역 검색 차단)
    params = {"query": query, "x": "129.0837", "y": "35.0763", "radius": 2000, "sort": "distance"}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers, params=params, timeout=5.0)
        data = res.json()
        documents = data.get('documents', [])
        
        if not documents: return f"📍 '{query}'에 대한 실제 검색 결과가 주변에 없습니다."

        results = [f"🍴 [아라 추천 '{query}' 실제 정보]"]
        for place in documents[:3]:
            results.append(f"✅ {place['place_name']}\n📍 거리: {place['distance']}m\n🔗 지도: {place['place_url']}")
        return "\n\n".join(results)
    except:
        return "⚠️ 카카오 장소 검색 서비스 일시 장애"