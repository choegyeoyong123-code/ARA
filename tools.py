from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

# =========================
# 환경 변수 설정 (요청 반영)
# =========================

ODSAY_API_KEY = os.environ.get("ODSAY_API_KEY") or os.environ.get("ODSAY_KEY")
DATA_GO_KR_SERVICE_KEY = (
    os.environ.get("DATA_GO_KR_SERVICE_KEY")
    or os.environ.get("PUBLIC_DATA_SERVICE_KEY")
    or os.environ.get("SERVICE_KEY")
)

# 요청하신 교정본과 동일하게 기본 False 고정
HTTPX_VERIFY = False

# 비용 최적화(기존 요구사항)용 간단 캐시
CACHE_TTL_SECONDS = int(os.environ.get("ARA_CACHE_TTL_SECONDS", "60"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# =========================
# 공통 유틸
# =========================

def _extract_digits(s: str) -> str:
    """오타/접미사(190qjs, 190번 등)에서 숫자만 추출"""
    if not s:
        return ""
    return "".join(re.findall(r"\d+", str(s)))

def _safe_get(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur

_CACHE: Dict[str, Tuple[float, Any]] = {}

def _make_cache_key(prefix: str, url: str, params: Dict[str, Any]) -> str:
    frozen = tuple(sorted((k, str(v)) for k, v in (params or {}).items()))
    return f"{prefix}:{url}:{frozen}"

def _cache_get(key: str) -> Optional[Any]:
    now = time.time()
    item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if now - ts > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value

def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)

async def _http_get_json(url: str, params: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
    cache_key = _make_cache_key("GETJSON", url, params)
    cached = _cache_get(cache_key)
    if cached is not None:
        return {"status": "success", "data": cached, "cached": True}

    try:
        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
            res = await client.get(url, params=params, timeout=timeout)
        res.raise_for_status()
        data = res.json()
        _cache_set(cache_key, data)
        return {"status": "success", "data": data, "cached": False}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# =========================
# 1) 날씨 정보 실시간 연동 (기상청 API) — 요청 교정본 반영
# =========================

async def get_kmou_weather():
    """한국해양대(영도구 동삼동) 실시간 기상 실황 조회"""
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps({"status": "error", "msg": "기상청 API 키가 없습니다."}, ensure_ascii=False)

    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    now = datetime.now()
    base_date = now.strftime("%Y%m%d")
    base_time_primary = now.strftime("%H00") if now.minute < 35 else now.strftime("%H30")

    # 안정성: 기본 교정 로직(00/30) + 실패 시 전 시각(HH00) fallback
    candidates: List[Tuple[str, str]] = [(base_date, base_time_primary)]
    if base_time_primary.endswith("30"):
        candidates.append((base_date, now.strftime("%H00")))
    # 전 1시간 HH00 fallback
    prev = now - timedelta(hours=1)
    candidates.append((prev.strftime("%Y%m%d"), prev.strftime("%H00")))

    last_error: Optional[str] = None
    for cand_date, cand_time in candidates:
        params = {
            "serviceKey": DATA_GO_KR_SERVICE_KEY,
            "pageNo": "1",
            "numOfRows": "10",
            "dataType": "JSON",
            "base_date": cand_date,
            "base_time": cand_time,
            "nx": "98",
            "ny": "75",
        }

        try:
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
                res = await client.get(url, params=params, timeout=10.0)
                data = res.json()

            # 응답 구조 fail-safe
            code = _safe_get(data, "response", "header", "resultCode", default=None)
            if code and code not in {"00", "0"}:
                last_error = _safe_get(data, "response", "header", "resultMsg", default="API 오류")
                continue

            items = _safe_get(data, "response", "body", "items", "item", default=[])
            if not isinstance(items, list) or not items:
                last_error = "날씨 raw data가 비어 있습니다."
                continue

            weather_info: Dict[str, Any] = {}
            for item in items:
                if item.get("category") == "T1H":
                    weather_info["temp"] = item.get("obsrValue")
                if item.get("category") == "PTY":
                    weather_info["state"] = item.get("obsrValue")

            return json.dumps(
                {
                    "status": "success",
                    "weather": {
                        "temp": f"{weather_info.get('temp', 'N/A')}°C",
                        "location": "영도구 동삼동(해양대)",
                        "date": cand_date,
                        "time": cand_time,
                        # raw data 일부를 함께 포함(숫자 근거 제공)
                        "raw": weather_info,
                    },
                },
                ensure_ascii=False,
            )
        except Exception as e:
            last_error = str(e)
            continue

    return json.dumps({"status": "error", "msg": f"날씨 조회 실패: {last_error or 'unknown'}"}, ensure_ascii=False)

# =========================
# 2) 버스 필터링 로직 최적화 (ODsay) — 요청 교정본 반영
# =========================

def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()

def _pick_station_id(stations: List[Dict[str, Any]], priority_names: List[str]) -> Optional[str]:
    if not stations:
        return None
    pri_norm = [_norm(p) for p in priority_names]
    for pnorm in pri_norm:
        for st in stations:
            if _norm(st.get("stationName", "")) == pnorm:
                return st.get("stationID")
    for pnorm in pri_norm:
        for st in stations:
            if pnorm and pnorm in _norm(st.get("stationName", "")):
                return st.get("stationID")
    return stations[0].get("stationID")

_OCEAN_VIEW_STOPS: Dict[str, List[Dict[str, Any]]] = {
    "OUT": [
        {
            "label": "구본관",
            "query": "해양대",
            "priority": ["해양대구본관", "해양대 구본관", "Haeyangdae Old Main Bldg", "한국해양대학교", "한국해양대", "해양대종점"],
        },
        {"label": "방파제입구", "query": "방파제입구", "priority": ["방파제입구", "방파제 입구"]},
        {"label": "승선생활관", "query": "승선생활관", "priority": ["승선생활관"]},
    ],
    "IN": [
        {"label": "승선생활관", "query": "승선생활관", "priority": ["승선생활관"]},
        {"label": "대학본부", "query": "대학본부", "priority": ["대학본부"]},
        {
            "label": "구본관",
            "query": "해양대",
            "priority": ["해양대구본관", "해양대 구본관", "Haeyangdae Old Main Bldg", "한국해양대학교", "한국해양대", "해양대종점"],
        },
    ],
}

async def get_bus_arrival(bus_number: str = None, direction: str = None):
    if not ODSAY_API_KEY:
        return json.dumps({"status": "error", "msg": "ODSAY_API_KEY 미설정"}, ensure_ascii=False)

    dir_up = (direction or "").strip().upper()
    if dir_up not in {"OUT", "IN"}:
        return json.dumps(
            {
                "status": "need_direction",
                "msg": "버스 동선을 선택해 주세요: OUT(진출) 또는 IN(진입).",
                "ocean_view": {"OUT": ["구본관", "방파제입구", "승선생활관"], "IN": ["승선생활관", "대학본부", "구본관"]},
            },
            ensure_ascii=False,
        )

    # 요청 교정본: 기본값 190
    target_bus_num = _extract_digits(bus_number) if bus_number else "190"

    search_url = "https://api.odsay.com/v1/api/searchStation"
    realtime_url = "https://api.odsay.com/v1/api/realtimeStation"

    stops_result: List[Dict[str, Any]] = []
    any_arrivals = False
    any_unfiltered = False
    suggestions: List[Dict[str, Any]] = []

    for stop in _OCEAN_VIEW_STOPS[dir_up]:
        search_res = await _http_get_json(
            search_url,
            {"apiKey": ODSAY_API_KEY, "stationName": stop["query"], "CID": "6"},
            timeout=10.0,
        )
        if search_res["status"] != "success":
            stops_result.append({"label": stop["label"], "status": "error", "msg": search_res.get("msg", "정류장 검색 실패")})
            continue

        stations = _safe_get(search_res, "data", "result", "station", default=[]) or []
        station_id = _pick_station_id(stations, stop["priority"])
        if not station_id:
            stops_result.append({"label": stop["label"], "status": "station_not_found", "msg": "정류장을 찾을 수 없습니다."})
            continue

        arr_res = await _http_get_json(realtime_url, {"apiKey": ODSAY_API_KEY, "stationID": station_id}, timeout=10.0)
        if arr_res["status"] != "success":
            stops_result.append({"label": stop["label"], "station_id": station_id, "status": "error", "msg": arr_res.get("msg", "도착 정보 조회 실패")})
            continue

        arrival_list = _safe_get(arr_res, "data", "result", "realtimeArrivalList", default=[]) or []
        unfiltered_buses: List[Dict[str, Any]] = []
        filtered_buses: List[Dict[str, Any]] = []

        for bus in arrival_list:
            route_name = bus.get("routeNm", "")
            entry = {
                "bus_no": route_name,
                "status": _safe_get(bus, "arrival1", "msg1", default="정보없음"),
                "low_plate": "저상" if str(bus.get("lowPlate1")) == "1" else "일반",
            }
            unfiltered_buses.append(entry)

            # 요청 교정본: 숫자만 추출해 contains 비교
            route_digits = _extract_digits(route_name)
            if target_bus_num and target_bus_num not in route_digits:
                continue
            filtered_buses.append(entry)

        if unfiltered_buses:
            any_unfiltered = True
        if filtered_buses:
            any_arrivals = True

        if not filtered_buses and unfiltered_buses:
            suggestions.append({"label": stop["label"], "buses": unfiltered_buses[:3]})

        stops_result.append(
            {
                "label": stop["label"],
                "station_id": station_id,
                "status": "success",
                "buses": filtered_buses[:5],
            }
        )

    if not any_arrivals and any_unfiltered:
        return json.dumps(
            {
                "status": "fallback",
                "direction": dir_up,
                "bus_number": target_bus_num,
                "msg": "요청하신 버스 번호로는 도착 정보를 찾지 못했습니다. 동일 정류장의 근접 도착 정보를 함께 제공합니다.",
                "stops": stops_result,
                "suggestions": suggestions,
            },
            ensure_ascii=False,
        )

    if not any_arrivals:
        return json.dumps(
            {
                "status": "empty",
                "direction": dir_up,
                "bus_number": target_bus_num,
                "msg": "현재 도착 정보를 확인할 수 없습니다(도착 목록이 비어있음).",
                "stops": stops_result,
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {"status": "success", "direction": dir_up, "bus_number": target_bus_num, "stops": stops_result},
        ensure_ascii=False,
    )

# =========================
# 3) 맛집/의료/축제 (기존 기능 유지)
# =========================

def _read_places_csv(limit: int = 5) -> List[Dict[str, str]]:
    path = os.path.join(os.path.dirname(__file__), "places.csv")
    if not os.path.exists(path):
        return []

    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for idx, row in enumerate(reader):
            if not row:
                continue
            if idx == 0 and row[0].strip().lower().startswith("git merge"):
                continue
            name = (row[0] if len(row) > 0 else "").strip()
            category = (row[1] if len(row) > 1 else "").strip()
            description = (row[2] if len(row) > 2 else "").strip()
            recommendation = (row[3] if len(row) > 3 else "").strip()
            if not name:
                continue
            rows.append({"name": name, "category": category, "description": description, "recommendation": recommendation})
            if len(rows) >= limit:
                break
    return rows

async def get_cheap_eats(food_type: str = "한식"):
    """
    영도 착한가격/가성비 식당 조회
    - DATA_GO_KR_SERVICE_KEY 없으면 places.csv로 제한 안내
    """
    if not DATA_GO_KR_SERVICE_KEY:
        places = _read_places_csv(limit=5)
        if not places:
            return json.dumps({"status": "error", "msg": "공공데이터 API 키 및 로컬 데이터가 없어 조회할 수 없습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "source": "local_csv", "restaurants": places}, ensure_ascii=False)

    url = "http://apis.data.go.kr/6260000/GoodPriceStoreService/getGoodPriceStore"
    params = {"serviceKey": DATA_GO_KR_SERVICE_KEY, "numOfRows": "100", "pageNo": "1", "resultType": "json"}
    res = await _http_get_json(url, params, timeout=15.0)
    if res["status"] != "success":
        return json.dumps({"status": "error", "msg": res.get("msg", "API 호출 실패")}, ensure_ascii=False)

    try:
        items = _safe_get(res, "data", "getGoodPriceStore", "item", default=[]) or []
        targets = []
        for i in items:
            if "영도구" in (i.get("addr", "") or "") and (food_type in (i.get("induty", "") or "")):
                targets.append(
                    {
                        "name": i.get("sj"),
                        "menu": i.get("menu"),
                        "price": i.get("price"),
                        "tel": i.get("tel"),
                        "addr": i.get("addr"),
                        "desc": i.get("cn", ""),
                    }
                )
        if not targets:
            return json.dumps({"status": "empty", "msg": "조건에 맞는 식당 정보를 찾지 못했습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "source": "public_api", "restaurants": targets[:5]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

async def get_medical_info(kind: str = "약국"):
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps({"status": "error", "msg": "공공데이터 API 키가 없어 조회할 수 없습니다."}, ensure_ascii=False)

    url = "http://apis.data.go.kr/6260000/MedicInstitService/MedicalInstitInfo"
    params = {"serviceKey": DATA_GO_KR_SERVICE_KEY, "numOfRows": "100", "pageNo": "1", "resultType": "json"}
    res = await _http_get_json(url, params, timeout=15.0)
    if res["status"] != "success":
        return json.dumps({"status": "error", "msg": res.get("msg", "API 호출 실패")}, ensure_ascii=False)

    try:
        items = _safe_get(res, "data", "MedicalInstitInfo", "item", default=[]) or []
        targets = []
        for i in items:
            addr = i.get("addr", "") or ""
            instit_kind = i.get("instit_kind", "") or ""
            if "영도구" in addr and kind in instit_kind:
                targets.append({"name": i.get("instit_nm"), "tel": i.get("tel"), "addr": addr, "time": i.get("trtm_mon_end")})
        if not targets:
            return json.dumps({"status": "empty", "msg": "조건에 맞는 의료 기관 정보를 찾지 못했습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

async def get_festival_info():
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps({"status": "error", "msg": "공공데이터 API 키가 없어 조회할 수 없습니다."}, ensure_ascii=False)

    url = "http://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": DATA_GO_KR_SERVICE_KEY, "numOfRows": "10", "pageNo": "1", "resultType": "json"}
    res = await _http_get_json(url, params, timeout=15.0)
    if res["status"] != "success":
        return json.dumps({"status": "error", "msg": res.get("msg", "API 호출 실패")}, ensure_ascii=False)

    try:
        items = _safe_get(res, "data", "getFestivalKr", "item", default=[]) or []
        targets = []
        for i in items:
            targets.append({"title": i.get("MAIN_TITLE"), "place": i.get("MAIN_PLACE"), "date": i.get("USAGE_DAY_WEEK_AND_TIME")})
        if not targets:
            return json.dumps({"status": "empty", "msg": "조회 가능한 축제 정보가 없습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "festivals": targets[:5]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# =========================
# Tool Specification (CRITICAL)
# =========================

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "get_bus_arrival",
            "description": "🚌 190번(학교행): '190번 버스 IN' / 190번(역·대교행): '190번 버스 OUT' 형태로 버스 도착 정보를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bus_number": {"type": "string", "description": "예: 190, 101 등(미입력 시 190 기본값)"},
                    "direction": {"type": "string", "enum": ["IN", "OUT"], "description": "IN(진입) 또는 OUT(진출)"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kmou_weather",
            "description": "🌤️ 해양대 날씨: '지금 학교 날씨 어때?' 형태로 실시간 기상 실황을 조회합니다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cheap_eats",
            "description": "🍚 가성비 맛집: '영도 착한가격 식당 추천해줘' 형태로 착한가격/가성비 식당을 추천합니다.",
            "parameters": {"type": "object", "properties": {"food_type": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_medical_info",
            "description": "🏥 약국/병원: '학교 근처 약국이나 병원 알려줘' 형태로 의료 기관 정보를 조회합니다.",
            "parameters": {"type": "object", "properties": {"kind": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_festival_info",
            "description": "🎉 축제/행사: '지금 부산에 하는 축제 있어?' 형태로 부산 축제 정보를 조회합니다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]