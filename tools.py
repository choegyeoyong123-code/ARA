from __future__ import annotations

import csv
import json
import os
import re
import time
import asyncio
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

import httpx

# =========================
# 환경 변수 설정 (요청 반영)
# =========================

ENV_MODE = (os.environ.get("ENV_MODE") or "prod").strip().lower()
ARA_REF_DATE = (os.environ.get("ARA_REF_DATE") or "20260120").strip()
ARA_REF_TIME = (os.environ.get("ARA_REF_TIME") or "0630").strip()

ODSAY_API_KEY = os.environ.get("ODSAY_API_KEY") or os.environ.get("ODSAY_KEY")
DATA_GO_KR_SERVICE_KEY = (
    os.environ.get("DATA_GO_KR_SERVICE_KEY")
    or os.environ.get("PUBLIC_DATA_SERVICE_KEY")
    or os.environ.get("SERVICE_KEY")
)

# SSL 보안 강화: 운영 기본 True, 개발(dev)에서만 False 허용
# - 로컬에서 인증서 문제가 발생하는 경우에만 dev 모드로 사용하세요.
HTTPX_VERIFY = False if ENV_MODE == "dev" else True

# 비용 최적화(기존 요구사항)용 간단 캐시
CACHE_TTL_SECONDS = int(os.environ.get("ARA_CACHE_TTL_SECONDS", "60"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# =========================
# 공통 유틸
# =========================

def _reference_datetime() -> datetime:
    """
    데이터 무결성 기준 시각(요청 반영)
    - 기본: 2026-01-20 06:30 (ARA_REF_DATE/ARA_REF_TIME로 오버라이드 가능)
    """
    d = re.sub(r"\D+", "", ARA_REF_DATE)
    t = re.sub(r"\D+", "", ARA_REF_TIME)
    if len(d) != 8:
        d = "20260120"
    if len(t) not in (3, 4):
        t = "0630"
    if len(t) == 3:
        t = "0" + t
    try:
        return datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]), int(t[0:2]), int(t[2:4]))
    except Exception:
        return datetime(2026, 1, 20, 6, 30)

def _ref_date_floor_20260120() -> str:
    """base_date는 최소 20260120을 보장합니다."""
    ref = _reference_datetime().strftime("%Y%m%d")
    return "20260120" if ref < "20260120" else ref

