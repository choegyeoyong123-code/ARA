import os
from dotenv import load_dotenv

# .env 환경 변수 로드 (모든 커스텀 모듈 import 이전에 실행되어야 함)
load_dotenv()

import asyncio
import tempfile
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import json
import re
import time

# 커스텀 모듈은 반드시 load_dotenv() 이후 import
from database import init_db, update_conversation_feedback, get_pending_state, set_pending_state, clear_pending_state
from agent import ask_ara
from tools import get_shuttle_next_buses
from tools import get_astronomy_data
from startup_check import run_startup_checks

app = FastAPI()
templates = Jinja2Templates(directory="templates")
init_db()

@app.on_event("startup")
async def startup_diagnostics():
    """
    통합 진단: 서버 시작 시 주요 API 키 로드 상태를 터미널에 출력합니다.
    - 보안: API 키(일부 포함)를 절대 출력하지 않습니다.
    """
    # Windows(cp949) 콘솔에서는 이모지 출력이 실패할 수 있어 안전장치를 둡니다.
    # 멀티 워커(gunicorn)에서 로그가 4번 찍히지 않도록, temp 파일 락으로 1회만 출력합니다.
    lock_path = os.path.join(tempfile.gettempdir(), "ara_startup_logged.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        run_startup_checks()
        print("✅ API Key Load Success")
        # Astronomy API sync(짧은 타임아웃, 무환각)
        try:
            today = time.strftime("%Y%m%d")
            raw = await asyncio.wait_for(get_astronomy_data(today), timeout=2.0)
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
            sunset = payload.get("sunset") if isinstance(payload, dict) else None
            if payload.get("status") == "success" and sunset:
                print(f"✅ Astronomy API Sync Success: {sunset}")
        except Exception:
            pass
    except UnicodeEncodeError:
        print("API Key Load Success")
    except FileExistsError:
        # already logged by another worker
        pass

NAV_QUICK_REPLIES = [
    # 요구된 고정 순서
    {"label": "🚌 190 Bus", "action": "message", "messageText": "190 버스"},
    {"label": "🌤️ Weather", "action": "message", "messageText": "영도 날씨"},
    {"label": "🚐 Shuttle", "action": "message", "messageText": "셔틀 시간"},
    {"label": "🏫 KMOU Homepage", "action": "message", "messageText": "KMOU 홈페이지"},
    {"label": "📞 캠퍼스 연락처", "action": "message", "messageText": "캠퍼스 연락처"},
    {"label": "🍚 Restaurants", "action": "message", "messageText": "맛집"},
    {"label": "🏥 Pharmacy/Hospital", "action": "message", "messageText": "약국/병원"},
    {"label": "🎉 Festival/Events", "action": "message", "messageText": "부산 행사"},
]

def _build_quick_replies():
    """
    카카오 quickReplies는 모든 응답 하단에 상시 노출합니다.
    - 요구된 고정 네비게이션(7개)을 "항상" 포함(상시 메뉴)
    """
    return list(NAV_QUICK_REPLIES)

def _kakao_response(outputs: list[dict]):
    """
    카카오 스킬 응답 공통 래퍼
    - 반드시 {"version":"2.0","template":{"outputs":[...]}} 형식을 유지
    - 모든 응답에 quickReplies 상시 포함
    """
    return {
        "version": "2.0",
        "template": {
            "outputs": outputs,
            "quickReplies": _build_quick_replies(),
        },
    }

def _kakao_simple_text(text: str):
    # NOTE: 요구사항(카드 UI 강제)에 따라 simpleText는 사용하지 않습니다.
    # 기존 호출부 호환을 위해 basicCard로 래핑합니다.
    t = (text or "").strip()
    return _kakao_basic_card(
        title="ARA 안내",
        description=t[:450] if t else "요청을 처리할 수 없습니다.",
        buttons=[
            {"action": "message", "label": "다시 시도", "messageText": (t[:30] if t else "다시 시도")},
        ],
    )

def _kakao_basic_card(title: str, description: str, buttons: list[dict] | None = None):
    card: dict = {"title": title, "description": description}
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"basicCard": card}])

