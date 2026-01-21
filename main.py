import os
from dotenv import load_dotenv

# .env 환경 변수 로드 (모든 커스텀 모듈 import 이전에 실행되어야 함)
load_dotenv()

import asyncio
import contextvars
import httpx
import tempfile
from datetime import datetime
from collections import deque
from zoneinfo import ZoneInfo
import xmltodict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import json
import re
import time
from rapidfuzz import fuzz

# 커스텀 모듈은 반드시 load_dotenv() 이후 import
from database import (
    init_db,
    update_conversation_feedback,
    get_pending_state,
    set_pending_state,
    clear_pending_state,
    get_history,
    save_history,
    log_interaction,
    save_food_contribution,
    save_restaurant_report,
    list_user_contribution_db,
    set_user_contribution_status,
)
from agent import ask_ara
from tools import get_shuttle_next_buses, get_shuttle_schedule
from tools import get_astronomy_data
from startup_check import run_startup_checks

app = FastAPI()
templates = Jinja2Templates(directory="templates")
init_db()

_KST = ZoneInfo("Asia/Seoul")

# Ocean-themed Visual Assets (ARA Identity)
IMG_KMOU_HOME = "https://images.unsplash.com/photo-1533596572767-021096785655?q=80&w=600&auto=format&fit=crop"  # 윤슬
IMG_DEFAULT_WAVE = "https://images.unsplash.com/photo-1505228395891-9a51e7e86bf6?q=80&w=600&auto=format&fit=crop"  # 파도

def _img_url_or(fallback: str, *candidates: str) -> str:
    """
    KakaoTalk 썸네일은 외부 접근 가능한 URL이 필요합니다.
    - 후보 중 http(s)로 시작하는 값이 있으면 그 값을 사용
    - 없으면 fallback 사용 (placeholder/빈값으로 인한 깨짐 방지)
    """
    for u in candidates:
        s = (u or "").strip()
        if s.startswith("http://") or s.startswith("https://"):
            return s
    return fallback

# User-provided anime-style illustrations (override via env or edit constants)
IMG_SHUTTLE_NEW = os.environ.get("IMG_SHUTTLE_NEW") or "URL_FOR_IMAGE_1"
IMG_BUS_190_NEW = os.environ.get("IMG_BUS_190_NEW") or "URL_FOR_IMAGE_2"

# Function-Specific Thumbnail Mapping (Visual Differentiation)
_THUMBNAIL_MAP = {
    "Bus_190": _img_url_or(
        "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?q=80&w=600&auto=format&fit=crop",
        IMG_BUS_190_NEW,
    ),
    "Shuttle": _img_url_or(
        "https://images.unsplash.com/photo-1570125909517-53cb21c89ff2?q=80&w=600&auto=format&fit=crop",
        IMG_SHUTTLE_NEW,
    ),
    "Cafeteria": "https://images.unsplash.com/photo-1547573854-74d2a71d0826?q=80&w=600&auto=format&fit=crop",
    "Food_Restaurant": "https://images.unsplash.com/photo-1504674900247-0877df9cc836?q=80&w=600&auto=format&fit=crop",
    "Career_Policy": "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?q=80&w=600&auto=format&fit=crop",
    "Weather": "https://images.unsplash.com/photo-1592210454359-9043f067919b?q=80&w=600&auto=format&fit=crop",
    "Default": IMG_DEFAULT_WAVE,
}

# KakaoTalk BasicCard 규격 준수(Error 2461 방지): thumbnail 기본값(요구사항 고정 URL)
_DEFAULT_BASICCARD_THUMBNAIL_URL = IMG_DEFAULT_WAVE

_KMOU_SPECIALIZED_DICTIONARY: dict[str, list[str]] = {
    "학식": ["학식", "식단", "밥", "오늘의학식", "점심", "저녁", "식표", "학석"],
    "날씨": ["날씨", "기온", "비", "영도날씨", "온도", "체감", "날시", "날씨는"],
    "맛집": ["맛집", "식당", "카페", "영도맛집", "밥집", "맛짐", "맛짖"],
    "제보": ["제보", "추천", "맛집제보", "등록", "제보하기", "재보", "추천하기"],
    "취업": ["취업", "채용", "일자리", "공고", "워크넷", "취업정보", "구인", "추업"],
}

# English keyword mapping to Korean intents (Fallback for English inputs)
_ENGLISH_INTENT_MAPPING: dict[str, str] = {
    "bus": "190 해양대구본관 출발",
    "shuttle": "셔틀 시간",
    "190": "190 해양대구본관 출발",
    "transport": "190 해양대구본관 출발",
    "food": "맛집",
    "cafeteria": "학식",
    "menu": "학식",
    "lunch": "학식",
    "job": "취업",
    "career": "취업",
    "work": "취업",
    "policy": "취업",
    "weather": "영도 날씨",
    "contact": "캠퍼스 연락처",
    "home": "KMOU 홈페이지",
    "homepage": "KMOU 홈페이지",
}

def _normalize_english_input(msg: str) -> str:
    """Convert English keywords to Korean intent messages"""
    msg_lower = msg.lower().strip()
    
    # Direct mapping
    if msg_lower in _ENGLISH_INTENT_MAPPING:
        return _ENGLISH_INTENT_MAPPING[msg_lower]
    
    # Partial matching for common English inputs
    for eng_key, ko_intent in _ENGLISH_INTENT_MAPPING.items():
        if eng_key in msg_lower:
            # Handle bus 190 specific patterns
            if eng_key in ["bus", "190", "transport"] and any(k in msg_lower for k in ["old main", "kmou main", "depart", "schedule"]):
                return "190 해양대구본관 출발"
            # Handle shuttle specific patterns
            if eng_key == "shuttle" or "campus bus" in msg_lower or "school bus" in msg_lower:
                return "셔틀 시간"
            return ko_intent
    
    return msg  # Return original if no match
_KMOU_DICT_FLAT: list[tuple[str, str]] = []

def _norm_for_fuzz(s: str) -> str:
    t = (s or "").strip().casefold()
    t = re.sub(r"\s+", "", t)
    return t

def _build_kmou_dict_flat() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, vals in (_KMOU_SPECIALIZED_DICTIONARY or {}).items():
        for v in (vals or []):
            nv = _norm_for_fuzz(v)
            if nv:
                out.append((k, nv))
    return out