def _extract_ymd(date_text: str) -> Optional[datetime]:
    """문자열에서 YYYYMMDD(또는 YYYY-MM-DD/YY년MM월DD일 등) 추출. 불확실하면 None."""
    if not date_text:
        return None
    s = str(date_text)
    m = re.search(r"(?P<y>20\d{2})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})", s)
    if not m:
        m = re.search(r"(?P<y>20\d{2})\s*(?P<m>\d{2})\s*(?P<d>\d{2})", re.sub(r"\D+", "", s))
    if not m:
        return None
    try:
        return datetime(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except Exception:
        return None

def _parse_hours_range(s: str) -> Optional[Tuple[int, int]]:
    """
    '09:00~18:00' -> (540, 1080) 분 단위.
    불확실하면 None.
    """
    if not s:
        return None
    m = re.search(r"(?P<sh>\d{1,2})\s*:\s*(?P<sm>\d{2})\s*~\s*(?P<eh>\d{1,2})\s*:\s*(?P<em>\d{2})", str(s))
    if not m:
        return None
    try:
        sh, sm, eh, em = int(m.group("sh")), int(m.group("sm")), int(m.group("eh")), int(m.group("em"))
        return (sh * 60 + sm, eh * 60 + em)
    except Exception:
        return None

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

def _strip_html(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", str(s)).strip()

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

async def _http_get_json(
    url: str,
    params: Dict[str, Any],
    timeout: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    cache_key = _make_cache_key("GETJSON", url, params)
    cached = _cache_get(cache_key)
    if cached is not None:
        return {"status": "success", "data": cached, "cached": True}

    try:
        if client is None:
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as _client:
                res = await _client.get(url, params=params, timeout=timeout)
        else:
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
    # 데이터 무결성: 기준일/시간(기본 2026-01-20 06:30)을 사용
    now = _reference_datetime()
    base_date = _ref_date_floor_20260120()
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
    # 런타임 기준으로 다시 읽어, 로드 순서/리로드 영향 최소화
    runtime_key = os.environ.get("ODSAY_API_KEY") or os.environ.get("ODSAY_KEY") or ODSAY_API_KEY

    if not runtime_key:
        return json.dumps({"status": "error", "msg": "죄송합니다. ODSAY_API_KEY가 설정되지 않아 버스 정보를 조회할 수 없습니다."}, ensure_ascii=False)

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

    realtime_url = "https://api.odsay.com/v1/api/realtimeStation"

    # 정류장 ID 정밀 매칭(요청 반영): 방향(IN/OUT)에 따라 정류장ID를 강제 사용
    # - IN(학교행): 03058 (한국해양대학교본관)
    # - OUT(진출행): 03053 (해양대입구 - 영도대교 방면)
    station_id = "03058" if dir_up == "IN" else "03053"
    label = "한국해양대학교본관" if dir_up == "IN" else "해양대입구(영도대교 방면)"

    # 카카오 5초 제한 대응: ODsay 호출은 짧은 타임아웃을 기본 적용
    odsay_timeout = float(os.environ.get("ARA_ODSAY_TIMEOUT_SECONDS", "2.5"))
    arr_res = await _http_get_json(realtime_url, {"apiKey": runtime_key, "stationID": station_id}, timeout=odsay_timeout)
    if arr_res["status"] != "success":
        return json.dumps(
            {
                "status": "error",
                "msg": "현재 2026-01-20 실시간 버스 정보가 서버에서 응답하지 않습니다",
                "direction": dir_up,
                "bus_number": target_bus_num,
                "station_id": station_id,
            },
            ensure_ascii=False,
        )

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
        if target_bus_num and target_bus_num not in _extract_digits(route_name):
            continue
        filtered_buses.append(entry)

    # 조회 자체는 되었으나, 필터 결과가 비어 있으면(또는 전체도 비어 있으면) 추측 없이 정직하게 보고
    if not unfiltered_buses:
        return json.dumps(
            {
                "status": "error",
                "msg": "현재 2026-01-20 실시간 버스 정보가 서버에서 응답하지 않습니다",
                "direction": dir_up,
                "bus_number": target_bus_num,
                "station_id": station_id,
            },
            ensure_ascii=False,
        )

    if target_bus_num and not filtered_buses:
        return json.dumps(
            {
                "status": "fallback",
                "direction": dir_up,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "msg": "요청하신 버스 번호로는 도착 정보를 찾지 못했습니다. 동일 정류장의 근접 도착 정보를 함께 제공합니다.",
                "stops": [{"label": label, "station_id": station_id, "status": "success", "buses": []}],
                "suggestions": [{"label": label, "buses": unfiltered_buses[:3]}],
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "status": "success",
            "direction": dir_up,
            "bus_number": target_bus_num,
            "stops": [{"label": label, "station_id": station_id, "status": "success", "buses": filtered_buses[:5]}],
        },
        ensure_ascii=False,
    )

    async def _fetch_stop(stop: Dict[str, Any], client: httpx.AsyncClient) -> Dict[str, Any]:
        search_res = await _http_get_json(
            search_url,
            {"apiKey": runtime_key, "stationName": stop["query"], "CID": "6"},
            timeout=10.0,
            client=client,
        )
        if search_res["status"] != "success":
            return {"label": stop["label"], "status": "error", "msg": search_res.get("msg", "정류장 검색 실패"), "_any_unfiltered": False, "_any_arrivals": False}

        stations = _safe_get(search_res, "data", "result", "station", default=[]) or []
        station_id = _pick_station_id(stations, stop["priority"])
        if not station_id:
            return {"label": stop["label"], "status": "station_not_found", "msg": "정류장을 찾을 수 없습니다.", "_any_unfiltered": False, "_any_arrivals": False}

        arr_res = await _http_get_json(realtime_url, {"apiKey": runtime_key, "stationID": station_id}, timeout=10.0, client=client)
        if arr_res["status"] != "success":
            return {
                "label": stop["label"],
                "station_id": station_id,
                "status": "error",
                "msg": arr_res.get("msg", "도착 정보 조회 실패"),
                "_any_unfiltered": False,
                "_any_arrivals": False,
            }

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

        result = {
            "label": stop["label"],
            "station_id": station_id,
            "status": "success",
            "buses": filtered_buses[:5],
            "_any_unfiltered": bool(unfiltered_buses),
            "_any_arrivals": bool(filtered_buses),
            "_suggestion": {"label": stop["label"], "buses": unfiltered_buses[:3]} if (not filtered_buses and unfiltered_buses) else None,
        }
        return result

    any_arrivals = False
    any_unfiltered = False
    async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
        tasks = [_fetch_stop(stop, client) for stop in _OCEAN_VIEW_STOPS[dir_up]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, r in enumerate(results):
        if isinstance(r, Exception):
            stop = _OCEAN_VIEW_STOPS[dir_up][idx]
            stops_result.append({"label": stop["label"], "status": "error", "msg": str(r)})
            continue
        any_arrivals = any_arrivals or bool(r.pop("_any_arrivals", False))
        any_unfiltered = any_unfiltered or bool(r.pop("_any_unfiltered", False))
        sugg = r.pop("_suggestion", None)
        if sugg:
            suggestions.append(sugg)
        stops_result.append(r)

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
        # API 응답 구조 fail-safe (일부는 response.body.items.item 형태)
        items = _safe_get(res, "data", "getGoodPriceStore", "item", default=None)
        if not items:
            items = _safe_get(res, "data", "response", "body", "items", "item", default=[]) or []
        if isinstance(items, dict):
            items = [items]
        targets = []
        for i in items:
            addr = (i.get("adres") or i.get("addr") or "").strip()
            if "영도" not in addr:
                continue

            # food_type은 데이터 필드가 일정치 않아 보수적으로 적용(비어있으면 필터 생략)
            if food_type:
                blob = " ".join(
                    [
                        str(i.get("cn", "") or ""),
                        str(i.get("mNm", "") or ""),
                        str(i.get("sj", "") or ""),
                        _strip_html(i.get("intrcn", "") or ""),
                    ]
                )
                if food_type not in blob:
                    continue

            targets.append(
                {
                    "name": (i.get("sj") or "").strip(),
                    "addr": addr,
                    "tel": (i.get("tel") or "").strip(),
                    "time": (i.get("bsnTime") or "").strip(),
                    "desc": _strip_html(i.get("intrcn", "") or ""),
                }
            )
        if not targets:
            # 공공데이터에서 영도권 결과가 없으면 로컬 CSV로 graceful fallback
            places = _read_places_csv(limit=5)
            if places:
                return json.dumps(
                    {
                        "status": "success",
                        "source": "local_csv_fallback",
                        "msg": "공공데이터에서 영도구 착한가격 식당을 충분히 확인하지 못해, 로컬 추천 목록으로 안내드립니다.",
                        "restaurants": places,
                    },
                    ensure_ascii=False,
                )
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
        # API 응답 구조 fail-safe (일부는 response.body.items.item 형태)
        items = _safe_get(res, "data", "MedicalInstitInfo", "item", default=None)
        if not items:
            items = _safe_get(res, "data", "response", "body", "items", "item", default=[]) or []
        if isinstance(items, dict):
            items = [items]
        ref_dt = _reference_datetime()
        weekday_field = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][ref_dt.weekday()]
        ref_minutes = ref_dt.hour * 60 + ref_dt.minute

        targets = []
        for i in items:
            addr = (i.get("street_nm_addr") or i.get("organ_loc") or i.get("addr") or "").strip()
            instit_kind = (i.get("instit_kind") or i.get("medical_instit_kind") or "").strip()
            if "영도구" not in addr:
                continue
            if kind and kind not in instit_kind:
                continue
            hours_str = (i.get(weekday_field) or i.get("monday") or "").strip()
            rng = _parse_hours_range(hours_str)
            is_open = False
            if rng:
                start_m, end_m = rng
                is_open = (start_m <= ref_minutes <= end_m)

            targets.append(
                {
                    "name": (i.get("instit_nm") or "").strip(),
                    "kind": instit_kind,
                    "tel": (i.get("tel") or "").strip(),
                    "addr": addr,
                    # 대표 운영시간으로 monday를 우선 사용(원문 문자열만 그대로 사용)
                    "time": hours_str or (i.get("monday") or "").strip(),
                    "is_open": bool(is_open),
                }
            )
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
            title = i.get("MAIN_TITLE")
            place = i.get("MAIN_PLACE")
            date_text = i.get("USAGE_DAY_WEEK_AND_TIME")

            # 2026 데이터 무결성: 2026-01-20 이후 일정만 통과, 불확실하면 폐기
            dt = _extract_ymd(str(date_text or ""))
            if not dt:
                continue
            if dt.strftime("%Y%m%d") < "20260120":
                continue
            targets.append({"title": title, "place": place, "date": date_text, "date_ymd": dt.strftime("%Y%m%d")})
        if not targets:
            return json.dumps({"status": "empty", "msg": "2026-01-20 이후의 확정 일정만 제공할 수 있습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "festivals": targets[:5]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

# =========================
# 4) 셔틀/캠퍼스맵 (이미지 기반 기능 추가)
# =========================

def get_current_season(today: Optional[date] = None) -> str:
    """
    SeasonDetector (요청 반영)
    - Winter Vacation: ~ 2026-02-28 (inclusive)
    - Spring Semester(1st): 2026-03-02 ~ 2026-06-21 (inclusive)
    """
    d = today or date.today()
    if d <= date(2026, 2, 28):
        return "VACATION"
    if date(2026, 3, 2) <= d <= date(2026, 6, 21):
        return "SEMESTER"
    # 범위 외에는 가장 보수적으로 학기중으로 간주(요청: 3/2 이후 자동 전환)
    if d >= date(2026, 3, 2):
        return "SEMESTER"
    return "VACATION"

def _hhmm_to_minutes(hhmm: str) -> Optional[int]:
    if not hhmm:
        return None
    s = re.sub(r"\s+", "", str(hhmm))
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        m = re.match(r"^(\d{3,4})$", re.sub(r"\D+", "", s))
        if not m:
            return None
        digits = m.group(1).zfill(4)
        h, mi = int(digits[:2]), int(digits[2:])
    else:
        h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 60 + mi

def _minutes_to_hhmm(m: int) -> str:
    h = m // 60
    mi = m % 60
    return f"{h:02d}:{mi:02d}"

_SHUTTLE_SEMESTER: Dict[str, List[str]] = {
    "1-1": ["08:15", "09:00", "18:10"],
    "2-1": ["08:00", "08:55", "11:00", "13:00", "16:10", "18:10"],
}

def _shuttle_3_1_semester_times() -> List[str]:
    # 08:00 ~ 21:30, 20분 간격
    start = 8 * 60
    end = 21 * 60 + 30
    return [_minutes_to_hhmm(m) for m in range(start, end + 1, 20)]

_SHUTTLE_VACATION: Dict[str, Optional[List[str]]] = {
    "1-1": None,  # 방학중 미운행
    "2-1": None,  # 방학중 미운행
    "3-1": [
        "08:00", "08:30", "09:00", "09:30", "10:00",
        "11:00", "11:30", "12:00", "12:30",
        "14:00", "14:30", "15:00", "15:30",
        "16:00", "16:30", "17:00",
        "18:10", "18:30", "19:00", "20:00", "21:00",
    ],
}

_SHUTTLE_NOTICE = "주말 및 법정 공휴일 운행 없음"

# 이미지(시간표) 하단 텍스트 기반 노선 안내
_SHUTTLE_ROUTE_BASE = (
    "학내 출발점(해사대학관 앞) → 공과대학 1호관 앞 → 승선생활관 입구 → 릴랙스게이트 → 태종대 과일가게 앞 → 신흥하리상가 → "
    "릴랙스게이트 → 승선생활관 입구 → 학내진입시 앵커탑 앞 좌회전(실습선 부두 방면) → 공대 1호관 후문 → 어울림관 → 학내 종점(해사대학관 앞)"
)
_SHUTTLE_ROUTE_MARKET = (
    "학교 출발 12:40, 14:00, 18:10, 20:30 / 학내 종점(해사대학관 앞) → 공과대학 1호관 앞 → 승선생활관 입구 → 릴랙스게이트 → "
    "롯데리아영도점 맞은편 버스 정류장 → 동삼시장 → 동삼시민공원입구(매물녀5번 정류장) → 태종대 과일가게 앞 → 신흥하리상가 → "
    "릴랙스게이트 → 승선생활관 입구 → 학내진입시 앵커탑 앞에서 좌회전(실습선 부두 방면) → 공대 1호관 후문 → 어울림관 → 학내 종점(해사대학관 앞)"
)

async def get_shuttle_next_buses(limit: int = 3, now_hhmm: Optional[str] = None, date_yyyymmdd: Optional[str] = None):
    """셔틀 다음 N회 출발(시즌 자동 전환 + 실시간 필터)"""
    # 기준 시각(시스템 시계)
    now_dt = datetime.now()
    if date_yyyymmdd:
        digits = re.sub(r"\D+", "", str(date_yyyymmdd))
        if len(digits) == 8:
            try:
                now_dt = datetime(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), now_dt.hour, now_dt.minute)
            except Exception:
                pass
    if now_hhmm:
        mm = _hhmm_to_minutes(now_hhmm)
        if mm is not None:
            now_dt = now_dt.replace(hour=mm // 60, minute=mm % 60, second=0, microsecond=0)

    season = get_current_season(now_dt.date())
    is_weekend = now_dt.weekday() >= 5
    if is_weekend:
        return json.dumps(
            {
                "status": "no_service",
                "season": season,
                "msg": _SHUTTLE_NOTICE,
                "next": [],
                "route_base": _SHUTTLE_ROUTE_BASE,
                "route_market": _SHUTTLE_ROUTE_MARKET,
            },
            ensure_ascii=False,
        )

    cur_min = now_dt.hour * 60 + now_dt.minute

    departures: List[Tuple[int, str]] = []
    inactive: List[str] = []

    if season == "VACATION":
        schedule = _SHUTTLE_VACATION
        if schedule.get("1-1") is None:
            inactive.append("1-1")
        if schedule.get("2-1") is None:
            inactive.append("2-1")
        times_3 = schedule.get("3-1") or []
        for t in times_3:
            m = _hhmm_to_minutes(t)
            if m is not None:
                departures.append((m, "3-1 하리전용"))
    else:
        schedule = dict(_SHUTTLE_SEMESTER)
        # 3-1 학기중 20분 간격
        schedule["3-1"] = _shuttle_3_1_semester_times()
        for bus_id, times in schedule.items():
            for t in times:
                m = _hhmm_to_minutes(t)
                if m is not None:
                    label = bus_id if bus_id in {"1-1", "2-1"} else "3-1 하리전용"
                    departures.append((m, label))

    departures = sorted([d for d in departures if d[0] >= cur_min], key=lambda x: x[0])
    picked = departures[: max(0, int(limit))]

    if not picked:
        return json.dumps(
            {
                "status": "ended",
                "season": season,
                "msg": "오늘 운행이 종료되었습니다.",
                "next": [],
                "inactive": inactive,
                "route_base": _SHUTTLE_ROUTE_BASE,
                "route_market": _SHUTTLE_ROUTE_MARKET,
                "notice": _SHUTTLE_NOTICE,
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "status": "success",
            "season": season,
            "now": now_dt.strftime("%Y-%m-%d %H:%M"),
            "inactive": inactive,
            "next": [{"bus": bus, "time": _minutes_to_hhmm(m)} for m, bus in picked],
            "route_base": _SHUTTLE_ROUTE_BASE,
            "route_market": _SHUTTLE_ROUTE_MARKET,
            "notice": _SHUTTLE_NOTICE,
        },
        ensure_ascii=False,
    )

_KMOU_CAMPUS_MAP: Dict[str, Dict[str, str]] = {
    "A1": {"kr": "공학2관", "en": "College of Engineering ll"},
    "A2": {"kr": "해양인문사회과학대학관", "en": "College of Maritime Humanities & Social Sciences"},
    "A3": {"kr": "대학본부", "en": "University Administration"},
    "A4": {"kr": "종합연구관", "en": "Research Complex"},
    "A5": {"kr": "레포츠센터", "en": "Leisure &amp; Sports Center"},
    "A6": {"kr": "아산관", "en": "Asan Hall"},
    "A7": {"kr": "케미컬탱커 훈련센터", "en": "Chemical Tanker Training Center"},
    "A8": {"kr": "체육관", "en": "Gymnasium"},
    "A9": {"kr": "50주년 기념관", "en": "Half-Century Memorial Hall"},
    "AP1": {"kr": "중앙로", "en": "Center Street"},
    "AP2": {"kr": "중앙광장", "en": "Central Square"},
    "AP3": {"kr": "스포츠존", "en": "Sports Zone"},
    "AP4": {"kr": "테니스코트", "en": "Tennis Court"},
    "AP5": {"kr": "남해안로", "en": "South Shore Road"},
    "B1": {"kr": "공학1관", "en": "College of Engineering I"},
    "B2": {"kr": "어울림관", "en": "Oullim Hall"},
    "B3": {"kr": "도서관", "en": "Library"},
    "B4": {"kr": "미디어홀", "en": "Media Hall"},
    "B5": {"kr": "한바다호", "en": "T/S Hanbada"},
    "B6": {"kr": "한나라호", "en": "T/S Hannara"},
    "BP1": {"kr": "해상교육장", "en": "Marine Education Area"},
    "BP2": {"kr": "실습선부두", "en": "Wharf for Training Ships"},
    "BP3": {"kr": "어울림쉼터", "en": "Oullim Park"},
    "BP4": {"kr": "중앙공원", "en": "Central Park"},
    "C1": {"kr": "해사대학관", "en": "College of Maritime Sciences"},
    "C2": {"kr": "평생교육관", "en": "Lifelong Education Center"},
    "C4": {"kr": "예섬관", "en": "Student Union Hall I"},
    "C5": {"kr": "다솜관", "en": "Student Union Hall II"},
    "C6": {"kr": "해사대학 신관", "en": "College of Maritime Sciences"},
    "CP1": {"kr": "아치잔디공원", "en": "A-chi Green Park"},
    "CP2": {"kr": "아치뜰", "en": "A-chi Garden"},
    "CP3": {"kr": "아치해변", "en": "A-chi Beach"},
    "D1": {"kr": "해양과학기술관", "en": "College of Ocean Science Technology"},
    "D2": {"kr": "보트보관실", "en": "Boat Storage"},
    "D3": {"kr": "반도체실험동", "en": "Semiconductor Laboratory"},
    "D4": {"kr": "시설서비스센터", "en": "United Maintenance Offices"},
    "D5": {"kr": "대강당", "en": "Grand Auditorium"},
    "D6": {"kr": "아라관", "en": "Ara Hall"},
    "D7": {"kr": "공동실험관", "en": "Joint Laboratory Building"},
    "D8": {"kr": "국제교류협력관", "en": "International Exchange &amp; Cooperation Center"},
    "DP1": {"kr": "아치나루터", "en": "A-chi Dock"},
    "DP2": {"kr": "북해안로", "en": "North Shore Road"},
    "E1": {"kr": "아치관", "en": "A-chi Hall"},
    "E2": {"kr": "누리관", "en": "Nuri Hall"},
    "E3": {"kr": "전파암실동", "en": "Electric-wave Darkroom"},
    "E4": {"kr": "학생군사교육단", "en": "R.O.T.C."},
    "E5": {"kr": "입지관", "en": "Yipji Hall"},
}

_KMOU_CAMPUS_MAP_IMAGE_BASE = "https://www.kmou.ac.kr/UserFiles/web/kmou/Campus%20Map/images/sub/"

def _nearest_shuttle_stop_for_code(code: str) -> str:
    c = (code or "").upper()
    if c in {"C1", "C6"}:
        return "해사대학관 앞"
    if c == "B1":
        return "공과대학 1호관 앞"
    if c in {"B2", "B3", "B4"}:
        return "어울림관"
    if c == "BP2":
        return "실습선 부두 방면(앵커탑 인근)"
    if c.startswith("A"):
        return "공과대학 1호관 앞"
    if c.startswith("B") or c.startswith("BP"):
        return "어울림관"
    return "해사대학관 앞"

async def get_campus_building_info(query: str):
    """캠퍼스맵 건물 코드/명칭 검색 + 가장 가까운 셔틀 정류장 안내"""
    q = (query or "").strip()
    if not q:
        return json.dumps({"status": "error", "msg": "검색어가 필요합니다."}, ensure_ascii=False)

    # 코드 우선
    m = re.search(r"\b([A-Za-z]{1,2}P?\d{1,2})\b", q)
    code = m.group(1).upper() if m else None

    found_code: Optional[str] = None
    if code and code in _KMOU_CAMPUS_MAP:
        found_code = code
    else:
        # 한글 명칭 포함 검색
        for k, v in _KMOU_CAMPUS_MAP.items():
            if v.get("kr") and v["kr"] in q:
                found_code = k
                break

    if not found_code:
        return json.dumps({"status": "empty", "msg": "해당 건물을 찾지 못했습니다."}, ensure_ascii=False)

    info = _KMOU_CAMPUS_MAP.get(found_code) or {}
    zone = re.sub(r"\d+.*$", "", found_code)  # A/B/C/D/E/CP/DP...
    thumb = _KMOU_CAMPUS_MAP_IMAGE_BASE + found_code + ".jpg"
    return json.dumps(
        {
            "status": "success",
            "code": found_code,
            "name": info.get("kr"),
            "name_en": info.get("en"),
            "zone": zone,
            "nearest_shuttle_stop": _nearest_shuttle_stop_for_code(found_code),
            "thumbnail_url": thumb,
        },
        ensure_ascii=False,
    )

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
    {
        "type": "function",
        "function": {
            "name": "get_shuttle_next_buses",
            "description": "🚍 셔틀 시간: 현재 시각 기준 다음 3회 셔틀 출발 정보를 제공합니다(방학/학기 자동 전환).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "가져올 출발 횟수(기본 3)"},
                    "now_hhmm": {"type": "string", "description": "테스트용 HH:MM(선택)"},
                    "date_yyyymmdd": {"type": "string", "description": "테스트용 YYYYMMDD(선택)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_campus_building_info",
            "description": "🗺️ 학교 지도: 건물 코드/명칭(A1, B3 도서관 등)으로 위치 정보를 조회하고, 가장 가까운 셔틀 정류장을 안내합니다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "예: B3 도서관, A3 대학본부, 도서관"}},
                "required": ["query"],
            },
        },
    },
]