def _kakao_list_card(header_title: str, items: list[dict], buttons: list[dict] | None = None):
    card: dict = {"header": {"title": header_title}, "items": items}
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"listCard": card}])

def _kakao_auto_text(text: str):
    """
    text가 너무 길어 simpleText 제한에 걸릴 수 있으면 listCard로 완화합니다.
    - 구조화 데이터가 없을 때의 안전한 fallback(줄 단위 요약)
    """
    t = (text or "").strip()
    if len(t) <= 450:
        return _kakao_basic_card(
            title="ARA 응답",
            description=t,
            buttons=[{"action": "message", "label": "다시 조회", "messageText": "다시 조회"}],
        )

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    header = lines[0][:30] if lines else "ARA 안내"
    items: list[dict] = []
    for ln in lines[1:]:
        if ln.startswith("- "):
            title = ln[2:][:50]
            items.append({"title": title, "description": ""})
        else:
            if not items:
                items.append({"title": ln[:50], "description": ""})
            else:
                prev = items[-1].get("description", "")
                merged = (prev + ("\n" if prev else "") + ln)[:230]
                items[-1]["description"] = merged
        if len(items) >= 5:
            break

    if not items:
        return _kakao_basic_card(
            title="ARA 응답",
            description=t[:450],
            buttons=[{"action": "message", "label": "다시 조회", "messageText": "다시 조회"}],
        )
    return _kakao_list_card(header_title=header, items=items)

def _normalize_desc(s: str) -> str:
    """
    카드 description은 불렛(-, •)을 지양하고 한 문장/구 형태로 정리합니다.
    """
    if not s:
        return ""
    lines = [ln.strip() for ln in str(s).splitlines() if ln.strip()]
    # '- '로 시작하는 라인은 불렛이므로 제거하고 문장 결합
    lines = [re.sub(r"^\-\s+", "", ln) for ln in lines]
    return " / ".join(lines)[:450]