def _kmou_dict_best_intent(user_msg: str) -> tuple[str | None, int]:
    global _KMOU_DICT_FLAT
    if not _KMOU_DICT_FLAT:
        _KMOU_DICT_FLAT = _build_kmou_dict_flat()
    u = _norm_for_fuzz(user_msg)
    if not u:
        return (None, 0)
    best_key: str | None = None
    best_score = 0
    for k, v in _KMOU_DICT_FLAT:
        sc = int(fuzz.ratio(u, v))
        if sc > best_score:
            best_score = sc
            best_key = k
    return (best_key, best_score)

_HANGUL_RE = re.compile(r"[ㄱ-ㅎ가-힣]")

def _t(key: str) -> str:
    """Korean-only translation table"""
    ko = {
        "bridge_title": "처리 지연",
        "bridge_desc": "실시간 데이터를 가져오는 중입니다. 2초 후 버튼을 다시 눌러주세요.",
        "retry": "다시 시도",
        "need_input_title": "입력 필요",
        "need_input_desc": "말씀을 이해하지 못했습니다. 다시 한 번 입력해 주세요.",
    }
    return ko.get(key, key)

def _nav_quick_replies() -> list[dict]:
    """Global Navigation - 8 buttons (English Mode removed)"""
    base = [
        {"label": "190번 출발 (구본관)", "action": "message", "messageText": "190 해양대구본관 출발"},
        {"label": "학식", "action": "message", "messageText": "학식"},
        {"label": "셔틀버스", "action": "message", "messageText": "셔틀 시간"},
        {"label": "날씨", "action": "message", "messageText": "영도 날씨"},
        {"label": "맛집 추천", "action": "message", "messageText": "맛집"},
        {"label": "취업/정책", "action": "message", "messageText": "취업"},
        {"label": "캠퍼스 연락처", "action": "message", "messageText": "캠퍼스 연락처"},
        {"label": "학교 홈피", "action": "message", "messageText": "KMOU 홈페이지"},
    ]
    return base

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
        print("[ARA Log] API Key Load Success")
        # Astronomy API sync(짧은 타임아웃, 무환각)
        try:
            today = time.strftime("%Y%m%d")
            raw = await asyncio.wait_for(get_astronomy_data(today), timeout=2.0)
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
            sunset = payload.get("sunset") if isinstance(payload, dict) else None
            if payload.get("status") == "success" and sunset:
                print(f"[ARA Log] Astronomy API Sync Success: {sunset}")
        except Exception:
            pass
    except UnicodeEncodeError:
        print("[ARA Log] API Key Load Success")
    except FileExistsError:
        # already logged by another worker
        pass

# dict flat precompute (latency guard)
try:
    _KMOU_DICT_FLAT = _build_kmou_dict_flat()
except Exception:
    _KMOU_DICT_FLAT = []

# NOTE: quickReplies는 `_build_quick_replies()`에서 요청 언어 기반으로 동적 생성합니다.
NAV_QUICK_REPLIES: list[dict] = []

# =========================
# Admin (맛집 제보 검수/승인) — 간단 API/페이지
# - 보호: ADMIN_TOKEN (Header: X-Admin-Token 또는 query: ?token=)
# =========================
_ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()

