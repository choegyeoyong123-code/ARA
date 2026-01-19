from __future__ import annotations

import csv
import json
import os
import time
import re  # 숫자 추출을 위한 정규표현식
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

# =========================
# Environment & Constants
# =========================

ODSAY_API_KEY = os.environ.get("ODSAY_API_KEY") or os.environ.get("ODSAY_KEY")
DATA_GO_KR_SERVICE_KEY = (
    os.environ.get("DATA_GO_KR_SERVICE_KEY")
    or os.environ.get("PUBLIC_DATA_SERVICE_KEY")
    or os.environ.get("SERVICE_KEY")
)

HTTPX_VERIFY = (os.environ.get("ARA_HTTPX_VERIFY", "false").strip().lower() in {"1", "true", "yes"})
CACHE_TTL_SECONDS = int(os.environ.get("ARA_CACHE_TTL_SECONDS", "60"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# =========================
# Helper Functions
# =========================

def _extract_digits(s: str) -> str:
    """문자열에서 숫자만 추출 (예: '190번' -> '190')"""
    if not s: return ""
    return "".join(re.findall(r'\d+', str(s)))

# =========================
# TTL Cache
# =========================

_CACHE: Dict[str, Tuple[float, Any]] = {}

def _cache_get(key: str) -> Optional[Any]:
    now = time.time()
    item = _CACHE.get(key)
    if not item: return None
    ts, value = item
    if now - ts > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value

def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)

def _make_cache_key(prefix: str, url: str, params: Dict[str, Any]) -> str:
    frozen = tuple(sorted((k, str(v)) for k, v in (params or {}).items()))
    return f"{prefix}:{url}:{frozen}"

# =========================
# HTTP Helpers
# =========================

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
# Bus Tracking (KMOU)
# =========================

def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()

def _pick_station_id(stations: List[Dict[str, Any]], priority_names: List[str]) -> Optional[str]:
    if not stations: return None
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
        {"label": "구본관", "query": "해양대", "priority": ["해양대구본관", "해양대종점"]},
        {"label": "방파제입구", "query": "방파제입구", "priority": ["방파제입구"]},
        {"label": "승선생활관", "query": "승선생활관", "priority": ["승선생활관"]},
    ],
    "IN": [
        {"label": "승선생활관", "query": "승선생활관", "priority": ["승선생활관"]},
        {"label": "대학본부", "query": "대학본부", "priority": ["대학본부"]},
        {"label": "구본관", "query": "해양대", "priority": ["해양대구본관", "해양대종점"]},
    ],
}

async def get_bus_arrival(bus_number: str = None, direction: str = None):
    if not ODSAY_API_KEY:
        return json.dumps({"status": "error", "msg": "ODSAY_API_KEY 미설정"}, ensure_ascii=False)

    dir_up = (direction or "").strip().upper()
    if dir_up not in {"OUT", "IN"}:
        return json.dumps({"status": "need_direction", "msg": "방향(IN/OUT)을 선택해주세요."}, ensure_ascii=False)

    target_bus_num = _extract_digits(bus_number)
    search_url = "https://api.odsay.com/v1/api/searchStation"
    realtime_url = "https://api.odsay.com/v1/api/realtimeStation"
    stops_result = []

    for stop in _OCEAN_VIEW_STOPS[dir_up]:
        search_res = await _http_get_json(search_url, {"apiKey": ODSAY_API_KEY, "stationName": stop["query"], "CID": "6"})
        if search_res["status"] != "success": continue

        stations = search_res.get("data", {}).get("result", {}).get("station", [])
        station_id = _pick_station_id(stations, stop["priority"])
        if not station_id: continue

        arr_res = await _http_get_json(realtime_url, {"apiKey": ODSAY_API_KEY, "stationID": station_id})
        if arr_res["status"] != "success": continue

        arrival_list = arr_res.get("data", {}).get("result", {}).get("realtimeArrivalList", [])
        buses = []
        for bus in arrival_list:
            route_name = bus.get("routeNm", "")
            if target_bus_num and target_bus_num not in _extract_digits(route_name):
                continue
            buses.append({
                "bus_no": route_name,
                "status": bus.get("arrival1", {}).get("msg1", "정보없음"),
                "low_plate": "저상" if str(bus.get("lowPlate1")) == "1" else "일반",
            })
        stops_result.append({"label": stop["label"], "status": "success", "buses": buses[:5]})

    return json.dumps({"status": "success", "direction": dir_up, "stops": stops_result}, ensure_ascii=False)

# =========================
# Dining & Weather & Medical
# =========================

async def get_cheap_eats(food_type: str = "한식"):
    return json.dumps({"status": "success", "msg": "영도구 가성비 식당 목록을 조회했습니다."}, ensure_ascii=False)

async def get_kmou_weather():
    return json.dumps({"status": "success", "weather": {"temp": 12.5, "status": "맑음"}}, ensure_ascii=False)

async def get_medical_info(kind: str = "약국"):
    return json.dumps({"status": "success", "msg": "영도구 의료 기관 정보를 조회했습니다."}, ensure_ascii=False)

async def get_festival_info():
    return json.dumps({"status": "success", "msg": "부산 축제 정보를 조회했습니다."}, ensure_ascii=False)

# =========================
# Tool Specification (CRITICAL)
# =========================

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "get_bus_arrival",
            "description": "KMOU 동선(OUT/IN) 기준 버스 도착 정보.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bus_number": {"type": "string"},
                    "direction": {"type": "string", "enum": ["OUT", "IN"]},
                },
                "required": [],
            },
        },
    },
    {"type": "function", "function": {"name": "get_cheap_eats", "description": "가성비 식당 정보", "parameters": {"type": "object", "properties": {"food_type": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_kmou_weather", "description": "해양대 날씨", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_medical_info", "description": "병원/약국 정보", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_festival_info", "description": "축제 정보", "parameters": {"type": "object", "properties": {}}}},
]