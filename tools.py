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
from zoneinfo import ZoneInfo

# =========================
# 환경 변수 설정 (요청 반영)
# =========================

ENV_MODE = (os.environ.get("ENV_MODE") or "prod").strip().lower()
# 테스트/시뮬레이션용 기준 시각 오버라이드(미설정 시 시스템 시각 사용)
ARA_REF_DATE = (os.environ.get("ARA_REF_DATE") or "").strip()
ARA_REF_TIME = (os.environ.get("ARA_REF_TIME") or "").strip()

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

# 표준 타임존(KST)
_KST = ZoneInfo("Asia/Seoul")

# =========================
# 공통 유틸
# =========================

# =========================
# Astronomy (KASI) Rise/Set Time
# =========================

_ASTRO_CACHE_TTL_SECONDS = int(os.environ.get("ARA_ASTRONOMY_CACHE_TTL_SECONDS", "3600"))
_ASTRO_CACHE: Dict[str, Tuple[float, str]] = {}

def _format_hhmm(raw: str) -> Optional[str]:
    """
    '1742' -> '17:42'
    - 숫자 4자리(또는 6자리)만 허용
    """
    if not raw:
        return None
    digits = re.sub(r"\D+", "", str(raw))
    if len(digits) == 6:
        digits = digits[:4]
    if len(digits) != 4:
        return None
    hh, mm = digits[:2], digits[2:]
    if not (hh.isdigit() and mm.isdigit()):
        return None
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"

