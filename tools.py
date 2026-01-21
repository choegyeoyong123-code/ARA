from __future__ import annotations

import csv
import json
import os
import re
import time
import asyncio
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

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
# ARA Signature UI (Theme)
# =========================

THEME_COLOR = "5CABDC"  # Ocean Blue
THEME_TEXT_COLOR = "ffffff"  # White

EMOJI_BUS = "🚌"
EMOJI_TIME = "⏱️"
EMOJI_WEATHER = "🌤️"
EMOJI_FOOD = "🍱"
EMOJI_UNI = "⚓"

def get_theme_image(text: str) -> str:
    """
    테마 배너 이미지(임시): placehold.co를 이용해 프로필 컬러 기반 배너 생성
    """
    t = (text or "").strip()
    # URL query는 반드시 인코딩
    return f"https://placehold.co/800x400/{THEME_COLOR}/{THEME_TEXT_COLOR}?text={quote_plus(t)}&font=roboto"

def _kakao_item_card(
    *,
    thumbnail_text: str,
    head_title: str,
    head_desc: str,
    items: List[Tuple[str, str]],
    buttons: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Kakao itemCard payload 생성(시그니처 UI)
    - 주의: Open Builder 렌더링은 환경에 따라 차이가 있을 수 있어, 실패 시 main.py에서 basicCard로 폴백 가능하도록 구성합니다.
    """
    return {
        "thumbnail": {"imageUrl": get_theme_image(thumbnail_text)},
        "imageTitle": {"title": (head_title or "")[:50], "description": (head_desc or "")[:100]},
        "itemList": [{"title": (t or "")[:20], "description": (d or "")[:60]} for (t, d) in (items or [])][:10],
        "buttons": (buttons or [])[:3],
        "buttonLayout": "horizontal",
    }

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

def get_academic_schedule(query: Optional[str] = None, today_yyyy_mm_dd: Optional[str] = None, lang: str = "ko"):
    """
    2026 학사일정 D-Day 계산(웹 크롤링 금지 / 하드코딩 딕셔너리만 사용)
    - 입력:
      - query(선택): 특정 이벤트명 검색(부분일치)
      - today_yyyy_mm_dd(선택): 테스트/시뮬레이션용 기준일(YYYY-MM-DD)
      - lang(선택): ko/en (현재는 ko 기준 텍스트 제공, 데이터 구조는 공통)
    - 출력: json 문자열
      - items: [{name, date, weekday_ko, d_day, days_diff}]
    """
    # 요구사항: 함수 내부에 정확한 딕셔너리를 "복사"하여 사용
    SCHEDULE_2026 = {
        "1학기 수강신청": "2026-02-23",  # Corrected from PDF (Feb 23 is Mon)
        "전기 학위수여식(졸업)": "2026-02-24",
        "1학기 재학생 등록금 납부": "2026-02-24",
        "2026 입학식": "2026-02-26",
        "1학기 개강": "2026-03-02",      # Standard Start Date
        "1학기 수업일수 1/3선": "2026-04-07",
        "1학기 중간고사(예상)": "2026-04-20", # 8th week estimate
        "근로자의 날": "2026-05-01",
        "대동제(예상)": "2026-05-20",
        "1학기 기말고사(예상)": "2026-06-15",
        "여름방학 시작": "2026-06-22",
        "2학기 개강": "2026-09-01",
        "개교기념일": "2026-11-05"
    }

    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    def _parse_ymd(s: str) -> Optional[date]:
        try:
            d = date.fromisoformat((s or "").strip())
            if d.year != 2026:
                return None
            return d
        except Exception:
            return None

    def _weekday_ko(d: date) -> str:
        # 월(0)~일(6)
        names = ["월", "화", "수", "목", "금", "토", "일"]
        try:
            return names[d.weekday()]
        except Exception:
            return ""

    def _dday_label(days_diff: int) -> str:
        if days_diff == 0:
            return "D-DAY"
        if days_diff > 0:
            return f"D-{days_diff}"
        return f"D+{abs(days_diff)}"

    # 기준일(KST) 결정
    if today_yyyy_mm_dd:
        today_date = _parse_ymd(today_yyyy_mm_dd)
        if today_date is None:
            # 입력이 잘못된 경우, 시스템 기준으로 폴백
            today_date = _reference_datetime().date()
    else:
        today_date = _reference_datetime().date()

    # 검색어 정규화(부분일치)
    q = (query or "").strip()
    q_norm = re.sub(r"\s+", "", q)

    items: List[Dict[str, Any]] = []
    for name, ds in SCHEDULE_2026.items():
        if q_norm:
            key_norm = re.sub(r"\s+", "", str(name))
            if (q_norm not in key_norm) and (key_norm not in q_norm):
                continue
        d = _parse_ymd(ds)
        if d is None:
            items.append(
                {
                    "name": name,
                    "date": ds,
                    "weekday_ko": "",
                    "days_diff": None,
                    "d_day": None,
                    "status": "invalid_date",
                }
            )
            continue
        diff = (d - today_date).days
        items.append(
            {
                "name": name,
                "date": d.isoformat(),
                "weekday_ko": _weekday_ko(d),
                "days_diff": diff,
                "d_day": _dday_label(diff),
                "status": ("today" if diff == 0 else ("upcoming" if diff > 0 else "past")),
            }
        )

    # 날짜 기준 정렬(파싱 실패 항목은 뒤로)
    def _sort_key(it: Dict[str, Any]):
        ds = it.get("date")
        try:
            d = date.fromisoformat(ds) if isinstance(ds, str) else None
        except Exception:
            d = None
        return (1, date.max) if d is None else (0, d)

    items = sorted(items, key=_sort_key)

    # 간단 텍스트(도구 호출자가 그대로 보여줄 수 있도록)
    lines: List[str] = []
    for it in items:
        if it.get("d_day") and it.get("weekday_ko"):
            lines.append(f"{it['d_day']} · {it['name']} ({it['date']} {it['weekday_ko']})")
        elif it.get("d_day"):
            lines.append(f"{it['d_day']} · {it['name']} ({it['date']})")
        else:
            lines.append(f"— · {it.get('name')} ({it.get('date')})")

    return json.dumps(
        {
            "status": "success",
            "source": "derived_hardcoded_2026",
            "today": today_date.isoformat(),
            "query": q,
            "items": items,
            "text": "\n".join(lines)[:1500],
        },
        ensure_ascii=False,
    )

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
    # NOTE: 학식 메뉴 크롤링/캐시 로직은 요구사항에 따라 폐기되었습니다.

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
                # 풍속(WSD, m/s): getUltraSrtNcst 표준 제공 항목(없을 수 있어 안전하게 처리)
                if item.get("category") == "WSD":
                    weather_info["wind_speed"] = item.get("obsrValue")

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

def _wind_intensity_desc_ko(wind_speed_ms: float) -> str:
    """
    풍속(m/s) → 학생 친화적 강도 설명(요구사항)
    - 0.0 ~ 1.0: 고요함
    - 1.0 ~ 4.0: 선선한 바람
    - 4.0 ~ 9.0: 바람 다소 강함
    - 9.0 이상: ⚠️ 강풍 주의
    """
    v = float(wind_speed_ms or 0.0)
    if v <= 1.0:
        return "고요함"
    if v < 4.0:
        return "선선한 바람"
    if v < 9.0:
        return "바람 다소 강함"
    return "⚠️ 강풍 주의"

def _fmt_num(x: float) -> str:
    """0.0/1.0 같은 값은 '0'/'1'로, 그 외는 소수 1자리로 표시."""
    try:
        v = float(x)
    except Exception:
        v = 0.0
    s = f"{v:.1f}"
    return s.rstrip("0").rstrip(".")

def _wind_chill_c(temp_c: float, wind_speed_ms: float) -> float:
    t = float(temp_c or 0.0)
    v_kmh = float(wind_speed_ms or 0.0) * 3.6
    if t <= 10.0 and v_kmh > 4.8:
        return 13.12 + 0.6215 * t - 11.37 * (v_kmh ** 0.16) + 0.3965 * t * (v_kmh ** 0.16)
    return t

async def get_weather_info(lang: str = "ko") -> str:
    """
    영도 날씨(풍속 포함) — UI는 main.py에서 카드로 구성
    - 반환: json 문자열
    - 안정성: OpenWeatherMap(있으면) → KMA(get_kmou_weather) 폴백
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    try:
        data: Dict[str, Any] = {}

        # 1) OpenWeatherMap(있다면) 사용 — free API에서 보장되는 필드만 사용
        owm_key = (os.environ.get("OPENWEATHER_API_KEY") or os.environ.get("OPENWEATHERMAP_API_KEY") or "").strip()
        if owm_key:
            try:
                url = "https://api.openweathermap.org/data/2.5/weather"
                params = {
                    "lat": str(_KMOU_LAT),
                    "lon": str(_KMOU_LON),
                    "appid": owm_key,
                    "units": "metric",
                }
                async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
                    res = await client.get(url, params=params, timeout=5.0)
                res.raise_for_status()
                data = res.json() or {}
            except Exception:
                data = {}

        # 2) KMA 폴백
        if not data:
            raw = await get_kmou_weather(lang=lang)
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not isinstance(payload, dict) or payload.get("status") != "success":
                return json.dumps(
                    {"status": "error", "msg": "날씨 정보를 확인 중입니다."},
                    ensure_ascii=False,
                )
            w = payload.get("weather") or {}
            raw_weather = w.get("raw") if isinstance(w, dict) else {}
            if not isinstance(raw_weather, dict):
                raw_weather = {}
            # 기상청 실황: 체감온도 없음 → wind chill(가능 시) 계산
            data = {
                "main": {
                    "temp": raw_weather.get("temp"),
                    "feels_like": raw_weather.get("feels_like"),
                },
                "wind": {"speed": raw_weather.get("wind_speed")},
            }

        main = data.get("main") if isinstance(data, dict) else {}
        wind = data.get("wind") if isinstance(data, dict) else {}
        if not isinstance(main, dict):
            main = {}
        if not isinstance(wind, dict):
            wind = {}

        temp = float(main.get("temp") or 0.0)
        feels_raw = main.get("feels_like")
        feels = float(feels_raw) if feels_raw is not None else temp
        wind_speed = float(wind.get("speed") or 0.0)
        wind_text = _wind_intensity_desc_ko(wind_speed)

        if feels_raw is None:
            feels = float(_wind_chill_c(temp, wind_speed))

        return json.dumps(
            {
                "status": "success",
                "temp": temp,
                "feels_like": feels,
                "wind_speed": wind_speed,
                "wind_text": wind_text,
            },
            ensure_ascii=False,
        )
    except Exception:
        return json.dumps({"status": "error", "msg": "날씨 정보를 확인 중입니다."}, ensure_ascii=False)

# =========================
# 2) 버스 필터링 로직 최적화 (ODsay) — 요청 교정본 반영
# =========================

async def get_bus_arrival(bus_number: str = None, direction: str = None, lang: str = "ko") -> str:
    """
    190번 버스 도착정보(OUT 고정 / 03053)
    - UI는 main.py에서 BasicCard/ListCard로 구성(요구사항: BasicCard thumbnail 제거)
    - 반환: json 문자열
    """
    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    # 190만 지원
    req_num = _extract_digits(bus_number) if bus_number else "190"
    if req_num and req_num != "190":
        return json.dumps({"status": "error", "msg": "현재는 190번 버스만 지원합니다."}, ensure_ascii=False)

    # OUT 고정: 해양대입구(남포/시내행)
    station_id = "03053"

    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps({"status": "error", "msg": "공공데이터 API 키(DATA_GO_KR_SERVICE_KEY)가 없습니다."}, ensure_ascii=False)

    target_bus_num = "190"

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
        return json.dumps({"status": "empty", "msg": "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"}, ensure_ascii=False)

    if not items:
        # 공공데이터 장애/비정상 응답(보수적 문구)
        return json.dumps(
            {"status": "error", "msg": "현재 2026-01-20 실시간 버스 정보가 서버에서 응답하지 않습니다", "detail": last_err or "empty"},
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
        return json.dumps({"status": "empty", "msg": "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"}, ensure_ascii=False)

    b1 = found_190.get("bus1") if isinstance(found_190, dict) else None
    b2 = found_190.get("bus2") if isinstance(found_190, dict) else None

    # 다음 버스(min1) 없으면: 운행 없음으로 처리
    if (not isinstance(b1, dict)) or (not str(b1.get("min") or "").strip()):
        return json.dumps({"status": "empty", "msg": "현재 운행 중인 190번 버스가 없습니다 (차고지 대기 중)"}, ensure_ascii=False)

    min1 = str(b1.get("min") or "").strip()
    st1 = str(b1.get("stop") or "").strip() or "?"

    min2 = ""
    st2 = ""
    if isinstance(b2, dict):
        min2 = str(b2.get("min") or "").strip()
        st2 = str(b2.get("stop") or "").strip()

    return json.dumps(
        {
            "status": "success",
            "line": "190",
            "direction": "OUT",
            "station_id": station_id,
            "station_label": "해양대입구(남포/시내행)",
            "bus1": {"min": min1, "stop": st1},
            "bus2": {"min": (min2 or None), "stop": (st2 or None)},
        },
        ensure_ascii=False,
    )

_BUS_190_KMOU_MAIN_TIMETABLE: Dict[str, List[str]] = {
    "Mon": ["04:55", "05:10", "05:25", "05:40", "05:55", "06:11", "06:25", "06:40", "06:55", "07:10", "07:27", "07:45", "08:04", "08:22", "08:41", "09:01", "09:20", "09:40", "09:59", "10:20", "10:40", "11:00", "11:20", "11:43", "12:02", "12:21", "12:40", "12:59", "13:18", "13:37", "13:56", "14:15", "14:34", "14:54", "15:12", "15:29", "15:47", "16:07", "16:26", "16:45", "17:04", "17:23", "17:42", "18:01", "18:19", "18:39", "18:57", "19:18", "19:37", "19:56", "20:14", "20:34", "20:53", "21:12", "21:30", "21:49"],
    "Tue": ["04:55", "05:10", "05:25", "05:40", "05:55", "06:10", "06:25", "06:40", "06:55", "07:10", "07:27", "07:45", "08:04", "08:23", "08:43", "09:00", "09:19", "09:39", "10:00", "10:20", "10:39", "11:00", "11:20", "11:43", "12:02", "12:21", "12:40", "12:59", "13:18", "13:37", "13:56", "14:15", "14:34", "14:53", "15:13", "15:28", "15:48", "16:07", "16:26", "16:45", "17:04", "17:23", "17:42", "18:01", "18:20", "18:39", "18:58", "19:17", "19:37", "19:56", "20:15", "20:34", "20:53", "21:11", "21:30", "21:49"],
    "Wed": ["04:55", "05:10", "05:25", "05:40", "05:55", "06:10", "06:25", "06:40", "06:55", "07:10", "07:27", "07:45", "08:04", "08:23", "08:42", "09:01", "09:20", "09:40", "10:00", "10:20", "10:40", "10:59", "11:19", "11:43", "12:02", "12:21", "12:40", "12:59", "13:18", "13:37", "13:56", "14:15", "14:34", "14:53", "15:12", "15:28", "15:48", "16:07", "16:26", "16:45", "17:04", "17:23", "17:42", "18:01", "18:20", "18:39", "18:58", "19:18", "19:36", "19:56", "20:15", "20:34", "20:52", "21:12", "21:31", "21:49"],
    "Thu": ["04:55", "05:10", "05:25", "05:40", "05:55", "06:10", "06:25", "06:40", "06:55", "07:10", "07:28", "07:45", "08:04", "08:22", "08:42", "09:01", "09:20", "09:39", "10:00", "10:20", "10:40", "11:00", "11:19", "11:43", "12:03", "12:21", "12:40", "12:59", "13:18", "13:37", "13:56", "14:16", "14:34", "14:53", "15:13", "15:29", "15:48", "16:07", "16:25", "16:45", "17:04", "17:23", "17:42", "18:01", "18:20", "18:39", "18:58", "19:18", "19:37", "19:56", "20:15", "20:34", "20:53", "21:11", "21:30", "21:49"],
    "Fri": ["04:55", "05:10", "05:25", "05:40", "05:55", "06:10", "06:25", "06:40", "06:55", "07:10", "07:27", "07:45", "08:03", "08:22", "08:41", "09:00", "09:20", "09:39", "10:00", "10:20", "10:40", "11:00", "11:20", "11:43", "12:02", "12:21", "12:40", "12:59", "13:18", "13:38", "13:56", "14:15", "14:34", "14:53", "15:12", "15:28", "15:48", "16:07", "16:26", "16:45", "17:04", "17:23", "17:42", "18:01", "18:20", "18:39", "18:57", "19:18", "19:36", "19:55", "20:14", "20:34", "20:53", "21:12", "21:30", "21:50"],
    "Sat": ["04:55", "05:12", "05:29", "05:46", "06:03", "06:20", "06:39", "06:55", "07:12", "07:30", "07:46", "08:04", "08:24", "08:47", "09:09", "09:31", "09:53", "10:14", "10:36", "10:59", "11:21", "11:43", "12:05", "12:26", "12:49", "13:11", "13:33", "13:55", "14:17", "14:38", "14:59", "15:16", "15:32", "15:52", "16:12", "16:34", "16:57", "17:18", "17:40", "18:02", "18:23", "18:44", "19:07", "19:26", "19:46", "20:07", "20:26", "20:47", "21:07", "21:28", "21:49"],
    "Holiday": ["04:55", "05:14", "05:33", "05:52", "06:12", "06:32", "06:50", "07:10", "07:29", "07:48", "08:07", "08:33", "08:58", "09:24", "09:49", "10:15", "10:38", "11:00", "11:21", "11:41", "12:06", "12:31", "12:56", "13:22", "13:47", "14:13", "14:36", "14:58", "15:19", "15:39", "16:04", "16:29", "16:54", "17:20", "17:45", "18:11", "18:34", "18:56", "19:16", "19:37", "19:56", "20:17", "20:40", "21:02", "21:25", "21:49"],
}

_BUS_190_KMOU_MAIN_WEEKDAY_SCHEDULE_SIMPLE: List[str] = [
    "04:55",
    "05:10", "05:25", "05:40", "05:55",
    "06:10", "06:25", "06:40", "06:55",
    "07:10", "07:27", "07:45",
    "08:04", "08:23", "08:42",
    "09:01", "09:20", "09:40",
    "10:00", "10:20", "10:40",
    "11:00", "11:20", "11:43",
    "12:02", "12:21", "12:40", "12:59",
    "13:18", "13:37", "13:56",
    "14:15", "14:34", "14:54",
    "15:12", "15:29", "15:47",
    "16:07", "16:26", "16:45",
    "17:04", "17:23", "17:42",
    "18:01", "18:19", "18:39", "18:57",
    "19:18", "19:37", "19:56",
    "20:14", "20:34", "20:53",
    "21:12", "21:30", "21:49"
]

async def get_bus_190_kmou_main_next_departures(now_hhmm: Optional[str] = None, date_yyyymmdd: Optional[str] = None) -> str:
    import pytz

    kst = pytz.timezone("Asia/Seoul")
    now_dt = datetime.now(kst)
    if date_yyyymmdd:
        digits = re.sub(r"\D+", "", str(date_yyyymmdd))
        if len(digits) == 8:
            try:
                dt_naive = datetime(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), now_dt.hour, now_dt.minute)
                now_dt = kst.localize(dt_naive)
            except Exception:
                pass
    if now_hhmm:
        mm = _hhmm_to_minutes(now_hhmm)
        if mm is not None:
            now_dt = now_dt.replace(hour=mm // 60, minute=mm % 60, second=0, microsecond=0)

    day_key = "Weekday"
    times = _BUS_190_KMOU_MAIN_WEEKDAY_SCHEDULE_SIMPLE[:]
    cur_m = now_dt.hour * 60 + now_dt.minute
    minutes = []
    for t in times:
        m = _hhmm_to_minutes(t)
        if m is not None:
            minutes.append((m, t))
    minutes.sort(key=lambda x: x[0])

    next1 = next(((m, t) for (m, t) in minutes if m > cur_m), None)
    if not next1:
        last = minutes[-1][1] if minutes else None
        return json.dumps(
            {
                "status": "ENDED",
                "stop_name": "해양대구본관",
                "route_number": "190",
                "day_type": day_key,
                "now": now_dt.strftime("%H:%M"),
                "next": None,
                "next2": None,
                "remaining_min": None,
                "last_time": last,
            },
            ensure_ascii=False,
        )

    rem1 = int(next1[0] - cur_m)
    idx = 0
    for i, (m, t) in enumerate(minutes):
        if t == next1[1] and m == next1[0]:
            idx = i
            break
    next2 = minutes[idx + 1] if idx + 1 < len(minutes) else None
    rem2 = int(next2[0] - cur_m) if next2 else None

    status = "PRE_DEPARTURE" if rem1 > 0 else "ACTIVE"
    return json.dumps(
        {
            "status": status,
            "stop_name": "해양대구본관",
            "route_number": "190",
            "day_type": day_key,
            "now": now_dt.strftime("%H:%M"),
            "next": {"time": next1[1], "remaining_min": rem1},
            "next2": ({"time": next2[1], "remaining_min": rem2} if next2 else None),
            "remaining_min": rem1,
        },
        ensure_ascii=False,
    )

# =========================
# 2-1) 190 버스 트래커 (ARA_190_Bus_Tracker)
# - getBusLocation() 응답(= items 배열)을 검증하여 실시간 위치를 제공
# - items가 비었거나(또는 좌표 누락) 검증 불가이면 출발(운행) 시간표 로직으로 폴백
# =========================

# 운행 시간(첫차/막차) — 공개 정보 기반 기본값(환경변수로 오버라이드 가능)
_BUS_190_FIRST_BUS_HHMM = (os.environ.get("ARA_BUS_190_FIRST_BUS_HHMM") or "04:55").strip()
_BUS_190_LAST_BUS_HHMM = (os.environ.get("ARA_BUS_190_LAST_BUS_HHMM") or "21:49").strip()

# 실시간 위치 API(프로젝트 외부 연동용)
# - 이 레포에는 "부산 BIMS 차량별 GPS" 공식 엔드포인트가 포함되어 있지 않아, URL/파라미터는 환경변수로 주입합니다.
_BUS_190_LOCATION_URL = (os.environ.get("ARA_BUS_190_LOCATION_URL") or "").strip()
_BUS_190_LOCATION_TIMEOUT_SECONDS = float(os.environ.get("ARA_BUS_190_LOCATION_TIMEOUT_SECONDS") or "2.5")
_BUS_190_LOCATION_AUTH = (os.environ.get("ARA_BUS_190_LOCATION_AUTH") or "").strip()  # 예: "Bearer xxx"
_BUS_190_LOCATION_PARAMS_JSON = (os.environ.get("ARA_BUS_190_LOCATION_PARAMS_JSON") or "").strip()  # 예: {"routeNo":"190"}

# 190번 주요 정류장명(검증용) — 공개된 노선 안내(웹)에서 확보한 "주요 정류장" 기반
# NOTE: 실제 API의 bstopNm 표기는 괄호/중점/공백 등이 다를 수 있으므로 정규화 비교합니다.
_ROUTE_190_STATIONS_KO: List[str] = [
    "해양대구본관",
    "해양대방파제입구",
    "해양대승선생활관",
    "해양대입구",
    "에덴금호아파트",
    "동삼혁신지구입구",
    "동삼국민은행앞교차로",
    "동삼시장",
    "일동미라주아파트",
    "동삼삼거리",
    "동삼주공",
    "영도구청",
    "청학주유소",
    "청학동부산은행",
    "SK부산저유소",
    "HJ중공업",
    "한성맨션",
    "교통순찰대 센트럴에일린의뜰",
    "교통순찰대·센트럴 에일린의뜰",
    "해동병원",
    "영도우체국",
    "대교동",
    "영도대교 남포역",
    "영도대교·남포역",
    "부산데파트",
    "중앙역 부산우체국",
    "중앙역·부산우체국",
    "영주동",
    "부산역",
    "초량시장입구",
    "부산고교",
    "화신아파트",
    "동일파크맨션",
    "영주삼거리",
    "시민아파트",
]

def _norm_bstop_name(name: str) -> str:
    s = str(name or "")
    # 괄호 내용 제거(예: "해양대입구(남포/시내행)" → "해양대입구")
    s = re.sub(r"\([^)]*\)", "", s)
    # 중점/구분자 제거
    s = s.replace("·", " ").replace("•", " ")
    # 공백 제거
    s = re.sub(r"\s+", "", s)
    # 한글/영문/숫자만 남김(비교 안정화)
    s = re.sub(r"[^0-9A-Za-z가-힣_]", "", s)
    return s.lower()

_ROUTE_190_STATIONS_NORM = {_norm_bstop_name(x) for x in _ROUTE_190_STATIONS_KO if x}

def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).strip())
    except Exception:
        return None