def _map_search_link(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "https://map.kakao.com"
    return "https://map.kakao.com/link/search/" + re.sub(r"\s+", "%20", q)

_KAKAO_CACHE_TTL_SECONDS = int(os.environ.get("ARA_KAKAO_CACHE_TTL_SECONDS", "60"))
_KAKAO_ASYNC_CACHE: dict[str, tuple[float, dict]] = {}
_KAKAO_INFLIGHT: set[str] = set()

def _pending_get(user_id: str | None) -> str | None:
    if not user_id:
        return None
    try:
        return get_pending_state(user_id)
    except Exception:
        return None

def _pending_set(user_id: str | None, kind: str) -> None:
    if not user_id:
        return
    try:
        set_pending_state(user_id, kind)
    except Exception:
        pass

def _pending_clear(user_id: str | None) -> None:
    if not user_id:
        return
    try:
        clear_pending_state(user_id)
    except Exception:
        pass

def _cache_get(key: str) -> dict | None:
    item = _KAKAO_ASYNC_CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > _KAKAO_CACHE_TTL_SECONDS:
        _KAKAO_ASYNC_CACHE.pop(key, None)
        return None
    return val

def _cache_set(key: str, value: dict) -> None:
    _KAKAO_ASYNC_CACHE[key] = (time.time(), value)

async def _run_with_timeout(coro, timeout: float):
    """
    카카오 5초 제한 대응:
    - asyncio.wait_for는 내부 작업이 cancel을 무시하면 반환이 지연될 수 있어,
      asyncio.wait 기반으로 "즉시 반환"을 보장합니다.
    """
    task = asyncio.create_task(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        task.cancel()
        return ("timeout", None)
    try:
        return ("ok", task.result())
    except Exception as e:
        return ("error", e)

def _is_bus_query(text: str) -> bool:
    """
    'B3' 같은 건물 코드가 버스로 오인되지 않도록 보수적으로 판별합니다.
    """
    t = (text or "").lower()
    if "버스" in t:
        return True
    if re.search(r"\b(in|out)\b", t):
        return True
    # 버스 번호는 보통 2자리 이상
    if re.search(r"\d{2,4}", t) and any(k in t for k in ["도착", "정류장", "언제", "몇", "분", "시간"]):
        return True
    return False

def _infer_direction(text: str) -> str | None:
    t = (text or "")
    tl = t.lower()
    if re.search(r"\bout\b", tl) or "진출" in t:
        return "OUT"
    if re.search(r"\bin\b", tl) or "진입" in t:
        return "IN"
    if ("학교" in t) or ("등교" in t):
        return "IN"
    if ("부산역" in t) or ("하교" in t):
        return "OUT"
    return None

def _extract_digits(text: str) -> str:
    return "".join(re.findall(r"\d+", str(text or "")))

async def _handle_structured_kakao(user_msg: str, user_id: str | None):
    """
    카카오용: 도구 결과를 구조화된 카드로 변환(정확성/형식 준수).
    """
    from tools import get_bus_arrival, get_kmou_weather, get_medical_info, get_festival_info, search_restaurants

    msg = (user_msg or "").strip()

    # 캠퍼스 연락처(오프라인): 카테고리 → 부서 → 전화하기
    if msg in {"캠퍼스 연락처", "연락처", "학교 연락처", "교내 연락처"}:
        from tools import get_campus_contacts

        raw = get_campus_contacts()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        cats = payload.get("categories") or []
        items = []
        for c in cats:
            cat = c.get("category") or ""
            cnt = c.get("count") or 0
            items.append(
                {
                    "title": (c.get("category_label") or cat)[:50],
                    "description": _normalize_desc(f"{cnt}개 / 선택하면 부서를 표시합니다."),
                    "action": "message",
                    "messageText": f"연락처 {cat}",
                }
            )
        return _kakao_list_card(
            header_title="📞 캠퍼스 연락처",
            items=items or [{"title": "연락처", "description": "표시할 항목이 없습니다.", "action": "message", "messageText": "캠퍼스 연락처"}],
            buttons=[{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
        )

    m_contact_cat = re.match(r"^연락처\s+(?P<cat>[A-Za-z_]+)\s*$", msg)
    if m_contact_cat:
        from tools import get_campus_contacts

        cat = m_contact_cat.group("cat")
        raw = get_campus_contacts(category=cat)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="📞 캠퍼스 연락처",
                description=_normalize_desc(payload.get("msg") or "해당 분류를 찾지 못했습니다."),
                buttons=[{"action": "message", "label": "분류 다시 보기", "messageText": "캠퍼스 연락처"}],
            )
        contacts = payload.get("contacts") or []
        items = []
        for it in contacts:
            office = it.get("office") or ""
            phone = it.get("phone") or ""
            items.append(
                {
                    "title": (it.get("office_label") or office)[:50],
                    "description": _normalize_desc(str(phone)),
                    "action": "message",
                    "messageText": f"전화 {office}",
                }
            )
        return _kakao_list_card(
            header_title=f"📞 {cat}",
            items=items or [{"title": "연락처", "description": "표시할 부서가 없습니다.", "action": "message", "messageText": "캠퍼스 연락처"}],
            buttons=[{"action": "message", "label": "다른 분류", "messageText": "캠퍼스 연락처"}],
        )

    m_contact_office = re.match(r"^(전화|call)\s+(?P<office>[A-Za-z_]+)\s*$", msg, flags=re.IGNORECASE)
    if m_contact_office:
        from tools import get_campus_contacts

        office = m_contact_office.group("office")
        raw = get_campus_contacts(office=office)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="📞 캠퍼스 연락처",
                description=_normalize_desc(payload.get("msg") or "해당 부서를 찾지 못했습니다."),
                buttons=[{"action": "message", "label": "분류 다시 보기", "messageText": "캠퍼스 연락처"}],
            )
        phone = payload.get("phone") or ""
        label = payload.get("office_label") or office
        # Kakao basicCard: phone action으로 즉시 전화
        return _kakao_basic_card(
            title=f"📞 {label}",
            description=_normalize_desc(str(phone)),
            buttons=[
                {"action": "phone", "label": "전화 걸기", "phoneNumber": str(phone)},
                {"action": "message", "label": "다른 연락처", "messageText": "캠퍼스 연락처"},
            ],
        )

    # 날짜/공휴일 관련 질의는 LLM 추측을 원천 차단하고 calendar_2026.json만 신뢰합니다.
    if any(k in msg for k in ["공휴일", "휴일", "연휴", "대체공휴일", "holiday"]):
        from tools import get_calendar_day_2026

        # 사용자가 날짜를 명시하지 않으면 오늘로만 확인(계산/추측 금지)
        m = re.search(r"(2026)\D?(0[1-9]|1[0-2])\D?(0[1-9]|[12]\d|3[01])", msg)
        date_yyyymmdd = time.strftime("%Y%m%d")
        if m:
            date_yyyymmdd = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        raw = get_calendar_day_2026(date_yyyymmdd)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if isinstance(payload, dict) and payload.get("status") == "success":
            day = payload.get("day") or {}
            name = (day.get("name") or day.get("summary") or "").strip() if isinstance(day, dict) else ""
            is_hol = day.get("is_holiday") if isinstance(day, dict) else None
            desc = f"{date_yyyymmdd} / " + ("휴일" if is_hol else "평일")
            if name:
                desc += f" / {name}"
            return _kakao_basic_card(
                title="2026 캘린더",
                description=_normalize_desc(desc),
                buttons=[{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
            )
        return _kakao_basic_card(
            title="2026 캘린더",
            description="Data is currently being updated for this specific date.",
            buttons=[{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
        )

    # 멀티턴 상태(맛집/의료) 처리: 버튼 → 질문 → 사용자의 상세 입력 → 검색
    pending = _pending_get(user_id)
    if pending == "restaurants":
        _pending_clear(user_id)
        raw = await search_restaurants(query=msg, limit=5)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="Restaurants",
                description=_normalize_desc(payload.get("msg") or "조건에 맞는 결과를 찾지 못했습니다."),
                buttons=[{"action": "message", "label": "다시 선택", "messageText": "맛집"}],
            )
        items = []
        for r in (payload.get("restaurants") or [])[:5]:
            name = (r.get("name") or "").strip() or "가게"
            addr = (r.get("addr") or r.get("description") or "").strip()
            items.append({"title": name[:50], "description": _normalize_desc(addr), "link": {"web": _map_search_link(name)}})
        if not items:
            return _kakao_basic_card(
                title="Restaurants",
                description="No verified facilities found within the campus vicinity",
                buttons=[{"action": "message", "label": "다시 선택", "messageText": "맛집"}],
            )
        return _kakao_list_card(
            header_title=f"영도/해양대 인근 맛집: {payload.get('query','')}",
            items=items or [{"title": "검색 결과", "description": "표시할 결과가 없습니다.", "link": {"web": _map_search_link(msg)}}],
            buttons=[{"action": "message", "label": "다른 종류", "messageText": "맛집"}],
        )

    if pending == "medical":
        _pending_clear(user_id)
        # 입력을 그대로 kind로 전달하되, 너무 모호하면 전체 조회로 완화
        kind = msg if any(k in msg for k in ["약국", "치과", "내과", "피부", "안과", "이비인후", "정형", "산부", "소아"]) else ""
        raw = await get_medical_info(kind=kind)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="Pharmacy/Hospital",
                description=_normalize_desc(payload.get("msg") or "의료 정보를 확인할 수 없습니다."),
                buttons=[{"action": "message", "label": "다시 선택", "messageText": "약국/병원"}],
            )
        items = []
        for h in (payload.get("hospitals") or [])[:5]:
            name = (h.get("name") or "의료기관").strip()
            open_label = "Currently Open" if bool(h.get("is_open")) else "Closed"
            title = f"{name} [{open_label}]"
            desc = f"{(h.get('kind') or '').strip()} / {(h.get('time') or '').strip()} / {(h.get('tel') or '').strip()} / {(h.get('addr') or '').strip()}"
            items.append({"title": title[:50], "description": _normalize_desc(desc), "link": {"web": _map_search_link(h.get("addr") or name)}})
        if not items:
            return _kakao_basic_card(
                title="Pharmacy/Hospital",
                description="No verified facilities found within the campus vicinity",
                buttons=[{"action": "message", "label": "다시 선택", "messageText": "약국/병원"}],
            )
        return _kakao_list_card(
            header_title="Pharmacy/Hospital (Open first)",
            items=items or [{"title": "검색 결과", "description": "표시할 의료기관이 없습니다.", "link": {"web": _map_search_link('영도구 약국')}}],
            buttons=[{"action": "message", "label": "다시 선택", "messageText": "약국/병원"}],
        )

    # 날씨
    if "날씨" in msg:
        raw = await get_kmou_weather()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="날씨 정보",
                description=_normalize_desc(payload.get("msg") or "날씨 정보를 확인할 수 없습니다."),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
            )
        w = payload.get("weather") or {}
        desc = f"기준일자 {w.get('date','')} / 기준시각 {w.get('time','')} / 위치 {w.get('location','')} / 기온 {w.get('temp','')}"
        return _kakao_basic_card(
            title="해양대 날씨(실황)",
            description=_normalize_desc(desc),
            buttons=[
                {"action": "webLink", "label": "기상청", "webLinkUrl": "https://www.weather.go.kr"},
                {"action": "message", "label": "다시 조회", "messageText": msg},
            ],
        )

    # 축제/행사(2026 필터는 tools.py에서 수행)
    if ("축제" in msg) or ("행사" in msg):
        raw = await get_festival_info()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="부산 축제/행사",
                description=_normalize_desc(payload.get("msg") or "축제/행사 정보를 확인할 수 없습니다."),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
            )
        items = []
        for f in (payload.get("festivals") or [])[:5]:
            title = (f.get("title") or "행사").strip()
            place = (f.get("place") or "").strip()
            date_text = (f.get("date") or "").strip()
            items.append(
                {
                    "title": title[:50],
                    "description": _normalize_desc(f"{place} {date_text}"),
                    "link": {"web": _map_search_link(place or title)},
                }
            )
        return _kakao_list_card(
            header_title="부산 축제/행사(2026-01-20 이후)",
            items=items,
            buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
        )

    # Restaurants(멀티턴): 정적 리스트 제공 금지 → 음식 종류를 먼저 질문
    if ("맛집" in msg) or ("식당" in msg) or ("restaurants" in msg.lower()):
        _pending_set(user_id, "restaurants")
        return _kakao_basic_card(
            title="Restaurants",
            description="오늘은 어떤 종류의 음식을 찾으시나요? (예: 한식, 중식, 카페/커피, 분식 등)",
            buttons=[
                {"action": "message", "label": "한식", "messageText": "한식"},
                {"action": "message", "label": "중식", "messageText": "중식"},
            ],
        )

    # Pharmacy/Hospital(멀티턴): 증상/진료과를 먼저 질문 → 영업중 우선 노출
    if ("약국" in msg) or ("병원" in msg) or ("pharmacy" in msg.lower()) or ("hospital" in msg.lower()):
        _pending_set(user_id, "medical")
        return _kakao_basic_card(
            title="Pharmacy/Hospital",
            description="어디가 불편하시거나 어떤 진료과를 찾으시나요? (예: 약국, 감기/내과, 치과, 피부과 등)",
            buttons=[
                {"action": "message", "label": "약국", "messageText": "약국"},
                {"action": "message", "label": "치과", "messageText": "치과"},
            ],
        )

    # 버스(정류장ID 엄격 매핑은 tools.py에서 적용: 190 IN/OUT)
    if _is_bus_query(msg):
        direction = _infer_direction(msg)
        bus_num = _extract_digits(msg) or "190"
        if direction is None:
            return _kakao_basic_card(
                title="🚌 190 Bus",
                description="방향을 선택해 주세요. (IN: KMOU Main / OUT: Entrance/Nampo)",
                buttons=[
                    {"action": "message", "label": "IN", "messageText": "190 버스 IN"},
                    {"action": "message", "label": "OUT", "messageText": "190 버스 OUT"},
                ],
            )
        cache_key = f"bus:{direction}:{bus_num}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # 캐시가 없으면 백그라운드 프리페치 후, 즉시 브릿지 카드 반환
        if cache_key not in _KAKAO_INFLIGHT:
            _KAKAO_INFLIGHT.add(cache_key)

            async def _prefetch():
                try:
                    raw = await get_bus_arrival(bus_number=bus_num, direction=direction)
                    payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    if payload.get("status") != "success":
                        card = _kakao_basic_card(
                            title=f"🚌 {bus_num} Bus ({direction})",
                            description=_normalize_desc(payload.get("msg") or "버스 정보를 확인할 수 없습니다."),
                            buttons=[{"action": "message", "label": "다시 조회", "messageText": f"{bus_num} 버스 {direction}"}],
                        )
                        _cache_set(cache_key, card)
                        return

                    stops = payload.get("stops") or []
                    stop0 = stops[0] if stops else {}
                    stop_label = (stop0.get("label") or "정류장").strip()
                    items = []
                    for b in (stop0.get("buses") or [])[:5]:
                        bn = (b.get("bus_no") or "").strip()
                        desc = f"{(b.get('status') or '').strip()} / {(b.get('low_plate') or '').strip()}"
                        items.append({"title": bn[:50], "description": _normalize_desc(desc), "link": {"web": _map_search_link(stop_label)}})
                    card = _kakao_list_card(
                        header_title=f"{bus_num} Bus {direction} - {stop_label}",
                        items=items or [{"title": "도착 정보", "description": "현재 표시할 수 있는 도착 정보가 없습니다.", "link": {"web": _map_search_link(stop_label)}}],
                        buttons=[{"action": "message", "label": "다시 조회", "messageText": f"{bus_num} 버스 {direction}"}],
                    )
                    _cache_set(cache_key, card)
                finally:
                    _KAKAO_INFLIGHT.discard(cache_key)

            asyncio.create_task(_prefetch())

        return _kakao_basic_card(
            title=f"🚌 {bus_num} Bus ({direction})",
            description="Fetching live data... Please click the button again in 2 seconds.",
            buttons=[{"action": "message", "label": "다시 조회", "messageText": f"{bus_num} 버스 {direction}"}],
        )

    # 학교 지도 기능은 제거하고, KMOU 홈페이지로 피벗합니다.
    if ("홈페이지" in msg) or ("kmou" in msg.lower()) or ("학교 홈페이지" in msg) or ("KMOU 홈페이지" in msg):
        return _kakao_basic_card(
            title="한국해양대학교(KMOU) 홈페이지",
            description="공식 홈페이지에서 공지/학사일정/학과 정보를 확인할 수 있습니다.",
            buttons=[{"action": "webLink", "label": "KMOU 홈페이지 열기", "webLinkUrl": "https://www.kmou.ac.kr"}],
        )

    # 셔틀 시간
    if "셔틀 노선" in msg:
        raw = await get_shuttle_next_buses(limit=1)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return _kakao_response(
            [
                {
                    "basicCard": {
                        "title": "셔틀 기본 운행 노선",
                        "description": _normalize_desc(payload.get("route_base") or ""),
                        "buttons": [{"action": "message", "label": "셔틀 시간", "messageText": "셔틀 시간"}],
                    }
                },
                {
                    "basicCard": {
                        "title": "동삼시장 방면 노선(해당 시각만)",
                        "description": _normalize_desc(payload.get("route_market") or ""),
                        "buttons": [{"action": "message", "label": "셔틀 시간", "messageText": "셔틀 시간"}],
                    }
                },
                {
                    "basicCard": {
                        "title": "운행 안내",
                        "description": _normalize_desc(payload.get("notice") or "주말 및 법정 공휴일 운행 없음"),
                        "buttons": [{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
                    }
                },
            ]
        )

    if ("셔틀" in msg) or ("순환" in msg) or ("shuttle" in msg.lower()):
        raw = await get_shuttle_next_buses(limit=3)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        season = payload.get("season")
        season_label = "[❄️ Winter Vacation Schedule]" if season == "VACATION" else "[🌸 Semester Schedule]"

        if payload.get("status") == "no_service":
            return _kakao_response(
                [
                    {
                        "basicCard": {
                            "title": f"{season_label} 셔틀 운행",
                            "description": _normalize_desc(payload.get("msg") or "운행 정보를 확인할 수 없습니다."),
                            "buttons": [{"action": "message", "label": "다시 조회", "messageText": "셔틀 시간"}],
                        }
                    }
                ]
            )

        if payload.get("status") == "ended":
            return _kakao_response(
                [
                    {
                        "basicCard": {
                            "title": f"{season_label} 셔틀 운행",
                            "description": _normalize_desc(payload.get("msg") or "오늘 운행이 종료되었습니다."),
                            "buttons": [{"action": "message", "label": "내일 다시", "messageText": "셔틀 시간"}],
                        }
                    },
                    {
                        "basicCard": {
                            "title": "운행 안내",
                            "description": _normalize_desc(payload.get("notice") or "No service on weekends/holidays"),
                            "buttons": [{"action": "message", "label": "노선 안내", "messageText": "셔틀 노선 안내"}],
                        }
                    },
                ]
            )

        items = []
        for it in (payload.get("next") or [])[:3]:
            items.append(
                {
                    "title": f"{it.get('bus','')}",
                    "description": _normalize_desc(f"Departure {it.get('time','')}"),
                    "action": "message",
                    "messageText": "셔틀 노선 안내",
                }
            )

        outputs = [
            {
                "listCard": {
                    "header": {"title": f"{season_label} 다음 셔틀 3회"},
                    "items": items or [{"title": "셔틀", "description": "현재 표시할 출발 정보가 없습니다.", "action": "message", "messageText": "셔틀 시간"}],
                    "buttons": [
                        {"action": "message", "label": "노선 안내", "messageText": "셔틀 노선 안내"},
                        {"action": "message", "label": "다시 조회", "messageText": "셔틀 시간"},
                    ],
                }
            },
            {
                "basicCard": {
                    "title": "운행 안내",
                    "description": _normalize_desc(payload.get("notice") or "No service on weekends/holidays"),
                    "buttons": [{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
                }
            },
        ]
        return _kakao_response(outputs)

    return None

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    user_msg = data.get("message")
    user_id = data.get("user_id")  # 선택: 프론트에서 전달 가능
    
    async def event_generator():
        res = await ask_ara(user_msg, user_id=user_id, return_meta=True)
        yield f"data: {json.dumps(res, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/feedback")
async def feedback_endpoint(request: Request):
    """
    대화 ID(conversation_id)에 대해 사용자 피드백을 기록합니다.
    payload 예시:
    {
      "conversation_id": "...",
      "user_feedback": 1,   # 1 또는 -1
      "is_gold_standard": false
    }
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "msg": "요청 JSON을 파싱할 수 없습니다."}

    conversation_id = (data.get("conversation_id") or "").strip()
    user_feedback = data.get("user_feedback")
    is_gold_standard = data.get("is_gold_standard", None)

    if not conversation_id:
        return {"ok": False, "msg": "conversation_id가 필요합니다."}
    if user_feedback not in (1, -1, 0):
        return {"ok": False, "msg": "user_feedback은 1(좋아요), -1(싫어요), 0(중립)만 허용합니다."}
    if is_gold_standard is not None and not isinstance(is_gold_standard, bool):
        return {"ok": False, "msg": "is_gold_standard는 boolean이어야 합니다."}

    changed = update_conversation_feedback(conversation_id, int(user_feedback), is_gold_standard=is_gold_standard)
    if not changed:
        return {"ok": False, "msg": "해당 conversation_id를 찾을 수 없습니다."}
    return {"ok": True}

@app.post("/query")
async def kakao_endpoint(request: Request):
    try:
        try:
            data = await request.json()
        except Exception:
            return _kakao_simple_text("요청 형식을 확인할 수 없습니다.")
        
        user_request = data.get("userRequest", {}) or {}
        user_msg = user_request.get("utterance") or ""
        kakao_user_id = ((user_request.get("user") or {}) or {}).get("id")
        
        if not user_msg:
            return _kakao_basic_card(
                title="입력 필요",
                description="말씀을 이해하지 못했습니다. 다시 한 번 입력해 주세요.",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": "다시 시도"}],
            )

        # 브릿지 카드용 Astronomy 프리페치(요청 처리 중 병렬 실행)
        astro_task = asyncio.create_task(get_astronomy_data(time.strftime("%Y%m%d")))

        # 카카오에서 quickReplies로 돌아오는 피드백 발화 처리(선택 기능)
        # 예: "feedback:+1:<conversation_id>" 또는 "feedback:-1:<conversation_id>"
        m = re.match(r"^feedback:(?P<score>[+-]1):(?P<cid>[0-9a-fA-F-]{16,})$", user_msg.strip())
        if m:
            score = int(m.group("score"))
            cid = m.group("cid")
            ok = update_conversation_feedback(cid, score)
            return _kakao_basic_card(
                title="피드백",
                description=("피드백이 반영되었습니다. 감사합니다." if ok else "피드백 대상을 찾지 못했습니다."),
                buttons=[{"action": "message", "label": "다시 질문", "messageText": "다시 질문"}],
            )

        # 카카오 5초 제한 대비: 기본 3.8초 내 브릿지 반환
        kakao_timeout = float(os.environ.get("KAKAO_TIMEOUT_SECONDS", "3.8"))

        # 1차: 구조화 카드 라우팅(정확성/형식 우선)
        structured_timeout = max(0.1, kakao_timeout - 0.2)
        st, structured = await _run_with_timeout(_handle_structured_kakao(user_msg, kakao_user_id), timeout=structured_timeout)
        if st == "timeout":
            sunset_time = "Update Pending"
            try:
                raw = await asyncio.wait_for(astro_task, timeout=0.2)
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                sunset_time = (payload.get("sunset") or "Update Pending") if isinstance(payload, dict) else "Update Pending"
            except Exception:
                pass
            return _kakao_basic_card(
                title="처리 지연",
                description=f"Today's sunset at KMOU (Jodo) is {sunset_time}.\nFetching live data... Please click the button again in 2 seconds.",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": user_msg}],
            )
        if st == "error":
            return _kakao_basic_card(
                title="처리 오류",
                description="요청을 처리하는 중 오류가 발생했습니다.",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": user_msg}],
            )
        if structured is not None:
            return structured

        st2, res = await _run_with_timeout(ask_ara(user_msg, user_id=kakao_user_id, return_meta=True), timeout=kakao_timeout)
        if st2 == "timeout":
            sunset_time = "Update Pending"
            try:
                raw = await asyncio.wait_for(astro_task, timeout=0.2)
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                sunset_time = (payload.get("sunset") or "Update Pending") if isinstance(payload, dict) else "Update Pending"
            except Exception:
                pass
            return _kakao_basic_card(
                title="처리 지연",
                description=f"Today's sunset at KMOU (Jodo) is {sunset_time}.\nFetching live data... Please click the button again in 2 seconds.",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": user_msg}],
            )
        if st2 == "error":
            return _kakao_basic_card(
                title="처리 오류",
                description="요청을 처리하는 중 오류가 발생했습니다.",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": user_msg}],
            )

        response_text = (res.get("content", "") if isinstance(res, dict) else str(res)).strip()
        # 카드 UI 강제: LLM 응답도 basicCard/listCard로만 래핑
        return _kakao_basic_card(
            title="ARA 답변",
            description=_normalize_desc(response_text),
            buttons=[{"action": "message", "label": "다시 질문", "messageText": "다시 질문"}],
        )

    except Exception as e:
        print(f"Kakao Error: {e}")
        return _kakao_basic_card(
            title="시스템 오류",
            description="시스템 오류가 발생했습니다.",
            buttons=[{"action": "message", "label": "다시 시도", "messageText": "다시 시도"}],
        )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))