def get_calendar_day_2026(date_yyyymmdd: str):
    """
    2026 진실 소스: calendar_2026.json
    - days[YYYYMMDD]에 저장된 값만 신뢰
    - 없으면 '업데이트 중'으로 처리(절대 계산/추측 금지)
    """
    digits = re.sub(r"\D+", "", str(date_yyyymmdd or ""))
    if len(digits) != 8 or not digits.startswith("2026"):
        return json.dumps({"status": "not_found", "date": digits, "msg": "Data is currently being updated for this specific date."}, ensure_ascii=False)
    path = os.path.join(os.path.dirname(__file__), "calendar_2026.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        days = data.get("days") if isinstance(data, dict) else None
        if not isinstance(days, dict):
            return json.dumps({"status": "not_found", "date": digits, "msg": "Data is currently being updated for this specific date."}, ensure_ascii=False)
        day = days.get(digits)
        if not day:
            return json.dumps({"status": "not_found", "date": digits, "msg": "Data is currently being updated for this specific date."}, ensure_ascii=False)
        return json.dumps({"status": "success", "date": digits, "day": day}, ensure_ascii=False)
    except Exception:
        return json.dumps({"status": "not_found", "date": digits, "msg": "Data is currently being updated for this specific date."}, ensure_ascii=False)

def is_holiday_2026(date_yyyymmdd: str) -> Optional[bool]:
    """
    공휴일 판단은 calendar_2026.json만 사용(계산 금지).
    - day.is_holiday == true/false 가 있으면 그 값만 사용
    - 없으면 None(미확인)
    """
    raw = get_calendar_day_2026(date_yyyymmdd)
    try:
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    day = payload.get("day") or {}
    if isinstance(day, dict) and "is_holiday" in day:
        return bool(day.get("is_holiday"))
    return None

# =========================
# Campus Contact Directory (Offline-first)
# =========================

_CAMPUS_CONTACT_DIRECTORY: Dict[str, Dict[str, str]] = {
    "Emergency": {
        "Integrated_Security_Office": "051-410-4112",
        "Campus_Police_Station": "051-410-4112",
        "Night_Guard_Office": "051-410-4111",
    },
    "Academic_Affairs": {
        "Academic_Management": "051-410-4011",
        "Admissions_Team": "051-410-4771",
        "International_Affairs": "051-410-4761",
        "Registrar_Office": "051-410-4012",
    },
    "Student_Services": {
        "Student_Support_Team": "051-410-4022",
        "Scholarship_Office": "051-410-4024",
        "Health_Center": "051-410-4066",
        "Counseling_Center": "051-410-4065",
    },
    "Campus_Facilities": {
        "Library_Information": "051-410-4071",
        "Dormitory_Administration": "051-410-4054",
        "Cafeteria_Management": "051-410-4114",
        "IT_Support_Center": "051-410-4082",
    },
    "Main_Office": {
        "KMOU_Representative": "051-410-4114",
    },
}

def _pretty_key(s: str) -> str:
    return (s or "").replace("_", " ").strip()

_CONTACT_CATEGORY_KO = {
    "Emergency": "긴급",
    "Academic_Affairs": "학사",
    "Student_Services": "학생지원",
    "Campus_Facilities": "시설",
    "Main_Office": "대표",
}

_CONTACT_OFFICE_KO = {
    "Integrated_Security_Office": "통합보안실",
    "Campus_Police_Station": "교내 경찰/치안",
    "Night_Guard_Office": "야간 경비실",
    "Academic_Management": "학사관리",
    "Admissions_Team": "입학팀",
    "International_Affairs": "국제교류",
    "Registrar_Office": "학적/제증명",
    "Student_Support_Team": "학생지원팀",
    "Scholarship_Office": "장학",
    "Health_Center": "보건실",
    "Counseling_Center": "상담센터",
    "Library_Information": "도서관",
    "Dormitory_Administration": "기숙사 행정",
    "Cafeteria_Management": "식당/구내식당",
    "IT_Support_Center": "IT 지원센터",
    "KMOU_Representative": "학교 대표번호",
}

def get_campus_contacts(category: Optional[str] = None, office: Optional[str] = None, lang: str = "ko"):
    """
    오프라인 캠퍼스 연락처 디렉토리(진실 소스: _CAMPUS_CONTACT_DIRECTORY)
    - category=None: 카테고리 목록 반환
    - category 지정: 해당 카테고리의 연락처 목록 반환
    - office 지정: office를 전체 카테고리에서 검색하여 단일 항목 반환
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    if office:
        key = (office or "").strip()
        for cat, mp in _CAMPUS_CONTACT_DIRECTORY.items():
            if key in mp:
                return json.dumps(
                    {
                        "status": "success",
                        "mode": "office",
                        "category": cat,
                        "office": key,
                        "office_label": (_pretty_key(key) if lang == "en" else (_CONTACT_OFFICE_KO.get(key) or _pretty_key(key))),
                        "phone": mp[key],
                    },
                    ensure_ascii=False,
                )
        return json.dumps(
            {"status": "empty", "msg": ("Contact not found." if lang == "en" else "해당 연락처를 찾지 못했습니다.")},
            ensure_ascii=False,
        )

    if category:
        cat = (category or "").strip()
        mp = _CAMPUS_CONTACT_DIRECTORY.get(cat)
        if not mp:
            return json.dumps(
                {"status": "empty", "msg": ("Category not found." if lang == "en" else "해당 분류를 찾지 못했습니다.")},
                ensure_ascii=False,
            )
        contacts = [
            {
                "office": k,
                "office_label": (_pretty_key(k) if lang == "en" else (_CONTACT_OFFICE_KO.get(k) or _pretty_key(k))),
                "phone": v,
            }
            for k, v in mp.items()
        ]
        return json.dumps(
            {
                "status": "success",
                "mode": "category",
                "category": cat,
                "category_label": (_pretty_key(cat) if lang == "en" else (_CONTACT_CATEGORY_KO.get(cat) or _pretty_key(cat))),
                "contacts": contacts,
            },
            ensure_ascii=False,
        )

    categories = [
        {
            "category": c,
            "category_label": (_pretty_key(c) if lang == "en" else (_CONTACT_CATEGORY_KO.get(c) or _pretty_key(c))),
            "count": len(mp),
        }
        for c, mp in _CAMPUS_CONTACT_DIRECTORY.items()
    ]
    return json.dumps({"status": "success", "mode": "categories", "categories": categories}, ensure_ascii=False)

async def get_astronomy_data(target_date: str):
    """
    KASI Rise/Set Time Information Service
    - Endpoint: http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getAreaRiseSetInfo
    - Params: serviceKey, locdate(YYYYMMDD), location('부산')
    - Strict fallback: 실패 시 Update Pending(임의 시간 생성 금지)
    """
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps(
            {"status": "pending", "msg": "Update Pending", "location": "부산", "date": None, "sunrise": "Update Pending", "sunset": "Update Pending"},
            ensure_ascii=False,
        )

    digits = re.sub(r"\D+", "", str(target_date or ""))
    if len(digits) != 8:
        digits = _reference_datetime().strftime("%Y%m%d")

    cache_key = f"{digits}:부산"
    cached = _ASTRO_CACHE.get(cache_key)
    if cached and (time.time() - cached[0] <= _ASTRO_CACHE_TTL_SECONDS):
        return cached[1]

    url = "http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getAreaRiseSetInfo"
    timeout_s = float(os.environ.get("ARA_ASTRONOMY_TIMEOUT_SECONDS", "2.0"))

    try:
        async with httpx.AsyncClient(headers=HEADERS) as client:
            res = await client.get(
                url,
                params={"serviceKey": DATA_GO_KR_SERVICE_KEY, "locdate": digits, "location": "부산"},
                timeout=timeout_s,
            )
        text = res.text or ""

        sunrise_raw = None
        sunset_raw = None

        # 1) JSON 응답(지원되는 경우)
        if text.lstrip().startswith("{"):
            try:
                data = res.json()

                def _jget(d: Any, *keys: str) -> Any:
                    cur = d
                    for k in keys:
                        if not isinstance(cur, dict):
                            return None
                        cur = cur.get(k)
                    return cur

                code = _jget(data, "response", "header", "resultCode")
                if code and str(code) not in {"00", "0"}:
                    raise RuntimeError("resultCode not OK")
                item = _jget(data, "response", "body", "items", "item") or _jget(data, "response", "body", "item")
                if isinstance(item, list) and item:
                    item = item[0]
                if isinstance(item, dict):
                    sunrise_raw = item.get("sunrise")
                    sunset_raw = item.get("sunset")
            except Exception:
                sunrise_raw = None
                sunset_raw = None

        # 2) XML 응답(기본)
        if sunrise_raw is None or sunset_raw is None:
            if "<resultCode>00</resultCode>" not in text:
                raise RuntimeError("resultCode not OK")

            import xml.etree.ElementTree as ET

            root = ET.fromstring(text)
            # 문서 구조 차이에 대비해 태그를 전역 탐색
            sr = root.find(".//sunrise")
            ss = root.find(".//sunset")
            if sr is not None and sr.text:
                sunrise_raw = sr.text.strip()
            if ss is not None and ss.text:
                sunset_raw = ss.text.strip()

        sunrise = _format_hhmm(sunrise_raw or "")
        sunset = _format_hhmm(sunset_raw or "")
        if not sunrise or not sunset:
            raise RuntimeError("missing sunrise/sunset")

        payload = json.dumps(
            {
                "status": "success",
                "location": "부산",
                "date": digits,
                "sunrise": sunrise,
                "sunset": sunset,
                "raw": {"sunrise": sunrise_raw, "sunset": sunset_raw},
            },
            ensure_ascii=False,
        )
        _ASTRO_CACHE[cache_key] = (time.time(), payload)
        return payload
    except Exception:
        payload = json.dumps(
            {"status": "pending", "msg": "Update Pending", "location": "부산", "date": digits, "sunrise": "Update Pending", "sunset": "Update Pending"},
            ensure_ascii=False,
        )
        _ASTRO_CACHE[cache_key] = (time.time(), payload)
        return payload
# 위치 필터링(무환각): KMOU 좌표(Wikidata 기반)
# - 위/경도는 검색/필터링에만 사용(응답에 임의 생성 좌표는 절대 포함하지 않음)
_KMOU_LAT = 35.074441
_KMOU_LON = 129.086944

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c

def _is_near_kmou(lat: Optional[float], lon: Optional[float], radius_m: float = 5000.0) -> Tuple[bool, Optional[int]]:
    if lat is None or lon is None:
        return (False, None)
    try:
        dist = _haversine_m(float(lat), float(lon), _KMOU_LAT, _KMOU_LON)
        return (dist <= radius_m, int(dist))
    except Exception:
        return (False, None)

def _reference_datetime() -> datetime:
    """
    기준 시각
    - 운영(Render): 시스템 시각(datetime.now) 사용
    - 테스트: ARA_REF_DATE/ARA_REF_TIME로 오버라이드 가능
    """
    if not ARA_REF_DATE and not ARA_REF_TIME:
        return datetime.now(_KST)
    d = re.sub(r"\D+", "", ARA_REF_DATE)
    t = re.sub(r"\D+", "", ARA_REF_TIME)
    if len(d) != 8:
        return datetime.now(_KST)
    if len(t) not in (3, 4):
        return datetime.now(_KST)
    if len(t) == 3:
        t = "0" + t
    try:
        return datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]), int(t[0:2]), int(t[2:4]), tzinfo=_KST)
    except Exception:
        return datetime.now(_KST)

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

async def get_kmou_weather(lang: str = "ko"):
    """한국해양대(영도구 동삼동) 실시간 기상 실황 조회 (lang: ko/en)"""
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps(
            {
                "status": "error",
                "msg": ("Weather API key (DATA_GO_KR_SERVICE_KEY) is missing." if lang == "en" else "기상청 API 키(DATA_GO_KR_SERVICE_KEY)가 없습니다."),
            },
            ensure_ascii=False,
        )

    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
    # 요구사항: 시스템 시각과 동기화된 base_time 사용(운영 기본)
    # - getUltraSrtNcst는 보통 HH00 단위 갱신이므로 HH00 기준으로 조회하고, 실패 시 전 시각으로 폴백합니다.
    now = _reference_datetime()
    base_date = now.strftime("%Y%m%d")
    base_time_primary = now.strftime("%H00")

    # 안정성: 기본 교정 로직(00/30) + 실패 시 전 시각(HH00) fallback
    candidates: List[Tuple[str, str]] = [(base_date, base_time_primary)]
    # 전 1시간 HH00 fallback(가장 흔한 지연/누락 케이스)
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
            # 요구사항: 영도구 격자 좌표
            "nx": "96",
            "ny": "74",
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

            location = "Busan, Yeongdo-gu" if lang == "en" else "부산광역시 영도구"
            return json.dumps(
                {
                    "status": "success",
                    "weather": {
                        "temp": f"{weather_info.get('temp', 'N/A')}°C",
                        "location": location,
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

    return json.dumps(
        {
            "status": "error",
            "msg": (f"Weather fetch failed: {last_error or 'unknown'}" if lang == "en" else f"날씨 조회 실패: {last_error or 'unknown'}"),
        },
        ensure_ascii=False,
    )

# =========================
# 2) 버스 필터링 로직 최적화 (ODsay) — 요청 교정본 반영
# =========================

async def get_bus_arrival(bus_number: str = None, direction: str = None, lang: str = "ko"):
    """
    190번 버스 도착정보(정확성 우선 / 정류장 ID 강제 매핑)
    - FORCE MAPPING:
      - direction이 IN/campus 계열이면 station_id='03058'
      - direction이 OUT/nampo 계열이면 station_id='03053'
    - 정류장 이름 검색(퍼지/우선순위) 로직은 사용하지 않습니다(하드코딩 ID만).
    - 부산BIMS(공공데이터) XML에서 190번의 min1/station1만 추출합니다.
    - API가 200이지만 데이터가 비면(= 운행 중 버스 없음) 정직한 문구로 안내합니다.
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    # direction 정규화(영문/한글/키워드)
    d = (direction or "").strip().lower()
    if d in {"in", "campus", "to campus", "school", "학교", "등교", "학교행"}:
        dir_up = "IN"
    elif d in {"out", "nampo", "to nampo", "city", "downtown", "남포", "남포동", "시내", "하교", "부산역"}:
        dir_up = "OUT"
    else:
        dir_up = (direction or "").strip().upper()
    if dir_up not in {"OUT", "IN"}:
        return json.dumps(
            {
                "status": "need_direction",
                "msg": (
                    "Please choose direction: IN (To Campus) or OUT (To Nampo/City)."
                    if lang == "en"
                    else "버스 동선을 선택해 주세요: OUT(진출) 또는 IN(진입)."
                ),
                "ocean_view": (
                    {"OUT": ["KMOU Main", "Breakwater Entrance", "Seafarers Dorm"], "IN": ["Seafarers Dorm", "University HQ", "KMOU Main"]}
                    if lang == "en"
                    else {"OUT": ["구본관", "방파제입구", "승선생활관"], "IN": ["승선생활관", "대학본부", "구본관"]}
                ),
            },
            ensure_ascii=False,
        )

    # 이 함수는 190만 지원(요구사항: 190 고정 파싱)
    req_num = _extract_digits(bus_number) if bus_number else "190"
    if req_num and req_num != "190":
        return json.dumps(
            {
                "status": "error",
                "msg": ("Currently only bus 190 is supported." if lang == "en" else "현재는 190번 버스만 지원합니다."),
                "requested_bus_number": req_num,
            },
            ensure_ascii=False,
        )
    target_bus_num = "190"

    station_id = "03058" if dir_up == "IN" else "03053"
    direction_label = ("To Campus" if dir_up == "IN" else "To Nampo/City") if lang == "en" else ("학교행" if dir_up == "IN" else "남포/시내행")
    _STOP_LABELS = {
        "03058": {"ko": "한국해양대학교본관(학교행)", "en": "KMOU Main (To Campus)"},
        "03053": {"ko": "해양대입구(남포/시내행)", "en": "KMOU Entrance (To Nampo/City)"},
    }
    label = _STOP_LABELS.get(station_id, {}).get(lang) or f"{direction_label} ({station_id})"

    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps(
            {
                "status": "error",
                "msg": ("Public data API key (DATA_GO_KR_SERVICE_KEY) is missing." if lang == "en" else "공공데이터 API 키(DATA_GO_KR_SERVICE_KEY)가 없습니다."),
                "direction": dir_up,
                "direction_label": direction_label,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "station_label": label,
            },
            ensure_ascii=False,
        )

    # 부산BIMS: 정류소 도착정보(ARS번호) 조회
    # - 일부 API는 arsno에서 선행 0을 허용하지 않는 경우가 있어 2회 시도합니다.
    ars_candidates = [station_id]
    stripped = station_id.lstrip("0")
    if stripped and stripped != station_id:
        ars_candidates.append(stripped)

    busan_bims_url = "http://apis.data.go.kr/6260000/BusanBIMS/bitArrByArsno"
    busan_timeout = float(os.environ.get("ARA_BUS_TIMEOUT_SECONDS", "2.5"))

    def _parse_items_xml(xml_text: str) -> List[Dict[str, Any]]:
        """
        부산BIMS bitArrByArsno XML 파싱(다음/다다음 버스)
        - min1/station1: 다음 버스
        - min2/station2: 다다음 버스
        반환 예(요구사항):
        {
          "line": "190",
          "bus1": {"min": "11", "stop": "8"},
          "bus2": {"min": "30", "stop": "21"}
        }
        """
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []
        items_el = root.find("./body/items")
        if items_el is None:
            return []
        out: List[Dict[str, Any]] = []
        for it in items_el.findall("./item"):
            d: Dict[str, str] = {}
            for child in list(it):
                if child.tag and child.text is not None:
                    d[child.tag] = child.text
            if not d:
                continue

            line = _extract_digits(d.get("lineno") or "")
            if not line:
                continue

            min1 = (d.get("min1") or "").strip()
            st1 = (d.get("station1") or "").strip()
            min2 = (d.get("min2") or "").strip()
            st2 = (d.get("station2") or "").strip()

            payload: Dict[str, Any] = {
                "line": line,
                "bus1": {"min": min1, "stop": st1} if min1 else None,
                "bus2": {"min": min2, "stop": st2} if min2 else None,
                # 추가 메타(필요 시 사용)
                "lowplate1": (d.get("lowplate1") or "").strip(),
                "lowplate2": (d.get("lowplate2") or "").strip(),
            }
            out.append(payload)
        return out

    items: List[Dict[str, Any]] = []
    last_err: Optional[str] = None
    last_xml_text: str = ""
    last_status_code: Optional[int] = None
    for arsno in ars_candidates:
        try:
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
                res = await client.get(
                    busan_bims_url,
                    params={"serviceKey": DATA_GO_KR_SERVICE_KEY, "arsno": arsno, "numOfRows": "50", "pageNo": "1"},
                    timeout=busan_timeout,
                )
            last_status_code = res.status_code
            xml_text = res.text or ""
            last_xml_text = xml_text
            # 정상코드 체크(00만 통과)
            if "<resultCode>00</resultCode>" not in xml_text:
                last_err = "공공데이터 응답이 정상코드가 아닙니다."
                continue
            parsed = _parse_items_xml(xml_text)
            # 200 + resultCode 00 인데 items가 비어있을 수 있음(운행 없음 케이스)
            items = parsed or []
            break
        except Exception as e:
            last_err = str(e)
            continue

    # 200인데 데이터가 비면: 운행 중 버스 없음(요구사항 문구)
    if (last_status_code == 200) and ("<resultCode>00</resultCode>" in (last_xml_text or "")) and (not items):
        return json.dumps(
            {
                "status": "empty",
                "msg": (
                    "There is no operating 190 bus right now (waiting at depot)."
                    if lang == "en"
                    else "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"
                ),
                "direction": dir_up,
                "direction_label": direction_label,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "station_label": label,
                "detail": "empty_items",
            },
            ensure_ascii=False,
        )

    if not items:
        # 공공데이터 장애/비정상 응답
        return json.dumps(
            {
                "status": "error",
                "msg": (
                    "Live bus data is currently not responding (2026-01-20)."
                    if lang == "en"
                    else "현재 2026-01-20 실시간 버스 정보가 서버에서 응답하지 않습니다"
                ),
                "direction": dir_up,
                "direction_label": direction_label,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "station_label": label,
                "detail": last_err or "empty",
            },
            ensure_ascii=False,
        )

    # 190번: bus1(다음) + bus2(다다음) 추출
    found_190: Optional[Dict[str, Any]] = None
    for it in items:
        if str(it.get("line") or "").strip() != "190":
            continue
        found_190 = it
        break

    if not found_190:
        return json.dumps(
            {
                "status": "empty",
                "direction": dir_up,
                "direction_label": direction_label,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "station_label": label,
                "msg": (
                    "There is no operating 190 bus right now (waiting at depot)."
                    if lang == "en"
                    else "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"
                ),
                "stops": [{"label": label, "station_id": station_id, "status": "success", "buses": []}],
            },
            ensure_ascii=False,
        )

    buses: List[Dict[str, Any]] = []
    b1 = found_190.get("bus1") if isinstance(found_190, dict) else None
    b2 = found_190.get("bus2") if isinstance(found_190, dict) else None

    if isinstance(b1, dict) and (b1.get("min") or ""):
        min1 = str(b1.get("min") or "").strip()
        st1 = str(b1.get("stop") or "").strip()
        lp1 = str(found_190.get("lowplate1") or "").strip() if isinstance(found_190, dict) else ""
        if lang == "en":
            status = f"Next: {min1} min" + (f" ({st1} stops away)" if st1 else "")
            low_plate = "Low-floor" if lp1 == "1" else "Standard"
        else:
            status = f"다음: {min1}분" + (f" ({st1}정류장 전)" if st1 else "")
            low_plate = "저상" if lp1 == "1" else "일반"
        buses.append(
            {
                "bus_no": "190",
                "arrival": "Next",
                "min": min1,
                "stop": st1,
                "status": status,
                "low_plate": low_plate,
            }
        )

    if isinstance(b2, dict) and (b2.get("min") or ""):
        min2 = str(b2.get("min") or "").strip()
        st2 = str(b2.get("stop") or "").strip()
        lp2 = str(found_190.get("lowplate2") or "").strip() if isinstance(found_190, dict) else ""
        if lang == "en":
            status = f"Subsequent: {min2} min" + (f" ({st2} stops away)" if st2 else "")
            low_plate = "Low-floor" if lp2 == "1" else "Standard"
        else:
            status = f"다다음: {min2}분" + (f" ({st2}정류장 전)" if st2 else "")
            low_plate = "저상" if lp2 == "1" else "일반"
        buses.append(
            {
                "bus_no": "190",
                "arrival": "Subsequent",
                "min": min2,
                "stop": st2,
                "status": status,
                "low_plate": low_plate,
            }
        )

    # bus1/bus2 모두 없으면: 운행 중인 190 없음으로 처리
    if not buses:
        return json.dumps(
            {
                "status": "empty",
                "direction": dir_up,
                "direction_label": direction_label,
                "bus_number": target_bus_num,
                "station_id": station_id,
                "station_label": label,
                "msg": (
                    "There is no operating 190 bus right now (waiting at depot)."
                    if lang == "en"
                    else "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"
                ),
                "stops": [{"label": label, "station_id": station_id, "status": "success", "buses": []}],
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "status": "success",
            "direction": dir_up,
            "direction_label": direction_label,
            "bus_number": target_bus_num,
            "stops": [{"label": label, "station_id": station_id, "status": "success", "buses": buses}],
        },
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

# =========================
# 3) 맛집(동적 추천) — 멀티턴용
# =========================

async def search_restaurants(query: str, limit: int = 5):
    """
    맛집/카페 동적 검색(무환각)
    - 1순위: Kakao Local Search(키가 있을 때만)
    - 2순위: places.csv 폴백
    """
    q = (query or "").strip()
    if not q:
        return json.dumps({"status": "error", "msg": "검색어가 필요합니다."}, ensure_ascii=False)

    limit_n = max(1, min(int(limit or 5), 10))

    kakao_key = (os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    if kakao_key:
        try:
            url = "https://dapi.kakao.com/v2/local/search/keyword.json"
            # 영도/해양대 인근 결과를 유도(검색 쿼리만 보강; 결과는 좌표/주소로 재검증)
            query2 = f"{q} 영도"
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers={"Authorization": f"KakaoAK {kakao_key}"}) as client:
                res = await client.get(url, params={"query": query2, "size": str(limit_n)}, timeout=2.5)
                res.raise_for_status()
                data = res.json()

            docs = (data.get("documents") or []) if isinstance(data, dict) else []
            out: List[Dict[str, Any]] = []
            for d in docs:
                name = (d.get("place_name") or "").strip()
                addr = (d.get("road_address_name") or d.get("address_name") or "").strip()
                phone = (d.get("phone") or "").strip()
                link = (d.get("place_url") or "").strip()
                try:
                    lon = float(d.get("x")) if d.get("x") else None
                    lat = float(d.get("y")) if d.get("y") else None
                except Exception:
                    lat, lon = None, None

                near, dist_m = _is_near_kmou(lat, lon, radius_m=5000.0)
                # 지오펜싱(엄격): KMOU 반경 5km + (주소가 있을 경우) 영도구 키워드
                if not near:
                    continue
                if addr and ("영도구" not in addr) and ("영도" not in addr):
                    continue

                out.append(
                    {
                        "name": name,
                        "addr": addr,
                        "tel": phone,
                        "lat": lat,
                        "lon": lon,
                        "distance_m": dist_m,
                        "link": link,
                        "source": "kakao",
                    }
                )
                if len(out) >= limit_n:
                    break

            if out:
                return json.dumps({"status": "success", "query": q, "restaurants": out}, ensure_ascii=False)
        except Exception:
            # Kakao API 실패 시 places.csv 폴백으로 진행(추측 금지)
            pass

    # places.csv 폴백(좌표 없음 → 텍스트 기반으로 '영도/해양대' 근처만 통과)
    try:
        path = os.path.join(os.path.dirname(__file__), "places.csv")
        if not os.path.exists(path):
            return json.dumps({"status": "empty", "msg": "로컬 places.csv를 찾지 못했습니다."}, ensure_ascii=False)

        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            # 헤더에 머지 찌꺼기가 섞인 경우 방어
            fieldnames = reader.fieldnames or []
            if fieldnames and fieldnames[0].lower().startswith("git merge"):
                # 첫 컬럼명을 name으로 정규화
                fieldnames[0] = "name"
                reader.fieldnames = fieldnames

            rows = list(reader)

        ql = q.lower()
        out: List[Dict[str, Any]] = []
        for r in rows:
            name = (r.get("name") or r.get("temp-fixname") or "").strip()
            cat = (r.get("category") or "").strip()
            desc = (r.get("description") or "").strip()
            rec = (r.get("recommendation") or "").strip()

            blob = f"{name} {cat} {desc} {rec}".lower()
            if ql not in blob:
                continue

            # 위치 근거가 텍스트에 포함될 때만 통과(무환각)
            # - 좌표가 없으므로 '영도/해양대' 등 근거 문자열이 없으면 폐기
            if not any(k in desc for k in ["영도", "영도구", "해양대", "동삼동", "흰여울"]):
                continue

            out.append({"name": name, "category": cat, "description": desc, "recommendation": rec, "source": "places.csv"})
            if len(out) >= limit_n:
                break

        if not out:
            return json.dumps({"status": "empty", "msg": "조건에 맞는 영도/해양대 인근 맛집을 찾지 못했습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "query": q, "restaurants": out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

async def get_medical_places(kind: str = "pharmacy", radius_m: int = 5000, lang: str = "ko"):
    """
    카카오 Local Search 기반 의료기관/약국 검색(지오펜싱 포함)
    - [ARA Log] 로깅 요구사항 반영(키 노출 금지)
    - 지오펜싱: 반경(radius_m) 5km 유지
    - 주소 문자열(영도/Yeongdo) 필터로 0건이 되면, 주소 필터는 풀고 반경 기준으로 폴백
    - 'pharmacy'가 0건이면 '약국'으로 재시도
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    kakao_key = (os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    if not kakao_key:
        print("[ARA Log] WARNING: KAKAO_REST_API_KEY is missing (medical search will fail).")
        return json.dumps(
            {"status": "error", "msg": ("Kakao API key is missing." if lang == "en" else "Kakao API 키(KAKAO_REST_API_KEY)가 없어 의료기관 검색을 할 수 없습니다.")},
            ensure_ascii=False,
        )

    q = (kind or "").strip()
    if not q:
        q = "pharmacy" if lang == "en" else "약국"

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    size = "15"
    radius = str(max(100, min(int(radius_m or 5000), 20000)))  # Kakao 제한 고려

    async def _fetch(query: str) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=headers) as client:
            res = await client.get(
                url,
                params={
                    "query": query,
                    "x": str(_KMOU_LON),
                    "y": str(_KMOU_LAT),
                    "radius": radius,
                    "size": size,
                },
                timeout=2.5,
            )
            res.raise_for_status()
            data = res.json()
        docs = (data.get("documents") or []) if isinstance(data, dict) else []
        print(f"[ARA Log] Kakao medical docs={len(docs)} query={query!r}")
        return docs

    try:
        docs = await _fetch(q)
        if (not docs) and (q.lower() == "pharmacy"):
            # 폴백: 영어 pharmacy가 0이면 한국어 약국으로 재시도
            docs = await _fetch("약국")

        if not docs:
            return json.dumps({"status": "empty", "msg": ("No medical institutions found." if lang == "en" else "조건에 맞는 의료 기관 정보를 찾지 못했습니다.")}, ensure_ascii=False)

        candidates_radius: List[Dict[str, Any]] = []
        candidates_addr: List[Dict[str, Any]] = []

        for d in docs:
            name = (d.get("place_name") or "").strip()
            addr = (d.get("road_address_name") or d.get("address_name") or "").strip()
            phone = (d.get("phone") or "").strip()
            link = (d.get("place_url") or "").strip()
            try:
                lon = float(d.get("x")) if d.get("x") else None
                lat = float(d.get("y")) if d.get("y") else None
            except Exception:
                lat, lon = None, None

            near, dist_m = _is_near_kmou(lat, lon, radius_m=float(radius_m or 5000))
            if not near:
                continue

            row = {
                "name": name,
                "addr": addr,
                "tel": phone,
                "lat": lat,
                "lon": lon,
                "distance_m": dist_m,
                "link": link,
                "source": "kakao",
                "is_open": None,  # Kakao 응답에 영업 여부가 없어 미확인
            }
            candidates_radius.append(row)

            if addr and (("영도" in addr) or ("영도구" in addr) or ("Yeongdo" in addr) or ("yeongdo" in addr)):
                candidates_addr.append(row)

        # 주소 문자열 필터로 0건이면 반경 기준으로 폴백(요구사항)
        final = candidates_addr if candidates_addr else candidates_radius

        if not final:
            return json.dumps({"status": "empty", "msg": ("No verified facilities found within the campus vicinity" if lang == "en" else "학교 인근(반경 5km)에서 확인된 의료기관이 없습니다.")}, ensure_ascii=False)

        final = sorted(final, key=lambda x: (x.get("distance_m") is None, x.get("distance_m") or 10**9))
        return json.dumps({"status": "success", "kind": q, "places": final[:5]}, ensure_ascii=False)
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
        # 09:00+ 운영 기준: 영업중(is_open=True) 우선 노출
        targets = sorted(targets, key=lambda x: (not bool(x.get("is_open")), x.get("name") or ""))
        return json.dumps({"status": "success", "hospitals": targets[:5]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

async def get_festival_info():
    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps({"status": "error", "msg": "공공데이터 API 키가 없어 조회할 수 없습니다."}, ensure_ascii=False)

    url = "http://apis.data.go.kr/6260000/FestivalService/getFestivalKr"
    params = {"serviceKey": DATA_GO_KR_SERVICE_KEY, "numOfRows": "10", "pageNo": "1", "resultType": "json"}
    res = await _http_get_json(url, params, timeout=10.0)

    # 1) 1차(기존) API 파싱
    try:
        if res["status"] != "success":
            raise RuntimeError(res.get("msg", "API 호출 실패"))
        items = _safe_get(res, "data", "getFestivalKr", "item", default=[]) or []
        targets: List[Dict[str, Any]] = []
        for i in items:
            title = i.get("MAIN_TITLE")
            place = i.get("MAIN_PLACE")
            date_text = i.get("USAGE_DAY_WEEK_AND_TIME")

            dt = _extract_ymd(str(date_text or ""))
            if not dt:
                continue
            if dt.strftime("%Y%m%d") < "20260120":
                continue
            targets.append({"title": title, "place": place, "date": date_text, "date_ymd": dt.strftime("%Y%m%d")})
        if targets:
            return json.dumps({"status": "success", "festivals": targets[:5]}, ensure_ascii=False)
    except Exception:
        pass

    # 2) 폴백: 문화정보조회서비스(area2)
    # - 이 API는 별도 이용신청이 필요할 수 있으며(403), 실패 시 정직하게 보고합니다.
    culture_key = (os.environ.get("CULTUREINFO_SERVICE_KEY") or "").strip() or DATA_GO_KR_SERVICE_KEY
    try:
        import xml.etree.ElementTree as ET

        fallback_url = "https://apis.data.go.kr/B553457/cultureinfo/area2"
        now = _reference_datetime()
        start_ymd = now.strftime("%Y%m%d")
        if start_ymd < "20260120":
            start_ymd = "20260120"
        end_ymd = (now + timedelta(days=60)).strftime("%Y%m%d")

        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
            r = await client.get(
                fallback_url,
                params={"serviceKey": culture_key, "pageNo": "1", "numOfrows": "20", "place": "부산광역시", "from": start_ymd, "to": end_ymd},
                timeout=5.0,
            )

        if r.status_code == 403:
            return json.dumps(
                {
                    "status": "empty",
                    "msg": "축제/행사 대체 API(문화정보조회서비스)는 현재 이용 권한이 없어 조회할 수 없습니다. 공공데이터포털에서 해당 API 이용신청이 필요합니다.",
                },
                ensure_ascii=False,
            )

        root = ET.fromstring(r.text or "")
        items_el = root.find(".//items")
        if items_el is None:
            return json.dumps({"status": "empty", "msg": "2026-01-20 이후의 확정 일정만 제공할 수 있습니다."}, ensure_ascii=False)

        out: List[Dict[str, Any]] = []
        for it in items_el.findall(".//item"):
            raw_map = {c.tag: (c.text or "").strip() for c in list(it) if c.tag}
            title = raw_map.get("title") or raw_map.get("TITLE") or raw_map.get("subject") or raw_map.get("programNm") or ""
            place = raw_map.get("place") or raw_map.get("PLACE") or raw_map.get("placeNm") or raw_map.get("addr") or ""
            date_text = raw_map.get("date") or raw_map.get("startDate") or raw_map.get("eventStartDate") or raw_map.get("start") or ""

            dt = _extract_ymd(date_text)
            if not dt:
                continue
            if dt.strftime("%Y%m%d") < "20260120":
                continue
            out.append({"title": title or "행사", "place": place, "date": date_text, "date_ymd": dt.strftime("%Y%m%d")})
            if len(out) >= 5:
                break

        if not out:
            return json.dumps({"status": "empty", "msg": "2026-01-20 이후의 확정 일정만 제공할 수 있습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "festivals": out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "empty", "msg": f"행사 조회 폴백 실패: {str(e)}"}, ensure_ascii=False)

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

async def get_shuttle_next_buses(limit: int = 3, now_hhmm: Optional[str] = None, date_yyyymmdd: Optional[str] = None, lang: str = "ko"):
    """셔틀 다음 N회 출발(시즌 자동 전환 + 실시간 필터)"""
    # 기준 시각(KST)
    now_dt = datetime.now(_KST)
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

    lang = (lang or "ko").strip().lower()
    season = get_current_season(now_dt.date())
    is_weekend = now_dt.weekday() >= 5
    # 법정 공휴일 판단은 calendar_2026.json만 사용(계산 금지)
    ymd = now_dt.strftime("%Y%m%d")
    is_holiday = is_holiday_2026(ymd)
    if is_weekend or (is_holiday is True):
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

    season_label = None
    if season == "VACATION":
        season_label = "Winter Vacation Schedule (No. 3-1)" if lang == "en" else "[❄️ 방학중] 3-1 하리전용"
        schedule = _SHUTTLE_VACATION
        if schedule.get("1-1") is None:
            inactive.append("1-1")
        if schedule.get("2-1") is None:
            inactive.append("2-1")
        times_3 = schedule.get("3-1") or []
        for t in times_3:
            m = _hhmm_to_minutes(t)
            if m is not None:
                departures.append((m, "3-1 (Hari)" if lang == "en" else "3-1 하리전용"))
    else:
        season_label = "Semester Schedule" if lang == "en" else "[🌸 학기중] 셔틀"
        schedule = dict(_SHUTTLE_SEMESTER)
        # 3-1 학기중 20분 간격
        schedule["3-1"] = _shuttle_3_1_semester_times()
        for bus_id, times in schedule.items():
            for t in times:
                m = _hhmm_to_minutes(t)
                if m is not None:
                    label = bus_id if bus_id in {"1-1", "2-1"} else ("3-1 (Hari)" if lang == "en" else "3-1 하리전용")
                    departures.append((m, label))

    departures = sorted([d for d in departures if d[0] >= cur_min], key=lambda x: x[0])
    picked = departures[: max(0, int(limit))]

    if not picked:
        return json.dumps(
            {
                "status": "ended",
                "season": season,
                "season_label": season_label,
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
            "season_label": season_label,
            "now": now_dt.strftime("%Y-%m-%d %H:%M"),
            "inactive": inactive,
            "next": [{"bus": bus, "time": _minutes_to_hhmm(m)} for m, bus in picked],
            "route_base": _SHUTTLE_ROUTE_BASE,
            "route_market": _SHUTTLE_ROUTE_MARKET,
            "notice": _SHUTTLE_NOTICE,
        },
        ensure_ascii=False,
    )

async def get_shuttle_schedule(current_time: Optional[str] = None, date_yyyymmdd: Optional[str] = None, lang: str = "ko"):
    """
    다음 셔틀 1회만 반환(요구사항)
    - current_time: 'HH:MM' (미입력 시 KST 현재시각 사용)
    - 방학(2026-01-20)은 VACATION으로 3-1(하리)만 기본 활성
    - Output: "Next shuttle is at [Time] (Type: Loop/Commute)"
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    now_dt = datetime.now(_KST)
    if date_yyyymmdd:
        digits = re.sub(r"\D+", "", str(date_yyyymmdd))
        if len(digits) == 8:
            try:
                now_dt = datetime(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), now_dt.hour, now_dt.minute, tzinfo=_KST)
            except Exception:
                pass

    if current_time:
        mm = _hhmm_to_minutes(current_time)
        if mm is not None:
            now_dt = now_dt.replace(hour=mm // 60, minute=mm % 60, second=0, microsecond=0)
    current_time_str = now_dt.strftime("%H:%M")

    season = get_current_season(now_dt.date())
    cur_min = now_dt.hour * 60 + now_dt.minute

    # 주말/공휴일 운행 없음(기존 정책)
    ymd = now_dt.strftime("%Y%m%d")
    is_weekend = now_dt.weekday() >= 5
    is_holiday = is_holiday_2026(ymd)
    if is_weekend or (is_holiday is True):
        msg = ("No service on weekends/holidays." if lang == "en" else "금일 셔틀 운행이 종료되었습니다.")
        return json.dumps({"status": "ended", "season": season, "msg": msg}, ensure_ascii=False)

    # 다음 출발 후보 생성
    candidates: List[Tuple[int, str, str]] = []  # (minutes, bus, type)

    if season == "VACATION":
        # 방학: 3-1 하리전용만
        for t in (_SHUTTLE_VACATION.get("3-1") or []):
            m = _hhmm_to_minutes(t)
            if m is None:
                continue
            candidates.append((m, "3-1 (Hari)", "Loop"))
    else:
        # 학기: 1-1/2-1(통학) + 3-1(순환)
        for bus_id, times in _SHUTTLE_SEMESTER.items():
            for t in (times or []):
                m = _hhmm_to_minutes(t)
                if m is None:
                    continue
                candidates.append((m, bus_id, "Commute"))
        for t in _shuttle_3_1_semester_times():
            m = _hhmm_to_minutes(t)
            if m is None:
                continue
            candidates.append((m, "3-1", "Loop"))

    candidates = sorted([c for c in candidates if c[0] >= cur_min], key=lambda x: x[0])
    if not candidates:
        msg = ("Service has ended for today." if lang == "en" else "금일 셔틀 운행이 종료되었습니다.")
        return json.dumps({"status": "ended", "season": season, "msg": msg}, ensure_ascii=False)

    next_m, bus, typ = candidates[0]
    next_time = _minutes_to_hhmm(next_m)

    if lang == "en":
        msg = f"Next shuttle is at {next_time} (Type: {typ})"
    else:
        # 요구 포맷 준수
        if "Hari" in bus or "하리" in bus:
            dest = "하리행"
        else:
            dest = "통학" if typ == "Commute" else "순환"
        msg = f"현재 시각({current_time_str}) 기준, 다음 셔틀은 [{next_time}]에 있습니다. ({dest})"
    return json.dumps({"status": "success", "season": season, "next_time": next_time, "bus": bus, "type": typ, "msg": msg}, ensure_ascii=False)

"""
NOTE: 캠퍼스 정적 지도/이미지 기능은 요구사항에 따라 제거되었습니다.
- 학교 지도는 `main.py`에서 KMOU 홈페이지(webLink) 기능으로 대체합니다.
"""

# =========================
# Tool Specification (CRITICAL)
# =========================

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "get_bus_arrival",
            "description": "🚌 190 Bus: '190 버스 IN' 또는 '190 버스 OUT'으로 정류장(03058/03053) 기준 실시간 도착 정보를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bus_number": {"type": "string", "description": "예: 190, 101 등(미입력 시 190 기본값)"},
                    "direction": {"type": "string", "enum": ["IN", "OUT"], "description": "IN(진입) 또는 OUT(진출)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kmou_weather",
            "description": "🌤️ Weather: '영도 날씨' 형태로 영도구 실시간 기상 실황을 조회합니다.",
            "parameters": {"type": "object", "properties": {"lang": {"type": "string", "description": "ko 또는 en(선택)"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cheap_eats",
            "description": "🍚 착한가격 식당(구형): 영도구 착한가격업소 정보를 조회합니다.",
            "parameters": {"type": "object", "properties": {"food_type": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": "🍚 Restaurants: 음식 종류(예: 한식/중식/카페/커피 등)로 영도/해양대 인근 맛집을 동적으로 검색합니다(places.csv 또는 지도 API).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "예: 한식, 중식, 카페, 커피, 국밥 등"},
                    "limit": {"type": "integer", "description": "최대 결과 수(기본 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_medical_info",
            "description": "🏥 Pharmacy/Hospital: 약국/병원 정보를 조회하고(영업중 우선), 필요 시 kind로 필터링합니다.",
            "parameters": {"type": "object", "properties": {"kind": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_medical_places",
            "description": "🏥 Medical(near KMOU): Kakao Local Search로 약국/병원을 조회하고 반경 5km 지오펜싱을 적용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "예: pharmacy, hospital, 약국, 치과 등(선택)"},
                    "radius_m": {"type": "integer", "description": "반경(m), 기본 5000"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_festival_info",
            "description": "🎉 Festival/Events: 부산 행사/축제 정보를 조회하고, 2026-01-20 이후 일정만 제공합니다(폴백 포함).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shuttle_next_buses",
            "description": "🚐 Shuttle: 현재 시각 기준 다음 N회 셔틀 출발 정보를 제공합니다(방학/학기 자동 전환).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "가져올 출발 횟수(기본 3)"},
                    "now_hhmm": {"type": "string", "description": "테스트용 HH:MM(선택)"},
                    "date_yyyymmdd": {"type": "string", "description": "테스트용 YYYYMMDD(선택)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shuttle_schedule",
            "description": "🚐 Shuttle(Next only): 현재 시각 기준 다음 1회 출발만 반환합니다(방학/학기 자동 전환).",
            "parameters": {
                "type": "object",
                "properties": {
                    "current_time": {"type": "string", "description": "HH:MM (선택)"},
                    "date_yyyymmdd": {"type": "string", "description": "YYYYMMDD (선택)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar_day_2026",
            "description": "📅 2026 캘린더(진실 소스): calendar_2026.json에 명시된 날짜만 확인합니다. 없으면 '업데이트 중'으로만 응답합니다(계산/추측 금지).",
            "parameters": {
                "type": "object",
                "properties": {"date_yyyymmdd": {"type": "string", "description": "YYYYMMDD (예: 20260120)"}},
                "required": ["date_yyyymmdd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_astronomy_data",
            "description": "🌅 일출/일몰(진실 소스): KASI 일출/일몰 API로 부산 지역의 sunrise/sunset을 조회합니다. 실패 시 Update Pending.",
            "parameters": {
                "type": "object",
                "properties": {"target_date": {"type": "string", "description": "YYYYMMDD (예: 20260120)"}},
                "required": ["target_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_campus_contacts",
            "description": "📞 캠퍼스 연락처(오프라인): 내장 JSON(진실 소스)에서 학교 연락처를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "예: Emergency, Academic_Affairs 등(선택)"},
                    "office": {"type": "string", "description": "예: Integrated_Security_Office 등(선택)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
]