def _require_admin(request: Request) -> str:
    """
    관리자 토큰 검사. 성공 시 토큰 문자열 반환.
    """
    if not _ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN is not configured.")
    tok = (request.headers.get("X-Admin-Token") or request.query_params.get("token") or "").strip()
    if not tok or tok != _ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tok

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    _require_admin(request)
    # 템플릿 없이 단일 HTML로 제공(간단)
    html = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>KMOU Bot Admin - 맛집 제보 검수</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", sans-serif; margin: 18px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap: wrap; }
    button { padding: 6px 10px; cursor:pointer; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
    th { background:#f6f6f6; text-align:left; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
    .badge { padding:2px 6px; border-radius: 6px; background:#eee; }
    .pending { background:#fff3cd; }
    .approved { background:#d1e7dd; }
    .rejected { background:#f8d7da; }
    textarea { width: 260px; height: 60px; }
  </style>
</head>
<body>
  <h2>맛집 제보 검수</h2>
  <div class="row">
    <label>상태:
      <select id="status">
        <option value="pending">pending</option>
        <option value="approved">approved</option>
        <option value="rejected">rejected</option>
        <option value="">all</option>
      </select>
    </label>
    <button onclick="load()">불러오기</button>
    <span id="msg" class="mono"></span>
  </div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>가게명/주소</th>
        <th>메모</th>
        <th>원문</th>
        <th>상태</th>
        <th>검수</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>

  <script>
    const token = new URLSearchParams(location.search).get("token") || "";
    async function api(path, opts) {
      const res = await fetch(path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token), opts || {});
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    function esc(s){ return (s||"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }
    async function load() {
      const st = document.getElementById("status").value;
      document.getElementById("msg").textContent = "loading...";
      const data = await api("/admin/api/contributions?status=" + encodeURIComponent(st));
      const rows = data.items || [];
      const tb = document.getElementById("tbody");
      tb.innerHTML = "";
      for (const it of rows) {
        const badgeClass = it.status === "approved" ? "approved" : (it.status === "rejected" ? "rejected" : "pending");
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="mono">${it.id}</td>
          <td>
            <div><b>${esc(it.place_name||"(미상)")}</b></div>
            <div>${esc(it.address||"")}</div>
            <div class="mono">is_yeongdo=${it.is_yeongdo}</div>
          </td>
          <td>${esc(it.note||"")}</td>
          <td class="mono">${esc(it.raw_text||"")}</td>
          <td><span class="badge ${badgeClass}">${it.status}</span></td>
          <td>
            <div class="row">
              <textarea id="note-${it.id}" placeholder="검수 메모(선택)"></textarea>
            </div>
            <div class="row">
              <button onclick="setStatus(${it.id}, 'approved')">승인</button>
              <button onclick="setStatus(${it.id}, 'rejected')">반려</button>
              <button onclick="setStatus(${it.id}, 'pending')">보류</button>
            </div>
            <div class="mono">${esc(it.reviewed_at||"")}</div>
          </td>
        `;
        tb.appendChild(tr);
      }
      document.getElementById("msg").textContent = "ok (" + rows.length + ")";
    }
    async function setStatus(id, status) {
      const note = (document.getElementById("note-" + id)?.value || "").trim();
      await api("/admin/api/contributions/" + id + "/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, review_note: note, reviewed_by: "admin" })
      });
      await load();
    }
    load();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)

@app.get("/admin/api/contributions")
async def admin_list_contributions(request: Request, status: str = "pending", limit: int = 50, offset: int = 0):
    _require_admin(request)
    st = (status or "").strip().lower()
    if st == "":
        st = None
    items = list_user_contribution_db(status=st, limit=limit, offset=offset)
    return {"ok": True, "items": items}

@app.post("/admin/api/contributions/{contribution_id}/status")
async def admin_set_contribution_status(request: Request, contribution_id: int):
    _require_admin(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    status = (data.get("status") or "").strip().lower()
    reviewed_by = (data.get("reviewed_by") or "").strip() or None
    review_note = (data.get("review_note") or "").strip() or None
    ok = set_user_contribution_status(contribution_id=int(contribution_id), status=status, reviewed_by=reviewed_by, review_note=review_note)
    if not ok:
        return {"ok": False, "msg": "Invalid status or contribution_id not found."}
    return {"ok": True}

def _build_quick_replies():
    """
    카카오 quickReplies는 모든 응답 하단에 상시 노출합니다.
    - 고정 네비게이션 8개 포함(상시 메뉴)
    """
    return _nav_quick_replies()

def _kakao_response(outputs: list[dict], quick_replies: list[dict] | None = None):
    """
    카카오 스킬 응답 공통 래퍼
    - 반드시 {"version":"2.0","template":{"outputs":[...]}} 형식을 유지
    - 모든 응답에 quickReplies 상시 포함
    """
    # KakaoTalk 규격: 모든 basicCard에 thumbnail 필수
    def _ensure_basiccard_thumbnail(card: dict) -> None:
        if not isinstance(card, dict):
            return
        thumb = card.get("thumbnail")
        if not isinstance(thumb, dict) or not str(thumb.get("imageUrl") or "").strip():
            card["thumbnail"] = {"imageUrl": _DEFAULT_BASICCARD_THUMBNAIL_URL}

    safe_outputs = outputs or []
    for out in safe_outputs:
        if not isinstance(out, dict):
            continue
        bc = out.get("basicCard")
        if isinstance(bc, dict):
            _ensure_basiccard_thumbnail(bc)
        car = out.get("carousel")
        if isinstance(car, dict) and car.get("type") == "basicCard":
            items = car.get("items")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        _ensure_basiccard_thumbnail(it)
    return {
        "version": "2.0",
        "template": {
            "outputs": safe_outputs,
            "quickReplies": (quick_replies if isinstance(quick_replies, list) else _build_quick_replies()),
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

def _kakao_basic_card(
    title: str,
    description: str,
    buttons: list[dict] | None = None,
    thumbnail: dict | None = None,
    quick_replies: list[dict] | None = None,
    thumbnail_type: str = "Default",
):
    # Mandatory thumbnail to prevent Kakao Error 2461
    # Use function-specific thumbnail if provided, else use type-based mapping
    if thumbnail:
        thumb_dict = thumbnail
    else:
        thumb_url = _THUMBNAIL_MAP.get(thumbnail_type, _THUMBNAIL_MAP["Default"])
        thumb_dict = {"imageUrl": thumb_url}
    
    card: dict = {
        "title": title,
        "description": description,
        "thumbnail": thumb_dict
    }
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"basicCard": card}], quick_replies=quick_replies)

def _kakao_list_card(header_title: str, items: list[dict], buttons: list[dict] | None = None, quick_replies: list[dict] | None = None):
    card: dict = {"header": {"title": header_title}, "items": items}
    if buttons:
        card["buttons"] = buttons
    return _kakao_response([{"listCard": card}], quick_replies=quick_replies)

def _kakao_carousel_basic_cards(cards: list[dict], quick_replies: list[dict] | None = None):
    safe_cards = (cards or [])[:10]
    return _kakao_response(
        [
            {
                "carousel": {
                    "type": "basicCard",
                    "items": safe_cards,
                }
            }
        ],
        quick_replies=quick_replies,
    )

def _qr(items: list[tuple[str, str]]) -> list[dict]:
    """Quick replies without language toggle"""
    out = []
    for label, text in items:
        out.append({"label": label, "action": "message", "messageText": text})
    return out

def _qr_career() -> list[dict]:
    """Career quick replies"""
    items = [("🏫 홈", "KMOU 홈페이지"), ("💼 해운/물류", "해운 채용"), ("🧾 세무/회계", "세무 채용"), ("🧩 청년정책", "청년지원 정책")]
    return _qr(items)

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

def _normalize_desc_preserve_lines(s: str) -> str:
    """
    버스 등 '정확한 줄바꿈 포맷'을 유지해야 하는 description 전용.
    - 줄바꿈(\n)을 유지합니다.
    - 마크다운(**)은 그대로 둡니다.
    """
    if not s:
        return ""
    lines = [ln.strip() for ln in str(s).splitlines() if ln.strip()]
    return "\n".join(lines)[:450]

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

def _is_nav_intent(msg: str) -> bool:
    """
    버튼/네비게이션 입력으로 간주되는 메시지.
    - 이전 pending_state가 현재 응답에 간섭하지 않도록 선제적으로 컨텍스트를 초기화합니다.
    """
    t = (msg or "").strip().lower()
    if not t:
        return False
    nav_keywords = [
        "버스", "190", "bus",
        "날씨", "weather",
        "셔틀", "shuttle",
        "홈", "home", "homepage", "kmou",
        "연락처", "contact",
        "맛집", "식당", "food", "restaurant",
        "학식", "식단", "cafeteria",
        "맛집 제보", "제보하기",
    ]
    return any(k in t for k in nav_keywords)

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
    Supports both Korean and English keywords.
    """
    t = (text or "").lower()
    # Korean keywords
    if "버스" in t or "bus" in t:
        return True
    if re.search(r"\b(in|out)\b", t):
        return True
    # English bus keywords
    if any(k in t for k in ["shuttle", "transport", "depart", "schedule", "old main", "kmou main"]):
        return True
    # Bus number patterns (Korean)
    if re.search(r"\d{2,4}", t) and any(k in t for k in ["도착", "정류장", "언제", "몇", "분", "시간"]):
        return True
    # Bus number patterns (English)
    if re.search(r"\d{2,4}", t) and any(k in t for k in ["arrival", "stop", "when", "time", "min", "minute", "depart"]):
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
    # English hints
    if "campus" in tl:
        return "IN"
    if "nampo" in tl or "city" in tl or "downtown" in tl:
        return "OUT"
    return None

def _extract_digits(text: str) -> str:
    return "".join(re.findall(r"\d+", str(text or "")))

def _extract_worknet_keyword(user_msg: str) -> str:
    s = (user_msg or "").strip()
    if not s:
        return "해운 물류"
    tl = s.lower()
    # 대표 키워드가 직접 포함되면 그대로 활용(최소 보정)
    for k in ["해운", "항만", "물류", "포워딩", "선사", "운항", "해사", "shipping", "port", "logistics", "maritime", "forwarding"]:
        if k in tl:
            # 한국어/영어 혼합 가능: 원문에서 의미 있는 구간만 남기도록 후처리
            break

    # 일반적인 “요청어/플랫폼명” 제거 후 남는 부분을 검색어로 사용
    cleaned = s
    for w in [
        "워크넷", "worknet",
        "채용", "취업", "일자리", "구인", "구직", "career", "job", "jobs",
        "추천", "찾아줘", "알려줘", "보여줘", "검색", "search",
        "관련", "쪽", "쪽으로", "좀", "요즘", "지금",
        "해양대", "kmou", "한국해양대", "한국해양대학교",
    ]:
        cleaned = re.sub(re.escape(w), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 너무 비면 기본 검색어로
    if not cleaned or len(cleaned) < 2:
        return "해운 물류"

    # 길이 제한(워크넷 키워드 과다 방지)
    return cleaned[:50]

_CAREER_INTENT_MAP: dict[str, list[str]] = {
    "취업_해양공학": ["해운", "물류", "it", "공학", "항만", "선사", "조선", "항해", "기관", "해양", "해양공학", "해사", "운항", "기관사"],
    "취업_전문사무": ["법", "회계", "세무", "인사", "마케팅", "경영", "행정", "사회과학", "인문", "인문학", "사회", "문과", "정책", "공공", "교육", "언론", "콘텐츠"],
    "청년정책": ["정책", "지원금", "수당", "청년지원", "정부지원", "활동비"],
}
_CAREER_FLAT: list[tuple[str, str, str]] = []
_CAREER_RATE: dict[str, deque] = {}

def _build_career_flat() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for intent, kws in (_CAREER_INTENT_MAP or {}).items():
        for kw in (kws or []):
            nkw = _norm_for_fuzz(kw)
            if nkw:
                out.append((intent, nkw, kw))
    return out

def _career_rate_limited(user_id: str | None) -> bool:
    key = (user_id or "__anon__").strip() if isinstance(user_id, str) else "__anon__"
    dq = _CAREER_RATE.get(key)
    if dq is None:
        dq = deque()
        _CAREER_RATE[key] = dq
    now = time.time()
    while dq and (now - dq[0] > 10.0):
        dq.popleft()
    if len(dq) >= 5:
        return True
    dq.append(now)
    return False

def _career_best_intent(user_msg: str) -> tuple[str | None, int, str | None]:
    global _CAREER_FLAT
    if not _CAREER_FLAT:
        _CAREER_FLAT = _build_career_flat()
    s = (user_msg or "").strip()
    if not s:
        return (None, 0, None)
    tl = s.casefold()
    tokens = re.findall(r"[0-9a-z가-힣]+", tl)
    cands = tokens + [tl.replace(" ", "")]
    best_intent: str | None = None
    best_kw: str | None = None
    best_score = 0
    for intent, kw_norm, kw_raw in _CAREER_FLAT:
        sc = 0
        for c in cands:
            cn = _norm_for_fuzz(c)
            if not cn:
                continue
            sc = max(sc, int(fuzz.ratio(cn, kw_norm)))
            if sc >= 100:
                break
        if sc > best_score:
            best_score = sc
            best_intent = intent
            best_kw = kw_raw
    return (best_intent, best_score, best_kw)

async def _handle_structured_kakao(user_msg: str, user_id: str | None):
    """
    카카오용: 도구 결과를 구조화된 카드로 변환(정확성/형식 준수).
    """
    from tools import get_bus_arrival, search_restaurants

    msg = (user_msg or "").strip()
    orig_msg = msg
    
    # Normalize English inputs to Korean
    if not _HANGUL_RE.search(msg):
        msg = _normalize_english_input(msg)
        orig_msg = msg

    dict_intent, dict_score = _kmou_dict_best_intent(msg)
    if dict_intent and 65 <= dict_score <= 74:
        label_map = {
            "학식": "학식",
            "날씨": "영도 날씨",
            "맛집": "맛집",
            "제보": "맛집 제보하기",
            "취업": "취업",
        }
        guess = dict_intent
        target_text = label_map.get(guess, guess)
        return _kakao_basic_card(
            title="ARA 확인",
            description=_normalize_desc(f"혹시 {guess} 정보를 찾으시는 건가요?"),
            buttons=[
                {"action": "message", "label": f"{guess} 보기", "messageText": target_text},
                {"action": "message", "label": "취소", "messageText": "KMOU 홈페이지"},
            ],
        )

    if dict_intent and dict_score >= 75:
        if dict_intent == "학식":
            msg = "학식"
        elif dict_intent == "날씨":
            msg = "영도 날씨"
        elif dict_intent == "맛집":
            if any(k in orig_msg.lower() for k in ["카페", "커피", "cafe", "coffee"]):
                msg = "카페"
            else:
                msg = "맛집"
        elif dict_intent == "제보":
            msg = "맛집 제보하기"
        elif dict_intent == "취업":
            msg = orig_msg

    # Bus 190 - English inputs normalized earlier
    msg_lower = msg.lower()
    
    # Korean logic only (English inputs are normalized to Korean)
    is_bus_190_query = (
        (("190" in msg) and (("해양대구본관" in msg) or ("구본관" in msg)) and any(k in msg for k in ["출발", "시간표", "언제", "다음", "몇분", "몇 분"]))
        or (("190" in msg_lower or "bus" in msg_lower) and any(k in msg_lower for k in ["old main", "kmou main", "depart", "schedule"]))
    )
    
    if is_bus_190_query:
        from tools import get_bus_190_kmou_main_next_departures

        raw = await get_bus_190_kmou_main_next_departures()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(payload, dict):
            payload = {}

        timetable_url = "https://www.kmou.ac.kr/kmou/cm/cntnts/cntntsView.do?mi=2036&cntntsId=356"
        buttons = [{"action": "webLink", "label": "전체 시간표 확인", "webLinkUrl": timetable_url}]

        if payload.get("status") == "ENDED":
            return _kakao_basic_card(
                title="🚌 190번 버스 (구본관 출발)",
                description=_normalize_desc_preserve_lines("오늘 190번 운행은 종료되었어 (막차 21:49). 내일 첫차는 04:55야! 🌙"),
                buttons=buttons,
                thumbnail_type="Bus_190",
            )

        nxt = payload.get("next") or {}
        nxt2 = payload.get("next2") or {}
        t1 = (nxt.get("time") or "").strip()
        r1 = nxt.get("remaining_min")
        t2 = (nxt2.get("time") or "").strip() if isinstance(nxt2, dict) else ""

        first_line = f"🚀 이번 차: {t1}"
        if isinstance(r1, int):
            first_line += f" ({r1}분 전)"
        second_line = f"🚍 다음 차: {t2}" if t2 else ""
        desc = "\n".join([x for x in [first_line, second_line] if x]).strip()

        return _kakao_basic_card(
            title="🚌 190번 버스 (구본관 출발)",
            description=_normalize_desc_preserve_lines(desc),
            buttons=buttons,
            thumbnail_type="Bus_190",
        )

    # 인터랙션 로그(프로토타입): 자주 묻는 질문/의도 집계를 위해 저장(응답에는 절대 노출하지 않음)
    try:
        tl = msg.lower()
        intent = (
            "bus" if _is_bus_query(msg)
            else "weather" if ("날씨" in msg or "weather" in tl)
            else "cafeteria" if ("학식" in msg or "식단" in msg or "cafeteria" in tl)
            else "restaurants" if ("맛집" in msg or "식당" in msg or "food" in tl or "restaurant" in tl)
            else "other"
        )
        log_interaction(user_id=user_id, intent=intent, user_query=msg)
    except Exception:
        pass

    # 캠퍼스 연락처(오프라인): 카테고리 → 부서 → 전화하기
    if msg.lower() in {"contact", "contacts"} or msg in {"캠퍼스 연락처", "연락처", "학교 연락처", "교내 연락처"}:
        from tools import get_campus_contacts

        raw = get_campus_contacts(lang="ko")
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

    m_contact_cat = re.match(r"^(연락처|contact)\s+(?P<cat>[A-Za-z_]+)\s*$", msg, flags=re.IGNORECASE)
    if m_contact_cat:
        from tools import get_campus_contacts

        cat = m_contact_cat.group("cat")
        raw = get_campus_contacts(category=cat, lang="ko")
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
            header_title=f"📞 {payload.get('category_label') or cat}",
            items=items or [{"title": "연락처", "description": "표시할 부서가 없습니다.", "action": "message", "messageText": "캠퍼스 연락처"}],
            buttons=[{"action": "message", "label": "다른 분류", "messageText": "캠퍼스 연락처"}],
        )

    m_contact_office = re.match(r"^(전화|call)\s+(?P<office>[A-Za-z_]+)\s*$", msg, flags=re.IGNORECASE)
    if m_contact_office:
        from tools import get_campus_contacts

        office = m_contact_office.group("office")
        raw = get_campus_contacts(office=office, lang="ko")
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

    # 버튼 중복/이전 컨텍스트 간섭 방지: 네비게이션 입력이면 pending을 선제 초기화
    if _is_nav_intent(msg):
        _pending_clear(user_id)

    # 멀티턴 상태 처리: 버튼 → 질문 → 사용자의 상세 입력 → 검색
    pending = _pending_get(user_id)
    if pending == "restaurants":
        _pending_clear(user_id)
        raw = await search_restaurants(query=msg, limit=5)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="카페/커피",
                description=_normalize_desc(payload.get("msg") or "조건에 맞는 결과를 찾지 못했습니다."),
                buttons=[{"action": "message", "label": "다시 검색", "messageText": "카페"}],
            )
        items = []
        for r in (payload.get("restaurants") or [])[:5]:
            name = (r.get("name") or "").strip() or "가게"
            addr = (r.get("addr") or r.get("description") or "").strip()
            link = (r.get("link") or "").strip()
            items.append({"title": name[:50], "description": _normalize_desc(addr), "link": {"web": (link or _map_search_link(name))}})
        if not items:
            return _kakao_basic_card(
                title="카페/커피",
                description="정보를 확인 중입니다",
                buttons=[{"action": "message", "label": "다시 검색", "messageText": "카페"}],
            )
        return _kakao_list_card(
            header_title=f"부산광역시 영도구 카페: {payload.get('query','')}",
            items=items or [{"title": "검색 결과", "description": "표시할 결과가 없습니다.", "link": {"web": _map_search_link(msg)}}],
            buttons=[
                {"action": "message", "label": "맛집 랜덤", "messageText": "맛집"},
                {"action": "message", "label": "맛집 제보하기", "messageText": "맛집 제보하기"},
            ],
        )

    if pending == "restaurant_report":
        _pending_clear(user_id)
        try:
            save_food_contribution(user_id=user_id, text=msg)
            save_restaurant_report(user_id=user_id, reported_text=msg)
            return _kakao_basic_card(
                title="맛집 제보 완료",
                description="제보 고마워요. 제가 바로 DB에 저장해두고, 검토되면 반영될 수 있게 해둘게요.",
                buttons=[{"action": "message", "label": "맛집 보기", "messageText": "맛집"}],
            )
        except Exception:
            return _kakao_basic_card(
                title="맛집 제보",
                description="정보를 확인 중입니다",
                buttons=[{"action": "message", "label": "다시 시도", "messageText": "맛집 제보하기"}],
            )

    # Cafeteria menu: 크롤링 폐기 → KMOU Coop 사이트로 바로 연결
    if ("학식" in msg) or ("식단" in msg) or ("cafeteria" in msg.lower()):
        return _kakao_basic_card(
            title="오늘의 학식",
            description="한국해양대학교 소비자생활협동조합 사이트로 바로 연결합니다.",
            buttons=[
                {"action": "webLink", "label": "학식 보러가기", "webLinkUrl": "https://www.kmou.ac.kr/coop/dv/dietView/selectDietDateView.do?mi=1189"},
            ],
            thumbnail_type="Cafeteria",
        )

    # Weather
    if ("날씨" in msg) or ("weather" in msg.lower()):
        from tools import get_weather_info
        raw = await get_weather_info(lang="ko")
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return _kakao_basic_card(
                title="날씨",
                description=_normalize_desc((payload.get("msg") if isinstance(payload, dict) else None) or "정보를 확인 중입니다"),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
            )
        
        # Weather Diversity: Include condition description
        condition = payload.get("condition", "")
        temp = payload.get("temp", 0)
        feels_like = payload.get("feels_like", 0)
        wind_speed = payload.get("wind_speed", 0)
        wind_text = payload.get("wind_text", "")
        
        desc_parts = []
        if condition:
            desc_parts.append(condition)
        desc_parts.append(f"온도 {temp:.1f}°C (체감 {feels_like:.1f}°C)")
        desc_parts.append(f"바람 {wind_speed:.1f}m/s ({wind_text})")
        desc = " / ".join(desc_parts)
        
        return _kakao_basic_card(
            title="해양대 날씨(실황)",
            description=_normalize_desc_preserve_lines(desc),
            buttons=[
                {"action": "webLink", "label": "기상청", "webLinkUrl": "https://www.weather.go.kr"},
                {"action": "message", "label": "다시 조회", "messageText": msg},
            ],
            thumbnail_type="Weather",
        )

    # Career/Jobs - English inputs normalized earlier
    msg_lower = msg.lower()
    is_career_query = any(k in msg for k in ["취업", "취업/정책", "채용", "일자리", "공고", "워크넷", "청년", "지원금", "수당", "정책"]) or any(k in msg_lower for k in ["worknet", "job", "jobs", "career", "policy", "employment"])
    
    if is_career_query:
        if _career_rate_limited(user_id):
            return _kakao_basic_card(
                title="커리어 가속",
                description=_normalize_desc("워워, 천천히 물어봐도 다 답해줄 수 있어! 조금만 숨 돌리고 오자."),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
                thumbnail_type="Career_Policy",
            )

        # Map English keywords to Korean for API search (English inputs normalized earlier)
        search_keyword = None
        msg_lower = msg.lower()
        
        # Check if English keywords present
        if any(k in msg_lower for k in ["job", "jobs", "career", "work", "employment"]):
            search_keyword = "취업"
        elif any(k in msg_lower for k in ["policy", "youth", "support"]):
            search_keyword = "청년"
        else:
            # Korean keywords - use existing logic
            intent, score, kw = _career_best_intent(msg)
            if intent and 65 <= score <= 74:
                return _kakao_basic_card(
                    title="커리어 가속",
                    description=_normalize_desc(f"혹시 {intent.replace('_', ' ')} 쪽을 찾으시는 건가요?"),
                    buttons=[
                        {"action": "message", "label": "해양/공학", "messageText": "해운 채용"},
                        {"action": "message", "label": "사무/세무", "messageText": "세무 채용"},
                        {"action": "message", "label": "청년정책", "messageText": "청년지원 정책"},
                    ],
                    thumbnail_type="Career_Policy",
                )
            keyword = (kw or "").strip() or _extract_worknet_keyword(msg)
            if any(k in msg for k in ["세무", "회계", "법", "변호", "노무", "행정", "인사", "총무", "마케팅", "경영"]):
                keyword = " ".join([x for x in ["세무" if "세무" in msg else "", "회계" if "회계" in msg else "", "법" if "법" in msg else ""] if x]).strip() or keyword
            search_keyword = keyword or "취업"  # Default fallback

        from tools import get_youth_jobs

        # Determine category code from keyword
        category_code = None
        if any(k in msg for k in ["주거", "주택", "집"]):
            category_code = "023020"  # Housing
        elif any(k in msg for k in ["금융", "대출", "생활"]):
            category_code = "023030"  # Finance_Life
        elif any(k in msg for k in ["교육", "학습"]):
            category_code = "023040"  # Education
        elif any(k in msg for k in ["참여", "권리"]):
            category_code = "023050"  # Participation_Rights
        # Default: 023010 (Employment) - will be used if category_code is None

        # Always search with Korean keyword (API requires Korean) - use get_youth_jobs with category code
        raw = await get_youth_jobs(keyword=search_keyword, category_code=category_code)
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("status") == "error":
            return _kakao_basic_card(
                title="커리어 가속",
                description=_normalize_desc(payload.get("msg") or "현재 정보를 불러올 수 없습니다."),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
                thumbnail_type="Career_Policy",
            )
        if payload.get("status") == "empty":
            return _kakao_basic_card(
                title="커리어 가속",
                description=_normalize_desc(payload.get("msg") or "현재 조건에 맞는 프로그램이 없습니다."),
                buttons=[{"action": "message", "label": "다른 키워드", "messageText": "해운 채용"}],
                thumbnail_type="Career_Policy",
            )

        policies = (payload.get("policies") or [])[:10]
        cards = []
        default_thumbnail = "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?q=80&w=600&auto=format&fit=crop"
        
        # Check if this is a static fallback (timeout/error case)
        fallback_msg = payload.get("msg")
        is_static_fallback = payload.get("source") == "static_fallback"
        
        def _short40(s: str) -> str:
            t = (s or "").strip()
            t = re.sub(r"\s+", " ", t)
            if len(t) <= 40:
                return t
            return t[:40].rstrip() + "…"

        for j in policies:
            if not isinstance(j, dict):
                continue
            # Keep Korean title for accuracy (prevent hallucination)
            title = (j.get("policyName") or j.get("name") or j.get("title") or "청년정책").strip()
            itcn = (j.get("polyItcnCn") or j.get("intro") or "").strip()
            prd = (j.get("bizPrdCn") or j.get("period") or "").strip()
            link = (j.get("detail_url") or j.get("url") or "").strip() or "https://www.youthcenter.go.kr"
            
            # Get category name and support summary from policy data
            category_name = j.get("category_name", "청년정책")
            support_summary = j.get("support_summary") or itcn or "정보를 확인 중입니다"
            
            # Get thumbnail URL - ensure it's never empty (use Career_Policy thumbnail)
            DEFAULT_THUMB = _THUMBNAIL_MAP.get("Career_Policy", _THUMBNAIL_MAP["Default"])
            thumb_from_policy = j.get("thumbnail")
            if isinstance(thumb_from_policy, dict):
                thumbnail_url = thumb_from_policy.get("imageUrl") or DEFAULT_THUMB
            elif isinstance(thumb_from_policy, str):
                thumbnail_url = thumb_from_policy or DEFAULT_THUMB
            else:
                thumbnail_url = DEFAULT_THUMB
            
            # Korean description format with category and support info
            desc_parts = []
            if category_name:
                desc_parts.append(f"📍 분야: {category_name}")
            if support_summary:
                desc_parts.append(f"💰 지원내용: {_short40(support_summary)}")
            if prd:
                desc_parts.append(prd)
            
            desc = "\n".join(desc_parts) if desc_parts else "정보를 확인 중입니다"
            
            if not cards:
                if is_static_fallback and fallback_msg:
                    desc = f"{fallback_msg}\n\n" + desc
                else:
                    desc = "지금 딱 맞는 정보를 찾았어! 네 꿈에 한 발짝 더 가까워지길 바랄게.\n\n" + desc
            
            cards.append(
                {
                    "title": title[:50],
                    "description": _normalize_desc_preserve_lines(desc),
                    "thumbnail": {"imageUrl": thumbnail_url},
                    "buttons": [{"action": "webLink", "label": "자세히", "webLinkUrl": link}],
                }
            )
        if not cards:
            return _kakao_basic_card(
                title="커리어 가속",
                description=_normalize_desc("정보를 확인 중입니다"),
                buttons=[{"action": "message", "label": "다시 조회", "messageText": msg}],
                thumbnail_type="Career_Policy",
            )
        return _kakao_carousel_basic_cards(cards)

    if ("맛집" in msg) or ("식당" in msg) or ("restaurants" in msg.lower()) or ("food" in msg.lower()) or ("restaurant" in msg.lower()):
        from tools import get_random_yeongdo_restaurant

        raw = await get_random_yeongdo_restaurant()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return _kakao_basic_card(
                title="맛집",
                description="부산 영도구 맛집을 지금은 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
                buttons=[
                    {"action": "message", "label": "다시 뽑기", "messageText": "맛집"},
                    {"action": "message", "label": "카페/커피", "messageText": "카페"},
                    {"action": "message", "label": "맛집 제보하기", "messageText": "맛집 제보하기"},
                ],
                thumbnail_type="Food_Restaurant",
            )
        r = payload.get("restaurant") or {}
        name = (r.get("name") or "맛집").strip()
        addr = (r.get("addr") or "").strip()
        tel = (r.get("tel") or "").strip()
        link = (r.get("link") or _map_search_link(addr or name)).strip()
        desc = " / ".join([x for x in [addr, tel] if x]).strip() or "정보를 확인 중입니다"
        return _kakao_basic_card(
            title=(name[:50]),
            description=_normalize_desc(desc),
            buttons=[
                {"action": "webLink", "label": "카카오맵 열기", "webLinkUrl": link},
                {"action": "message", "label": "다른 맛집 랜덤", "messageText": "맛집"},
                {"action": "message", "label": "카페/커피", "messageText": "카페"},
            ],
            thumbnail_type="Food_Restaurant",
        )

    # 맛집 제보 플로우(권유형 UX)
    if msg == "맛집 제보하기":
        _pending_set(user_id, "restaurant_report")
        return _kakao_basic_card(
            title="맛집 제보하기",
            description="아래 형식으로 한 번에 보내주세요:\n가게명 / 주소(영도구) / 한 줄 추천",
            buttons=[
                {"action": "message", "label": "취소", "messageText": "맛집"},
            ],
        )

    if (msg == "카페") or (msg.lower().strip() in {"coffee", "cafe"}) or ("카페" in msg) or ("커피" in msg):
        _pending_set(user_id, "restaurants")
        return _kakao_basic_card(
            title="카페/커피",
            description="원하는 카페 키워드를 입력해 주세요. (예: 동삼동 카페, 디저트, 커피)",
            buttons=[
                {"action": "message", "label": "취소", "messageText": "맛집"},
            ],
        )

    if _is_bus_query(msg):
        from tools import get_bus_190_kmou_main_next_departures

        raw = await get_bus_190_kmou_main_next_departures()
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(payload, dict):
            payload = {}

        timetable_url = "https://www.kmou.ac.kr/kmou/cm/cntnts/cntntsView.do?mi=2036&cntntsId=356"
        buttons = [{"action": "webLink", "label": "전체 시간표 확인", "webLinkUrl": timetable_url}]

        if payload.get("status") == "ENDED":
            return _kakao_basic_card(
                title="🚌 190번 버스 (구본관 출발)",
                description=_normalize_desc_preserve_lines("오늘 190번 운행은 종료되었어 (막차 21:49). 내일 첫차는 04:55야! 🌙"),
                buttons=buttons,
                thumbnail_type="Bus_190",
            )

        nxt = payload.get("next") or {}
        nxt2 = payload.get("next2") or {}
        t1 = (nxt.get("time") or "").strip()
        r1 = nxt.get("remaining_min")
        t2 = (nxt2.get("time") or "").strip() if isinstance(nxt2, dict) else ""

        first_line = f"🚀 이번 차: {t1}"
        if isinstance(r1, int):
            first_line += f" ({r1}분 전)"
        second_line = f"🚍 다음 차: {t2}" if t2 else ""
        desc = "\n".join([x for x in [first_line, second_line] if x]).strip()

        return _kakao_basic_card(
            title="🚌 190번 버스 (구본관 출발)",
            description=_normalize_desc_preserve_lines(desc),
            buttons=buttons,
            thumbnail_type="Bus_190",
        )

    # Home
    if ("홈페이지" in msg) or ("kmou" in msg.lower()) or ("학교 홈페이지" in msg) or ("KMOU 홈페이지" in msg) or (msg.lower().strip() in {"home", "homepage"}):
        return _kakao_basic_card(
            title="한국해양대학교(KMOU) 홈페이지",
            description="필요한 걸 바로 찾을 수 있게 메뉴를 싹 정리해봤어. 확인해봐!\n\n공식 홈페이지에서 공지/학사일정/학과 정보를 확인할 수 있습니다.",
            buttons=[{"action": "webLink", "label": "KMOU 홈페이지 열기", "webLinkUrl": "https://www.kmou.ac.kr"}],
            thumbnail={"imageUrl": IMG_KMOU_HOME},
        )

    # 셔틀 시간
    if "셔틀 노선" in msg:
        raw = await get_shuttle_next_buses(limit=1, lang="ko")
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        shuttle_thumb = {"imageUrl": _THUMBNAIL_MAP.get("Shuttle", IMG_DEFAULT_WAVE)}
        return _kakao_response(
            [
                {
                    "basicCard": {
                        "title": "셔틀 기본 운행 노선",
                        "description": _normalize_desc(payload.get("route_base") or ""),
                        "thumbnail": shuttle_thumb,
                        "buttons": [{"action": "message", "label": "셔틀 시간", "messageText": "셔틀 시간"}],
                    }
                },
                {
                    "basicCard": {
                        "title": "동삼시장 방면 노선(해당 시각만)",
                        "description": _normalize_desc(payload.get("route_market") or ""),
                        "thumbnail": shuttle_thumb,
                        "buttons": [{"action": "message", "label": "셔틀 시간", "messageText": "셔틀 시간"}],
                    }
                },
                {
                    "basicCard": {
                        "title": "운행 안내",
                        "description": _normalize_desc(payload.get("notice") or "주말 및 법정 공휴일 운행 없음"),
                        "thumbnail": shuttle_thumb,
                        "buttons": [{"action": "message", "label": "KMOU 홈페이지", "messageText": "KMOU 홈페이지"}],
                    }
                },
            ]
        )

    # Shuttle Bus - English inputs normalized earlier
    msg_lower = msg.lower()
    is_shuttle_query = ("셔틀" in msg) or ("순환" in msg) or any(k in msg_lower for k in ["shuttle", "campus bus", "school bus", "circulation"])
    
    # Exclude Bus 190 keywords
    if any(k in msg_lower for k in ["190", "bus 190", "public", "old main", "kmou main"]) and "셔틀" not in msg and "순환" not in msg:
        is_shuttle_query = False
    
    if is_shuttle_query:
        # 요구사항: 다음 셔틀 1회만 안내(테이블 덤프 금지)
        raw = await get_shuttle_schedule(lang="ko")
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if payload.get("status") != "success":
            return _kakao_basic_card(
                title="셔틀버스",
                description=_normalize_desc(payload.get("msg") or "셔틀 운행 정보를 확인할 수 없습니다."),
                buttons=[{"action": "message", "label": "노선 안내", "messageText": "셔틀 노선 안내"}],
                thumbnail_type="Shuttle",
            )
        return _kakao_basic_card(
            title="셔틀버스",
            description=_normalize_desc(payload.get("msg") or ""),
            buttons=[
                {"action": "message", "label": "노선 안내", "messageText": "셔틀 노선 안내"},
                {"action": "message", "label": "다시 조회", "messageText": "셔틀 시간"},
            ],
            thumbnail_type="Shuttle",
        )

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
        # 요청 시각 컨텍스트(KST)
        now_kst = datetime.now(_KST)
        current_context = {
            "now_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
            "current_time_str": now_kst.strftime("%H:%M"),
            "current_day": "Weekend" if now_kst.weekday() >= 5 else "Weekday",
            "weekday": now_kst.weekday(),
            "tz": "Asia/Seoul",
        }
        # 요구사항: lang 관련 로직 제거(항상 한국어)
        res = await ask_ara(user_msg, user_id=user_id, return_meta=True, session_lang="ko", current_context=current_context)
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

        # 요청 시각 컨텍스트(KST) — LLM에 주입
        now_kst = datetime.now(_KST)
        current_context = {
            "now_kst": now_kst.strftime("%Y-%m-%d %H:%M"),
            "weekday": now_kst.weekday(),
            "current_day": "Weekend" if now_kst.weekday() >= 5 else "Weekday",
            "tz": "Asia/Seoul",
            "current_time_str": now_kst.strftime("%H:%M"),
        }

        # -------- 요구사항: lang 관련 로직 제거(항상 한국어) --------
        msg_norm = (user_msg or "").strip()

        # English input normalization (convert to Korean intent)
        if not _HANGUL_RE.search(msg_norm):
            # No Korean characters - treat as English input
            msg_norm = _normalize_english_input(msg_norm)
            user_msg = msg_norm
        
        # Always use Korean language
        session_lang = "ko"
        
        if not user_msg:
            return _kakao_basic_card(
                title=_t("need_input_title"),
                description=_t("need_input_desc"),
                buttons=[{"action": "message", "label": _t("retry"), "messageText": _t("retry")}],
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
            sunset_time = "정보 확인 불가"
            try:
                raw = await asyncio.wait_for(astro_task, timeout=0.2)
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                sunset_time = (payload.get("sunset") or "정보 확인 불가") if isinstance(payload, dict) else "정보 확인 불가"
            except Exception:
                pass
            return _kakao_basic_card(
                title=_t("bridge_title"),
                description=f"실시간 데이터를 가져오는 중입니다. 잠시 후 다시 시도해주세요.\n{_t('bridge_desc')}",
                buttons=[{"action": "message", "label": _t("retry"), "messageText": user_msg}],
            )
        if st == "error":
            return _kakao_basic_card(
                title="처리 오류",
                description="요청을 처리하는 중 오류가 발생했습니다.",
                buttons=[{"action": "message", "label": _t("retry"), "messageText": user_msg}],
            )
        if structured is not None:
            return structured

        st2, res = await _run_with_timeout(
            ask_ara(user_msg, user_id=kakao_user_id, return_meta=True, session_lang=session_lang, current_context=current_context),
            timeout=kakao_timeout,
        )
        if st2 == "timeout":
            sunset_time = "정보 확인 불가"
            try:
                raw = await asyncio.wait_for(astro_task, timeout=0.2)
                payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
                sunset_time = (payload.get("sunset") or "정보 확인 불가") if isinstance(payload, dict) else "정보 확인 불가"
            except Exception:
                pass
            return _kakao_basic_card(
                title=_t("bridge_title"),
                description=f"실시간 데이터를 가져오는 중입니다. 잠시 후 다시 시도해주세요.\n{_t('bridge_desc')}",
                buttons=[{"action": "message", "label": _t("retry"), "messageText": user_msg}],
            )
        if st2 == "error":
            return _kakao_basic_card(
                title="처리 오류",
                description="요청을 처리하는 중 오류가 발생했습니다.",
                buttons=[{"action": "message", "label": _t("retry"), "messageText": user_msg}],
            )

        response_text = (res.get("content", "") if isinstance(res, dict) else str(res)).strip()
        # 카드 UI 강제: LLM 응답도 basicCard/listCard로만 래핑
        return _kakao_basic_card(
            title="ARA 답변",
            description=_normalize_desc(response_text),
            buttons=[{"action": "message", "label": "다시 질문", "messageText": "다시 질문"}],
        )

    except Exception as e:
        print(f"[ARA Log] Kakao Error: {e}")
        return _kakao_basic_card(
            title="시스템 오류",
            description="시스템 오류가 발생했습니다.",
            buttons=[{"action": "message", "label": _t("retry"), "messageText": _t("retry")}],
        )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))