def _extract_items_from_bus_location_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    getBusLocation() 응답에서 items 배열 추출.
    - 요구사항: API 'items' array 검증. 비었거나 None이면 [] 반환.
    """
    if payload is None:
        return []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return []
    if not isinstance(payload, dict):
        return []

    items = payload.get("items", None)
    # 흔한 중첩 케이스도 보수적으로 지원
    if items is None:
        items = _safe_get(payload, "response", "body", "items", default=None)
    if items is None:
        items = _safe_get(payload, "response", "body", "items", "item", default=None)

    # 단일 dict → list로 승격
    if isinstance(items, dict):
        if "item" in items and isinstance(items.get("item"), list):
            items = items.get("item")
        else:
            items = [items]
    if items is None:
        return []
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
    return out

async def _get_bus_190_location_api_payload() -> Any:
    """
    외부 위치 API 호출.
    - 이 레포에서는 URL/파라미터를 강제하지 않고 환경변수로만 주입합니다.
    - URL이 없으면 items=[]로 취급되어 시간표 폴백으로 진행됩니다.
    """
    if not _BUS_190_LOCATION_URL:
        return {"items": []}

    params: Dict[str, Any] = {}
    if _BUS_190_LOCATION_PARAMS_JSON:
        try:
            parsed = json.loads(_BUS_190_LOCATION_PARAMS_JSON)
            if isinstance(parsed, dict):
                params = parsed
        except Exception:
            params = {}

    extra_headers: Dict[str, str] = {}
    if _BUS_190_LOCATION_AUTH:
        extra_headers["Authorization"] = _BUS_190_LOCATION_AUTH

    try:
        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers={**HEADERS, **extra_headers}) as client:
            res = await client.get(_BUS_190_LOCATION_URL, params=params, timeout=_BUS_190_LOCATION_TIMEOUT_SECONDS)
        res.raise_for_status()
        # JSON 우선
        try:
            return res.json()
        except Exception:
            # JSON이 아니면 안전하게 실패 처리(좌표 환각 금지)
            return {"items": []}
    except Exception:
        return {"items": []}

def _bus_190_departure_schedule_payload(now_dt: datetime) -> Dict[str, Any]:
    """
    items가 없을 때의 폴백(운행 시간표/운행종료 판단).
    - anti_hallucination_rules: current_time > last_bus_time → SERVICE_ENDED
    """
    first_m = _hhmm_to_minutes(_BUS_190_FIRST_BUS_HHMM)
    last_m = _hhmm_to_minutes(_BUS_190_LAST_BUS_HHMM)
    cur_m = now_dt.hour * 60 + now_dt.minute

    # 안전장치: 시간표 파싱 실패 시 보수적으로 "확인 중"
    if first_m is None or last_m is None:
        return {
            "status": "ACTIVE",
            "data": {
                "bus_id": None,
                "location": {"lat": None, "lng": None},
                "remaining_time": None,
                "message": "DEPARTURE_SCHEDULE: 190번 버스 운행 시간표 데이터가 올바르지 않아 확인 중입니다.",
            },
        }

    if cur_m > last_m:
        return {
            "status": "ENDED",
            "data": {
                "bus_id": None,
                "location": {"lat": None, "lng": None},
                "remaining_time": None,
                "message": "SERVICE_ENDED",
            },
        }

    if cur_m < first_m:
        remain = first_m - cur_m
        return {
            "status": "PRE_DEPARTURE",
            "data": {
                "bus_id": None,
                "location": {"lat": None, "lng": None},
                "remaining_time": f"{remain}분",
                "message": f"DEPARTURE_SCHEDULE: 첫차({_BUS_190_FIRST_BUS_HHMM})까지 약 {remain}분 남았습니다. (막차 {_BUS_190_LAST_BUS_HHMM})",
            },
        }

    # 운행 시간 내이지만 위치 데이터가 없을 수 있음(차고지 대기/서버 미응답 등)
    return {
        "status": "ACTIVE",
        "data": {
            "bus_id": None,
            "location": {"lat": None, "lng": None},
            "remaining_time": None,
            "message": f"DEPARTURE_SCHEDULE: 현재 실시간 위치 데이터(items)가 없어 운행 시간만 안내합니다. (첫차 {_BUS_190_FIRST_BUS_HHMM} / 막차 {_BUS_190_LAST_BUS_HHMM})",
        },
    }

async def get_bus_190_tracker(now_hhmm: Optional[str] = None, date_yyyymmdd: Optional[str] = None) -> str:
    """
    ARA_190_Bus_Tracker
    - step_1: getBusLocation() 응답 파싱
    - step_2:
      - items 존재 + 좌표 유효: REAL_TIME_TRACKING
      - items 비어있음/무효: DEPARTURE_SCHEDULE(운행시간/운행종료)
    반환: output_template 준수(JSON 문자열)
    """
    # 기준 시각(KST, 테스트 오버라이드 지원)
    now_dt = _reference_datetime()
    if date_yyyymmdd:
        digits = re.sub(r"\D+", "", str(date_yyyymmdd))
        if len(digits) == 8:
            try:
                now_dt = datetime(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), now_dt.hour, now_dt.minute, tzinfo=_KST)
            except Exception:
                pass
    if now_hhmm:
        mm = _hhmm_to_minutes(now_hhmm)
        if mm is not None:
            now_dt = now_dt.replace(hour=mm // 60, minute=mm % 60, second=0, microsecond=0)

    payload = await _get_bus_190_location_api_payload()
    items = _extract_items_from_bus_location_payload(payload)

    # items 검증 실패/빈 배열 → 시간표 폴백
    if not items:
        return json.dumps(_bus_190_departure_schedule_payload(now_dt), ensure_ascii=False)

    # items가 있어도 좌표가 null/파싱 불가이면 환각 금지 → 시간표 폴백
    candidates: List[Dict[str, Any]] = []
    for it in items:
        lat = _as_float(it.get("lat") if isinstance(it, dict) else None)
        lon = _as_float(it.get("lon") if isinstance(it, dict) else None)
        if lat is None:
            lat = _as_float(it.get("latitude") if isinstance(it, dict) else None)
        if lon is None:
            lon = _as_float(it.get("lng") if isinstance(it, dict) else None)
        if lon is None:
            lon = _as_float(it.get("longitude") if isinstance(it, dict) else None)

        if lat is None or lon is None:
            continue

        car_no = (it.get("carNo") or it.get("car_no") or it.get("bus_id") or it.get("id")) if isinstance(it, dict) else None
        bstop_nm = (it.get("bstopNm") or it.get("bstopnm") or it.get("stopName") or it.get("bstop_name")) if isinstance(it, dict) else None

        verified_stop = False
        if bstop_nm:
            verified_stop = _norm_bstop_name(str(bstop_nm)) in _ROUTE_190_STATIONS_NORM

        candidates.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "carNo": str(car_no) if car_no is not None else None,
                "bstopNm": str(bstop_nm) if bstop_nm is not None else None,
                "verified_stop": bool(verified_stop),
            }
        )

    if not candidates:
        return json.dumps(_bus_190_departure_schedule_payload(now_dt), ensure_ascii=False)

    # 검증된 정류장명을 우선 선택(없으면 첫 후보)
    picked = next((c for c in candidates if c.get("verified_stop") is True), candidates[0])
    ver_txt = "OK" if picked.get("verified_stop") else "FAIL"
    stop_txt = picked.get("bstopNm") or "알 수 없음"
    bus_id = picked.get("carNo")

    out = {
        "status": "ACTIVE",
        "data": {
            "bus_id": bus_id,
            "location": {"lat": picked["lat"], "lng": picked["lon"]},
            "remaining_time": None,
            "message": f"REAL_TIME_TRACKING: 차량 {bus_id or '미상'} / 정류장 {stop_txt} (정류장명 검증: {ver_txt})",
        },
    }
    return json.dumps(out, ensure_ascii=False)

async def get_bus_190_tracker_busbusinfo(line_id: str = "5200190000", kmou_stop_id: str = "04001") -> str:
    import xml.etree.ElementTree as ET

    now_dt = _reference_datetime()
    last_updated = now_dt.isoformat(timespec="seconds")

    if not DATA_GO_KR_SERVICE_KEY:
        return json.dumps(
            {
                "status": "ENDED",
                "realtime_buses": [],
                "departure_info": {"eta_minutes": None, "message": "정보 없음"},
                "last_updated": last_updated,
            },
            ensure_ascii=False,
        )

    loc_url = "http://apis.data.go.kr/6260000/BusBusInfoService/getBusLocationList"
    arr_url = "http://apis.data.go.kr/6260000/BusBusInfoService/getBusArrivalList"
    timeout_s = float(os.environ.get("ARA_BUS_190_TIMEOUT_SECONDS", "2.5"))
    start_radius_m = float(os.environ.get("ARA_BUS_190_START_RADIUS_M", "5000"))

    def _xml_ok(xml_text: str) -> bool:
        try:
            root = ET.fromstring(xml_text or "")
        except Exception:
            return False
        code = (root.findtext(".//resultCode") or "").strip()
        return code in {"00", "0"}

    def _parse_items(xml_text: str) -> List[Dict[str, str]]:
        try:
            root = ET.fromstring(xml_text or "")
        except Exception:
            return []
        out: List[Dict[str, str]] = []
        for items_el in root.findall(".//items"):
            for it in items_el.findall("./item"):
                d: Dict[str, str] = {}
                for child in list(it):
                    if child.tag and child.text is not None:
                        d[child.tag] = child.text
                if d:
                    out.append(d)
            if out:
                break
        return out

    def _pick_first(d: Dict[str, Any], keys: List[str]) -> Optional[str]:
        for k in keys:
            v = d.get(k)
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return None

    async def _call_xml(url: str, params: Dict[str, Any]) -> str:
        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=HEADERS) as client:
            res = await client.get(url, params=params, timeout=timeout_s)
        res.raise_for_status()
        return res.text or ""

    async def _fetch_locations() -> List[Dict[str, Any]]:
        line = (line_id or "").strip()
        if not line:
            return []
        candidates = [
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "lineId": line},
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "lineid": line},
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "line_id": line},
        ]
        xml_text = ""
        for p in candidates:
            try:
                xml_text = await _call_xml(loc_url, p)
                if _xml_ok(xml_text):
                    items = _parse_items(xml_text)
                    if items:
                        break
            except Exception:
                continue
        items = _parse_items(xml_text) if (_xml_ok(xml_text) and xml_text) else []
        buses: List[Dict[str, Any]] = []
        for it in items:
            car = _pick_first(it, ["carNo", "carno", "car_no", "vehId", "vhclNo", "busNo"])
            lat = _as_float(_pick_first(it, ["lat", "gpsLat", "y", "gpsy", "posY", "latitude"]))
            lon = _as_float(_pick_first(it, ["lng", "lon", "x", "gpsx", "posX", "longitude"]))
            if lat is None or lon is None:
                continue
            buses.append({"carNo": (car or None), "lat": float(lat), "lng": float(lon)})
        return buses

    async def _fetch_departure_eta() -> Optional[int]:
        stop_id = (kmou_stop_id or "").strip()
        if not stop_id:
            return None
        candidates = [
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "bstopid": stop_id},
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "bstopId": stop_id},
            {"serviceKey": DATA_GO_KR_SERVICE_KEY, "bstop_id": stop_id},
        ]
        xml_text = ""
        for p in candidates:
            try:
                xml_text = await _call_xml(arr_url, p)
                if _xml_ok(xml_text):
                    items = _parse_items(xml_text)
                    if items:
                        break
            except Exception:
                continue
        items = _parse_items(xml_text) if (_xml_ok(xml_text) and xml_text) else []
        if not items:
            return None

        target_line = (line_id or "").strip()
        picked: Optional[Dict[str, str]] = None
        for it in items:
            line_val = _pick_first(it, ["lineId", "lineid", "line_id", "routeId", "routeid", "route_id", "lineno", "lineNo", "routeno"])
            if target_line and line_val and str(line_val).strip() == target_line:
                picked = it
                break
        if picked is None:
            picked = items[0]

        raw_min = _pick_first(picked, ["min1", "remainMin", "remain_min", "eta", "arrtime", "arrTime", "time"])
        if not raw_min:
            return None
        digits = re.sub(r"\D+", "", str(raw_min))
        if not digits:
            return None
        try:
            return int(digits)
        except Exception:
            return None

    realtime = await _fetch_locations()
    any_near = False
    for b in realtime:
        near, _ = _is_near_kmou(b.get("lat"), b.get("lng"), radius_m=start_radius_m)
        if near:
            any_near = True
            break

    eta_min = None
    if (not realtime) or (not any_near):
        eta_min = await _fetch_departure_eta()
    else:
        eta_min = await _fetch_departure_eta()

    first_m = _hhmm_to_minutes(_BUS_190_FIRST_BUS_HHMM)
    last_m = _hhmm_to_minutes(_BUS_190_LAST_BUS_HHMM)
    cur_m = now_dt.hour * 60 + now_dt.minute

    if eta_min is not None:
        dep_msg = f"출발 예정 {eta_min}분"
    else:
        if last_m is not None and cur_m > last_m:
            dep_msg = "운행 종료"
        else:
            dep_msg = "정보 없음"

    if eta_min is not None and ((not realtime) or (not any_near)):
        status = "PRE_DEPARTURE"
    elif realtime:
        status = "ACTIVE"
    else:
        status = "ENDED" if dep_msg == "운행 종료" else "ENDED"

    return json.dumps(
        {
            "status": status,
            "realtime_buses": [{"carNo": b.get("carNo"), "lat": b.get("lat"), "lng": b.get("lng")} for b in realtime],
            "departure_info": {"eta_minutes": eta_min, "message": dep_msg},
            "last_updated": last_updated,
        },
        ensure_ascii=False,
    )

# =========================
# 3) 맛집/의료 (기존 기능 유지)
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

    def _addr_is_yeongdo(addr: str) -> bool:
        a = (addr or "").strip()
        if not a:
            return False
        al = a.lower()
        # Kakao 주소는 보통 "부산 영도구 ..." 형태
        return ("영도구" in a) or ("yeongdo-gu" in al) or ("yeongdo gu" in al) or ("yeongdo" in al and "busan" in al)

    kakao_key = (os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    if kakao_key:
        try:
            url = "https://dapi.kakao.com/v2/local/search/keyword.json"
            # 영도구 결과를 유도(검색 쿼리만 보강; 결과는 좌표/주소로 재검증)
            query2 = f"{q} 영도구"
            # 영도구 전체를 커버하도록 반경 확대(중심: KMOU)
            radius_m = int(os.environ.get("ARA_KAKAO_YEONGDO_RADIUS_M", "20000"))
            radius_m = max(1000, min(radius_m, 20000))
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers={"Authorization": f"KakaoAK {kakao_key}"}) as client:
                # 필터링으로 0건이 될 수 있어 size는 여유 있게 요청
                res = await client.get(
                    url,
                    params={
                        "query": query2,
                        "x": str(_KMOU_LON),
                        "y": str(_KMOU_LAT),
                        "radius": str(radius_m),
                        "size": "15",
                    },
                    timeout=2.5,
                )
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

                # 지오펜싱(요구사항): 부산광역시 영도구 내만 허용 (좌표는 검색/필터에만 사용)
                near, dist_m = _is_near_kmou(lat, lon, radius_m=float(radius_m))
                if not near:
                    continue
                if not _addr_is_yeongdo(addr):
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
            # 최후 폴백: 승인된 사용자 제보(관리자 검수 후)에서만 검색
            try:
                from database import search_approved_contributions
                contrib = search_approved_contributions(q, limit=limit_n)
                if contrib:
                    return json.dumps({"status": "success", "query": q, "restaurants": contrib}, ensure_ascii=False)
            except Exception:
                pass
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
            if not any(k in desc for k in ["영도구", "영도", "해양대", "동삼동", "흰여울"]):
                continue

            out.append({"name": name, "category": cat, "description": desc, "recommendation": rec, "source": "places.csv"})
            if len(out) >= limit_n:
                break

        if not out:
            # 최후 폴백: 승인된 사용자 제보(관리자 검수 후)에서만 검색
            try:
                from database import search_approved_contributions
                contrib = search_approved_contributions(q, limit=limit_n)
                if contrib:
                    return json.dumps({"status": "success", "query": q, "restaurants": contrib}, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({"status": "empty", "msg": "조건에 맞는 부산광역시 영도구 맛집을 찾지 못했습니다."}, ensure_ascii=False)
        return json.dumps({"status": "success", "query": q, "restaurants": out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "msg": str(e)}, ensure_ascii=False)

async def get_random_yeongdo_restaurant(limit_pool: int = 15) -> str:
    import random

    kakao_key = (os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    limit_n = max(5, min(int(limit_pool or 15), 15))

    def _is_cafe_blob(name: str, cat: str) -> bool:
        blob = f"{name} {cat}".lower()
        return ("카페" in blob) or ("커피" in blob) or ("cafe" in blob) or ("coffee" in blob)

    if kakao_key:
        try:
            url = "https://dapi.kakao.com/v2/local/search/keyword.json"
            headers = {"Authorization": f"KakaoAK {kakao_key}"}
            async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=headers) as client:
                res = await client.get(
                    url,
                    params={
                        "query": "영도구 맛집",
                        "x": str(_KMOU_LON),
                        "y": str(_KMOU_LAT),
                        "radius": str(max(1000, min(int(os.environ.get("ARA_KAKAO_YEONGDO_RADIUS_M", "20000")), 20000))),
                        "size": str(limit_n),
                    },
                    timeout=2.5,
                )
            res.raise_for_status()
            data = res.json() if res is not None else {}
            docs = (data.get("documents") or []) if isinstance(data, dict) else []
            candidates: List[Dict[str, Any]] = []
            for d in docs:
                name = (d.get("place_name") or "").strip()
                addr = (d.get("road_address_name") or d.get("address_name") or "").strip()
                phone = (d.get("phone") or "").strip()
                link = (d.get("place_url") or "").strip()
                cat = (d.get("category_name") or d.get("category_group_name") or "").strip()
                if addr and ("영도구" not in addr) and ("영도" not in addr):
                    continue
                if _is_cafe_blob(name, cat):
                    continue
                if not name:
                    continue
                candidates.append({"name": name, "addr": addr, "tel": phone, "link": link, "source": "kakao"})
            if candidates:
                picked = random.choice(candidates)
                return json.dumps({"status": "success", "restaurant": picked}, ensure_ascii=False)
        except Exception:
            pass

    try:
        path = os.path.join(os.path.dirname(__file__), "places.csv")
        if not os.path.exists(path):
            return json.dumps({"status": "empty", "msg": "정보 없음"}, ensure_ascii=False)
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        candidates = []
        for r in rows:
            name = (r.get("name") or r.get("temp-fixname") or "").strip()
            cat = (r.get("category") or "").strip()
            desc = (r.get("description") or "").strip()
            rec = (r.get("recommendation") or "").strip()
            if not name:
                continue
            if _is_cafe_blob(name, cat):
                continue
            if not any(k in (desc or "") for k in ["영도구", "영도", "해양대", "동삼동", "흰여울"]):
                continue
            candidates.append({"name": name, "addr": desc, "tel": "", "link": "", "source": "places.csv", "recommendation": rec})
        if not candidates:
            return json.dumps({"status": "empty", "msg": "정보 없음"}, ensure_ascii=False)
        picked = random.choice(candidates)
        return json.dumps({"status": "success", "restaurant": picked}, ensure_ascii=False)
    except Exception:
        return json.dumps({"status": "empty", "msg": "정보 없음"}, ensure_ascii=False)

async def get_worknet_maritime_logistics_jobs(query: Optional[str] = None, limit: int = 5, lang: str = "ko") -> str:
    import requests
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    auth_key = (os.environ.get("WORKNET_API_KEY") or os.environ.get("WORKNET_AUTH_KEY") or "").strip()
    if not auth_key:
        return json.dumps(
            {"status": "error", "msg": ("정보를 확인 중입니다" if lang != "en" else "Data is being verified.")},
            ensure_ascii=False,
        )

    q = (query or "").strip()
    if not q:
        q = "해운 물류"

    url = "http://openapi.work.go.kr/opi/opi/opia/wantedApi.do"
    timeout_s = float(os.environ.get("ARA_WORKNET_TIMEOUT_SECONDS", "3.5"))
    display = str(max(5, min(int(limit or 5) * 3, 30)))
    params = {
        "authKey": auth_key,
        "callTp": "L",
        "returnType": "XML",
        "startPage": "1",
        "display": display,
        "keyword": q,
    }

    def _fetch_xml() -> str:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout_s, verify=HTTPX_VERIFY)
        r.raise_for_status()
        return r.text or ""

    try:
        xml_text = await asyncio.to_thread(_fetch_xml)
    except Exception:
        return json.dumps(
            {"status": "error", "msg": ("현재 채용 정보를 불러올 수 없습니다." if lang != "en" else "Unable to fetch job listings right now.")},
            ensure_ascii=False,
        )

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return json.dumps(
            {"status": "error", "msg": ("현재 채용 정보를 불러올 수 없습니다." if lang != "en" else "Unable to fetch job listings right now.")},
            ensure_ascii=False,
        )

    def _txt(path: str) -> str:
        return (root.findtext(path) or "").strip()

    code = _txt(".//resultCode")
    if code and code not in {"00", "0"}:
        return json.dumps(
            {"status": "error", "msg": ("현재 채용 정보를 불러올 수 없습니다." if lang != "en" else "Unable to fetch job listings right now.")},
            ensure_ascii=False,
        )

    keywords = ["해운", "항만", "물류", "포워딩", "선사", "해상", "maritime", "logistics", "shipping", "port"]
    out: List[Dict[str, Any]] = []

    for it in root.findall(".//wanted"):
        title = (it.findtext("wantedTitle") or it.findtext("title") or "").strip()
        company = (it.findtext("company") or it.findtext("companyNm") or it.findtext("corpNm") or "").strip()
        region = (it.findtext("region") or it.findtext("workRegion") or "").strip()
        end_date = (it.findtext("endDate") or it.findtext("receiptCloseDt") or "").strip()
        wanted_auth_no = (it.findtext("wantedAuthNo") or it.findtext("wantedno") or "").strip()
        info_url = (it.findtext("wantedInfoUrl") or "").strip()

        blob = f"{title} {company}".lower()
        if not any(k in blob for k in [k.lower() for k in keywords]):
            continue

        link = info_url
        if not link and wanted_auth_no:
            link = "https://www.work.go.kr/empInfo/empInfoSrch/list/dtlEmpSrch.do?wantedAuthNo=" + quote(wanted_auth_no)

        out.append(
            {
                "title": title,
                "company": company,
                "region": region,
                "end_date": end_date,
                "link": link,
                "wanted_auth_no": wanted_auth_no,
                "source": "worknet",
            }
        )
        if len(out) >= max(1, min(int(limit or 5), 5)):
            break

    if not out:
        return json.dumps(
            {"status": "empty", "msg": ("현재 진행 중인 해운/물류 채용 공고가 없습니다." if lang != "en" else "No maritime/logistics jobs found."), "jobs": []},
            ensure_ascii=False,
        )

    return json.dumps({"status": "success", "query": q, "jobs": out}, ensure_ascii=False)

_YOUTH_CENTER_JOB_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_YOUTH_CENTER_JOB_CACHE_TTL_SECONDS = int(os.environ.get("ARA_YOUTH_CENTER_CACHE_TTL_SECONDS", "86400"))

def _yc_cache_get(key: str) -> Optional[Dict[str, Any]]:
    item = _YOUTH_CENTER_JOB_CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > float(_YOUTH_CENTER_JOB_CACHE_TTL_SECONDS or 0):
        _YOUTH_CENTER_JOB_CACHE.pop(key, None)
        return None
    return val

def _yc_cache_set(key: str, value: Dict[str, Any]) -> None:
    _YOUTH_CENTER_JOB_CACHE[key] = (time.time(), value)

async def get_youth_center_jobs(query: str, limit: int = 5, lang: str = "ko") -> str:
    """
    온통청년/Work24(Youth Center) API: searchJob.do
    - 응답(XML) → dict(JSON) 변환 후, 필요한 필드만 추출
    - 캐시: 24h(in-memory)
    """
    import requests
    import xmltodict

    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    api_key = (os.environ.get("YOUTH_CENTER_API_KEY") or os.environ.get("WORK24_OPENAPI_KEY") or "").strip()
    if not api_key:
        api_key = "ba0aad9d-c862-410c-90ac-130b556e370e"
    if not api_key:
        return json.dumps({"status": "error", "msg": ("정보를 확인 중입니다" if lang != "en" else "Data is being verified.")}, ensure_ascii=False)

    q = (query or "").strip()
    if not q:
        q = "해운 물류"

    limit_n = max(1, min(int(limit or 5), 5))
    endpoint = "https://www.work24.go.kr/openapi/openapi/common/searchJob.do"
    timeout_s = float(os.environ.get("ARA_YOUTH_CENTER_TIMEOUT_SECONDS", "3.5"))
    num_rows = str(max(10, min(limit_n * 6, 60)))

    cache_key = f"YOUTH24:{q}:{limit_n}"
    cached = _yc_cache_get(cache_key)
    if cached is not None:
        return json.dumps(cached, ensure_ascii=False)

    def _fetch_xml(params: Dict[str, Any]) -> str:
        r = requests.get(endpoint, params=params, headers=HEADERS, timeout=timeout_s, verify=HTTPX_VERIFY)
        r.raise_for_status()
        return r.text or ""

    params_candidates = [
        {"apiKey": api_key, "keyword": q, "pageNo": "1", "numOfRows": num_rows},
        {"serviceKey": api_key, "keyword": q, "pageNo": "1", "numOfRows": num_rows},
        {"authKey": api_key, "keyword": q, "pageNo": "1", "numOfRows": num_rows},
    ]

    xml_text = ""
    for p in params_candidates:
        try:
            xml_text = await asyncio.to_thread(_fetch_xml, p)
            if xml_text:
                break
        except Exception:
            continue

    if not xml_text:
        payload = {"status": "error", "msg": ("현재 채용 정보를 불러올 수 없습니다." if lang != "en" else "Unable to fetch jobs right now.")}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)

    try:
        parsed = xmltodict.parse(xml_text)
    except Exception:
        payload = {"status": "error", "msg": ("현재 채용 정보를 불러올 수 없습니다." if lang != "en" else "Unable to fetch jobs right now.")}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)

    def _iter_dicts(node: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                out.extend(_iter_dicts(v))
        elif isinstance(node, list):
            for it in node:
                out.extend(_iter_dicts(it))
        return out

    def _extract_items(node: Any) -> List[Dict[str, Any]]:
        if isinstance(node, dict):
            for k in ["items", "itemList", "jobList", "jobs", "list"]:
                v = node.get(k)
                if v is None:
                    continue
                found = _extract_items(v)
                if found:
                    return found
            if "item" in node:
                v = node.get("item")
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
                if isinstance(v, dict):
                    return [v]
            return []
        if isinstance(node, list):
            out: List[Dict[str, Any]] = []
            for it in node:
                out.extend(_extract_items(it))
            return out
        return []

    items = _extract_items(parsed) or []
    if not items:
        payload = {"status": "empty", "msg": ("현재 진행 중인 채용 정보가 없습니다." if lang != "en" else "No jobs found."), "query": q, "jobs": []}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)

    def _pick(it: Dict[str, Any], keys: List[str]) -> str:
        for k in keys:
            v = it.get(k)
            if v is None:
                continue
            if isinstance(v, (str, int, float)):
                s = str(v).strip()
                if s:
                    return s
        return ""

    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = _pick(it, ["programNm", "programName", "title", "jobTitle", "wantedTitle", "recrtTitle", "sj"])
        summary = _pick(it, ["benefit", "benefitCn", "summary", "desc", "description", "cn", "content"])
        deadline = _pick(it, ["deadline", "ddlnDt", "endDate", "closeDt", "receiptCloseDt", "endYmd", "end_ymd"])
        detail = _pick(it, ["detailUrl", "detailURL", "url", "link", "detailPageUrl", "homepage", "pageUrl"])

        if not title:
            continue

        if detail:
            out.append(
                {
                    "title": title,
                    "summary": summary,
                    "deadline": deadline,
                    "detail_url": detail,
                }
            )
        if len(out) >= limit_n:
            break

    if not out:
        payload = {"status": "empty", "msg": ("현재 진행 중인 채용 정보가 없습니다." if lang != "en" else "No jobs found."), "query": q, "jobs": []}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)

    payload = {"status": "success", "source": "youth_center_work24", "query": q, "jobs": out}
    _yc_cache_set(cache_key, payload)
    return json.dumps(payload, ensure_ascii=False)

async def get_youth_jobs(keyword: Optional[str] = None) -> str:
    """
    Stable & Error-Free Employment Data Fetching from Youth Center API.
    - Philosophy: Stability First. Fail gracefully with helpful fallback.
    - API: https://www.youthcenter.go.kr/opi/youthPolicyList.do
    - Always returns valid JSON compatible with KakaoTalk API.
    """
    import xmltodict

    # API Configuration
    api_url = "https://www.youthcenter.go.kr/opi/youthPolicyList.do"
    api_key = "ba0aad9d-c862-410c-90ac-130b556e370e"
    default_thumbnail = "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?q=80&w=600&auto=format&fit=crop"
    timeout_seconds = 4.0  # Strict 4 seconds (Kakao limit is 5s)

    # Normalize keyword - default to '취업' if empty or vague
    q = (keyword or "").strip()
    if not q or len(q) < 2:
        q = "취업"

    try:
        # Prepare request parameters
        params = {
            "openApiVlak": api_key,
            "display": "5",
            "pageIndex": "1",
            "query": q
        }

        # Make API request with strict timeout
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/xml,text/xml,*/*"
        }

        async with httpx.AsyncClient(verify=HTTPX_VERIFY, headers=headers, timeout=timeout_seconds) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()

            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            xml_text = response.text or ""
            if not xml_text.strip():
                raise RuntimeError("Empty response")

            # Check if response is HTML (error page)
            if xml_text.lstrip().lower().startswith("<html"):
                raise RuntimeError("HTML response received instead of XML")

    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as e:
        # Connection/Timeout errors - return user-friendly message
        return json.dumps({
            "status": "error",
            "msg": "지금 정부 서버랑 연결이 조금 지연되고 있어! 잠시 후에 다시 물어봐줄래? 🔧",
            "query": q,
            "policies": []
        }, ensure_ascii=False)

    except Exception as e:
        # Log for debugging but return graceful error
        print(f"[ARA Log] Youth Jobs API Error: {e}")
        return json.dumps({
            "status": "error",
            "msg": "지금 정부 서버랑 연결이 조금 지연되고 있어! 잠시 후에 다시 물어봐줄래? 🔧",
            "query": q,
            "policies": []
        }, ensure_ascii=False)

    # Robust XML Parsing
    try:
        parsed = xmltodict.parse(xml_text)
        
        # Navigate to youthPolicyList
        youth_policy_list = None
        if isinstance(parsed, dict):
            # Try different possible structures
            if "youthPolicyList" in parsed:
                youth_policy_list = parsed["youthPolicyList"]
            elif "response" in parsed and isinstance(parsed["response"], dict):
                if "youthPolicyList" in parsed["response"]:
                    youth_policy_list = parsed["response"]["youthPolicyList"]
                elif "body" in parsed["response"] and isinstance(parsed["response"]["body"], dict):
                    if "youthPolicyList" in parsed["response"]["body"]:
                        youth_policy_list = parsed["response"]["body"]["youthPolicyList"]

        if not youth_policy_list:
            # Log raw response for debugging (status 200 but empty data)
            print(f"[ARA Log] Youth Jobs API: Status 200 but no youthPolicyList found. Response preview: {xml_text[:200]}")
            
            # Try fallback search with '청년'
            if q != "청년":
                return await get_youth_jobs("청년")
            
            return json.dumps({
                "status": "empty",
                "msg": "지금 정부 서버랑 연결이 조금 지연되고 있어! 잠시 후에 다시 물어봐줄래? 🔧",
                "query": q,
                "policies": []
            }, ensure_ascii=False)

        # Handle totalCnt check
        total_cnt = 0
        if isinstance(youth_policy_list, dict):
            total_cnt_str = str(youth_policy_list.get("totalCnt", "0") or "0")
            try:
                total_cnt = int(total_cnt_str)
            except (ValueError, TypeError):
                total_cnt = 0
        
        # If totalCnt is 0, try fallback search with '청년'
        if total_cnt == 0 and q != "청년":
            return await get_youth_jobs("청년")

        # Extract youthPolicy - handle both single dict and list
        youth_policies = []
        if isinstance(youth_policy_list, dict):
            if "youthPolicy" in youth_policy_list:
                policy_data = youth_policy_list["youthPolicy"]
                # Convert single dict to list
                if isinstance(policy_data, dict):
                    youth_policies = [policy_data]
                elif isinstance(policy_data, list):
                    youth_policies = policy_data

        # Normalize items for KakaoTalk UI
        items = []
        for policy in youth_policies:
            if not isinstance(policy, dict):
                continue

            # Extract fields
            policy_name = (
                policy.get("polyBizSjnm") or 
                policy.get("polyBizSjNm") or 
                policy.get("policyName") or 
                policy.get("name") or 
                ""
            ).strip()

            intro = (
                policy.get("polyItcnCn") or 
                policy.get("polyItcnCnNm") or 
                policy.get("intro") or 
                policy.get("summary") or 
                ""
            ).strip()

            biz_id = (
                policy.get("bizId") or 
                policy.get("bizid") or 
                ""
            ).strip()

            # Truncate intro to 40 chars for description
            intro_short = intro[:40] if len(intro) > 40 else intro

            # Build detail URL using bizId
            detail_url = f"https://www.youthcenter.go.kr/youngPlcyUnif/youngPlcyUnifDtl.do?bizId={biz_id}" if biz_id else "https://www.youthcenter.go.kr"

            if policy_name:
                items.append({
                    "policyName": policy_name,
                    "polyItcnCn": intro_short,
                    "bizId": biz_id,
                    "detail_url": detail_url,
                    "thumbnail": default_thumbnail
                })

        if not items:
            # Try fallback search if no items found
            if q != "청년":
                return await get_youth_jobs("청년")
            
            return json.dumps({
                "status": "empty",
                "msg": "지금 정부 서버랑 연결이 조금 지연되고 있어! 잠시 후에 다시 물어봐줄래? 🔧",
                "query": q,
                "policies": []
            }, ensure_ascii=False)

        # Return success response
        return json.dumps({
            "status": "success",
            "source": "youth_center_policy",
            "query": q,
            "policies": items
        }, ensure_ascii=False)

    except Exception as e:
        # XML parsing errors
        print(f"[ARA Log] Youth Jobs XML Parsing Error: {e}")
        return json.dumps({
            "status": "error",
            "msg": "지금 정부 서버랑 연결이 조금 지연되고 있어! 잠시 후에 다시 물어봐줄래? 🔧",
            "query": q,
            "policies": []
        }, ensure_ascii=False)

async def get_youth_center_info(query: Optional[str] = None, limit: int = 5, lang: str = "ko") -> str:
    import requests
    import xmltodict

    lang = (lang or "ko").strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"

    api_key = (os.environ.get("YOUTH_CENTER_API_KEY") or "").strip()
    if not api_key:
        api_key = "ba0aad9d-c862-410c-90ac-130b556e370e"

    q_raw = (query or "").strip()
    q = q_raw
    if any(k in q for k in ["세무", "회계", "법", "노무", "행정", "인사", "총무", "마케팅", "경영", "사회"]):
        q = q_raw

    limit_n = max(5, min(int(limit or 10), 10))
    endpoint_https = "https://www.youthcenter.go.kr/opi/youthPolicyList.do"
    endpoint_http_8080 = "http://www.youthcenter.go.kr:8080/opi/youthPolicyList.do"
    timeout_s = 4.0

    cache_key = f"YOUTH_POLICY:{q}:{limit_n}:{lang}"
    cached = _yc_cache_get(cache_key)
    if cached is not None:
        return json.dumps(cached, ensure_ascii=False)

    def _fetch(params: Dict[str, Any]) -> str:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/xml,text/xml,*/*"}

        last_err: Exception | None = None
        for url in [endpoint_https, endpoint_http_8080]:
            try:
                allow_redirects = False if url == endpoint_https else True
                r = requests.get(url, params=params, headers=headers, timeout=timeout_s, verify=HTTPX_VERIFY, allow_redirects=allow_redirects)
                if (r.status_code in (301, 302, 303, 307, 308)) and url == endpoint_https:
                    raise RuntimeError(f"Redirected: {r.headers.get('location')}")
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                if not r.encoding:
                    r.encoding = r.apparent_encoding or "utf-8"
                text = r.text or ""
                if text.lstrip().lower().startswith("<html"):
                    raise RuntimeError("HTML response")
                return text
            except Exception as e:
                last_err = e
                continue
        raise last_err or RuntimeError("request failed")

    def _parse_items(xml_text: str) -> List[Dict[str, Any]]:
        parsed = xmltodict.parse(xml_text)

        def _walk(node: Any):
            if isinstance(node, dict):
                yield node
                for v in node.values():
                    yield from _walk(v)
            elif isinstance(node, list):
                for it in node:
                    yield from _walk(it)

        def _as_list(v: Any) -> List[Dict[str, Any]]:
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                return [v]
            return []

        for d in _walk(parsed):
            if "youthPolicyList" in d:
                ypl = d.get("youthPolicyList")
                if isinstance(ypl, dict) and ("youthPolicy" in ypl):
                    return _as_list(ypl.get("youthPolicy"))
                return _as_list(ypl)

        for d in _walk(parsed):
            if "youthPolicy" in d:
                return _as_list(d.get("youthPolicy"))

        return []

    def _pick(it: Dict[str, Any], keys: List[str]) -> str:
        for k in keys:
            v = it.get(k)
            if v is None:
                continue
            if isinstance(v, (str, int, float)):
                s = str(v).strip()
                if s:
                    return s
        return ""

    def _normalize(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            name = _pick(it, ["policyName", "polyBizSjnm", "polyBizSjNm", "polyBizSjnmNm", "title", "name"])
            intro = _pick(it, ["polyItcnCn", "polyItcnCnNm", "intro", "summary", "cn", "content"])
            prd = _pick(it, ["bizPrdCn", "bizPrdCnNm", "bizPrd", "period"])
            url = _pick(it, ["detailUrl", "detailURL", "url", "link", "pageUrl", "homepage"])

            if not url:
                if q:
                    url = f"https://www.youthcenter.go.kr/?srchWord={quote_plus(q)}"
                else:
                    url = "https://www.youthcenter.go.kr"

            key = (name + "|" + prd + "|" + url).strip()
            if not name or key in seen:
                continue
            seen.add(key)
            out.append({"policyName": name, "polyItcnCn": intro, "bizPrdCn": prd, "detail_url": url})
            if len(out) >= limit_n:
                break
        return out

    def _request_once(query_text: str | None) -> List[Dict[str, Any]]:
        params = {"authKey": api_key, "display": "10", "pageIndex": "1"}
        if query_text:
            params["query"] = query_text
        xml_text = _fetch(params)
        items = _parse_items(xml_text)
        return _normalize(items)

    try:
        items = []
        if q:
            try:
                items = await asyncio.to_thread(_request_once, q)
            except Exception:
                items = []
        if len(items) < 5:
            try:
                more = await asyncio.to_thread(_request_once, None)
                merged = { (it.get("policyName","") + "|" + it.get("detail_url","")).strip(): it for it in items if isinstance(it, dict) }
                for it in more:
                    k2 = (it.get("policyName","") + "|" + it.get("detail_url","")).strip()
                    if k2 and k2 not in merged:
                        merged[k2] = it
                    if len(merged) >= limit_n:
                        break
                items = list(merged.values())[:limit_n]
            except Exception:
                pass

        if not items:
            payload = {"status": "empty", "msg": ("지금은 정책 정보를 찾지 못했어. 잠시 후 다시 시도해줘!" if lang != "en" else "No policies found."), "query": q, "policies": []}
            _yc_cache_set(cache_key, payload)
            return json.dumps(payload, ensure_ascii=False)

        payload = {"status": "success", "source": "youthcenter_policy", "query": q, "policies": items}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        payload = {"status": "error", "msg": ("지금은 정책 정보를 불러오지 못했어. 잠시만 기다려줘!" if lang != "en" else "Unable to fetch policies right now.")}
        _yc_cache_set(cache_key, payload)
        return json.dumps(payload, ensure_ascii=False)

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
            "description": "🚌 190번 버스(남포/시내행): 정류장ID 03053 기준 다음/다다음 도착 정보를 조회합니다(반환: JSON 문자열).",
            "parameters": {
                "type": "object",
                "properties": {
                    "bus_number": {"type": "string", "description": "예: 190 (미입력 시 190 기본값)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bus_190_tracker_busbusinfo",
            "description": "🚌 190번 버스(한국해양대 기점→남부민동): 실시간 위치(차량목록)와 기점(04001) 출발 예정(min1) 정보를 통합해 반환합니다(반환: JSON 문자열).",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string", "description": "노선 ID (기본 5200190000)"},
                    "kmou_stop_id": {"type": "string", "description": "기점 정류장 ID (기본 04001)"},
                },
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
            "name": "get_weather_info",
            "description": "🌤️ 오늘의 영도 날씨: 풍속/체감온도 포함 요약을 반환합니다(반환: JSON 문자열).",
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
            "name": "get_academic_schedule",
            "description": "📚 2026 학사일정(D-Day): 하드코딩된 2026 학사 이벤트 날짜로 D-Day를 계산합니다(웹 크롤링 금지).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "이벤트명 부분검색(예: 개강, 수강신청, 기말고사) (선택)"},
                    "today_yyyy_mm_dd": {"type": "string", "description": "기준일(YYYY-MM-DD) 테스트용 (선택)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
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
    {
        "type": "function",
        "function": {
            "name": "get_youth_center_info",
            "description": "💼 취업(온통청년): youthPolicyList(XML) → JSON으로 변환해 청년정책 목록을 반환합니다(반환: JSON 문자열).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 키워드(예: 해운 물류, 세무 회계, 청년정책)"},
                    "limit": {"type": "integer", "description": "최대 결과 수(기본 5, 최대 5)"},
                    "lang": {"type": "string", "description": "ko 또는 en(선택)"},
                },
            },
        },
    },
]