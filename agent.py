import os
import json
import asyncio
import re
import uuid
import inspect
import time
import hashlib
from typing import Any, Optional, Dict
from openai import AsyncOpenAI
from tools import (
    TOOLS_SPEC,
    get_bus_arrival,
    get_bus_190_tracker_busbusinfo,
    get_cheap_eats,
    get_kmou_weather,
    get_weather_info,
    get_shuttle_next_buses,
    search_restaurants,
    get_youth_center_info,
    get_calendar_day_2026,
    get_astronomy_data,
    get_campus_contacts,
    get_academic_schedule,
)
from database import init_db, save_conversation_pair, get_success_examples, get_history, save_history
from rag import get_university_context

_OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

# =========================
# Response Cache (TTL: 3600 seconds)
# =========================
RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL = 3600  # 1 hour
_CACHEABLE_QUERIES = ["개강날짜", "시험기간", "셔틀시간", "학사일정", "개강", "중간고사", "기말고사", "방학"]

def _get_cache_key(user_input: str, user_id: Optional[str] = None) -> str:
    """Generate cache key from user input and user_id"""
    key_str = f"{user_input}|{user_id or ''}"
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()

def _is_cacheable_query(user_input: str) -> bool:
    """Check if query is cacheable (fixed data like schedule)"""
    user_lower = user_input.lower()
    return any(keyword in user_lower for keyword in _CACHEABLE_QUERIES)

def _get_cached_response(cache_key: str) -> Optional[str]:
    """Get cached response if valid"""
    if cache_key not in RESPONSE_CACHE:
        return None
    
    cached = RESPONSE_CACHE[cache_key]
    if time.time() - cached["timestamp"] > _CACHE_TTL:
        del RESPONSE_CACHE[cache_key]
        return None
    
    return cached["response"]

def _set_cached_response(cache_key: str, response: str) -> None:
    """Store response in cache"""
    RESPONSE_CACHE[cache_key] = {
        "response": response,
        "timestamp": time.time()
    }

TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_bus_190_tracker_busbusinfo": get_bus_190_tracker_busbusinfo,
    "get_cheap_eats": get_cheap_eats,
    "get_kmou_weather": get_kmou_weather,
    "get_weather_info": get_weather_info,
    "get_shuttle_next_buses": get_shuttle_next_buses,
    "search_restaurants": search_restaurants,
    "get_youth_center_info": get_youth_center_info,
    "get_calendar_day_2026": get_calendar_day_2026,
    "get_astronomy_data": get_astronomy_data,
    "get_campus_contacts": get_campus_contacts,
    "get_academic_schedule": get_academic_schedule,
}

_BANNED_ADDRESSING_PATTERNS = [
    r"선장님",
    r"\bCaptain\b",
]

_LANG_TAG_RE = re.compile(r"^\[LANG:(EN|KO)\]\s*$", flags=re.IGNORECASE)

def _strip_legacy_lang_tags(history: list) -> list:
    """
    과거 버전에서 저장된 [LANG:..] system 메시지를 제거합니다.
    """
    out = []
    for it in (history or []):
        if isinstance(it, dict) and it.get("role") == "system":
            content = (it.get("content") or "").strip()
            if _LANG_TAG_RE.match(content):
                continue
        out.append(it)
    return out

def _save_history_trim(user_id: str, history: list, limit: int = 25) -> None:
    base = _strip_legacy_lang_tags(history or [])
    trimmed = base[-max(0, int(limit)) :]
    save_history(user_id, trimmed)

def _sanitize_response_text(text: str) -> str:
    """최후 안전장치: 금지 호칭/표현을 제거하거나 완화합니다."""
    if not text:
        return text
    for pat in _BANNED_ADDRESSING_PATTERNS:
        text = re.sub(pat, "사용자님", text, flags=re.IGNORECASE)
    return text

def _sanitize_response_text_with_context(text: str, user_input: str | None = None) -> str:
    """
    응답 정제(금지 호칭 제거 + 실패 시 대안 제시).
    """
    text = _sanitize_response_text(text)
    if not user_input:
        return text

    if _is_bus_query(user_input) and re.search(r"(확인할 수 없습니다|알 수 없습니다)", text):
        bus_num = _extract_digits(user_input) or "190"
        text = re.sub(
            r"(확인할 수 없습니다|알 수 없습니다)",
            f"{bus_num}번 버스 정보를 찾으시는 건가요? 현재 도착 정보가 없거나 입력이 불완전할 수 있습니다. "
            f"버스 번호({bus_num})와 방향(OUT/IN)을 함께 입력해 주시면 정확히 확인해 드리겠습니다.",
            text,
        )
    return text

def _extract_digits(text: str) -> str:
    if not text:
        return ""
    return "".join(re.findall(r"\d+", str(text)))

def _is_bus_query(text: str) -> bool:
    t = (text or "").lower()
    if "버스" in t:
        return True
    return bool(re.search(r"\d{2,4}", t)) and any(k in t for k in ["도착", "정류장", "위치", "언제", "몇분", "분"])

def _infer_direction(text: str) -> str | None:
    t = (text or "")
    tl = t.lower()
    if re.search(r"\bOUT\b", t, flags=re.IGNORECASE) or "진출" in t:
        return "OUT"
    if re.search(r"\bIN\b", t, flags=re.IGNORECASE) or "진입" in t:
        return "IN"

    has_in = ("학교" in t) or ("등교" in t) or ("학교 가자" in t) or ("in" in tl)
    has_out = ("부산역" in t) or ("하교" in t) or ("부산역 가자" in t) or ("out" in tl)
    if has_in and not has_out:
        return "IN"
    if has_out and not has_in:
        return "OUT"
    return None

def _norm_utterance(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("?", "").replace("!", "").replace(".", "").replace(",", "")
    return t

def _format_weather_response(payload: dict, lang: str) -> str:
    status = payload.get("status")
    if status != "success":
        return payload.get("msg") or "날씨 정보를 확인할 수 없습니다."

    w = payload.get("weather") or {}
    _ = lang
    lines = ["요청하신 해양대(영도구 동삼동) 날씨 정보입니다."]
    if w.get("temp"):
        lines.append(f"- 기온: {w.get('temp')}")
    if w.get("time"):
        lines.append(f"- 기준 시각: {w.get('time')}")
    if w.get("location"):
        lines.append(f"- 위치: {w.get('location')}")
    return "\n".join(lines).strip()

def _format_list_response(title: str, items: list, fields: list[tuple[str, str]], lang: str) -> str:
    if not items:
        _ = lang
        return f"{title} 정보를 확인할 수 없습니다."
    lines = [title]
    for it in items[:5]:
        parts = []
        for key, label in fields:
            v = (it or {}).get(key)
            if v:
                parts.append(f"{label} {v}")
        if parts:
            lines.append("- " + " / ".join(parts))
    return "\n".join(lines).strip()

def _format_bus_response(payload: dict, bus_number: str | None, direction: str, used_fallback: bool = False) -> str:
    dir_label = "IN(진입)" if direction == "IN" else "OUT(진출)"
    bn = bus_number or ""

    status = payload.get("status")
    msg = payload.get("msg") or ""

    if status == "need_direction":
        return (
            "버스 동선을 확인해야 정확히 안내드릴 수 있습니다.\n"
            "OUT(진출): 구본관 → 방파제입구 → 승선생활관\n"
            "IN(진입): 승선생활관 → 대학본부 → 구본관\n"
            "예) '190 OUT 버스', '101 IN 버스'\n"
            "참고: 발화에 '학교/등교'가 포함되면 IN, '부산역/하교'가 포함되면 OUT으로 자동 추론합니다."
        )

    if status in {"error"}:
        return f"버스 정보를 조회하는 과정에서 오류가 발생했습니다.\n사유: {msg or '알 수 없음'}"

    if status in {"empty"}:
        base = f"{bn + '번 ' if bn else ''}버스 {dir_label} 기준으로는 현재 도착 정보를 확인하지 못했습니다.\n"
        if used_fallback:
            base += "대신 동일 동선 정류장의 최신 도착 목록 일부를 안내드립니다.\n"
        base += "원하시면 버스 번호/방향을 다시 한 번 확인해 주시기 바랍니다."
        return base

    if status == "fallback":
        lines = []
        lines.append(f"{bn + '번 ' if bn else ''}버스 {dir_label} 기준으로는 해당 번호의 도착 정보를 찾지 못했습니다.")
        lines.append("혹시 버스 번호가 맞는지 확인해 주실 수 있을까요?")
        sugg = payload.get("suggestions") or []
        if sugg:
            lines.append("참고로, 동일 정류장에서 확인된 가장 근접한 도착 정보는 다음과 같습니다.")
            for s in sugg[:3]:
                label = s.get("label", "정류장")
                buses = s.get("buses") or []
                if not buses:
                    continue
                lines.append(f"- {label}")
                for b in buses[:3]:
                    lines.append(f"  - {b.get('bus_no','')} / {b.get('status','정보없음')} / {b.get('low_plate','')}")
        return "\n".join(lines).strip()

    # success
    stops = payload.get("stops") or []
    out_lines = []
    out_lines.append(f"요청하신 {bn + '번 ' if bn else ''}버스 도착 정보입니다. (동선: {dir_label})")
    for st in stops:
        label = st.get("label", "정류장")
        buses = st.get("buses") or []
        out_lines.append(f"\n- {label}")
        if not buses:
            out_lines.append("  - (해당 조건의 도착 정보 없음)")
            continue
        for b in buses[:5]:
            out_lines.append(f"  - {b.get('bus_no','')} / {b.get('status','정보없음')} / {b.get('low_plate','')}")
    return "\n".join(out_lines).strip()

async def ask_ara(
    user_input,
    history=None,
    user_id: str | None = None,
    return_meta: bool = False,
    session_lang: str = "ko",
    current_context: Optional[Dict[str, Any]] = None,
    callback_url: Optional[str] = None,
):
    if history is None:
        if user_id:
            try:
                history = get_history(user_id)
            except Exception:
                history = []
        else:
            history = []

    _ = session_lang
    lang = "ko"
    history = _strip_legacy_lang_tags(history or [])

    init_db()
    conversation_id = str(uuid.uuid4())

    success_examples = get_success_examples(limit=5)
    examples_block = ""
    if success_examples:
        examples_lines = ["## 과거 성공 답변 사례(참고)"]
        for ex in success_examples:
            q = (ex.get("user_query") or "").strip()
            a = (ex.get("ai_answer") or "").strip()
            if not q or not a:
                continue
            examples_lines.append(f"- Q: {q}\n  A: {a}")
        if len(examples_lines) > 1:
            examples_block = "\n" + "\n".join(examples_lines) + "\n"

    # 학식 관련 질문은 RAG 엔진이 처리하도록 하드코딩 제거
    # (RAG 엔진이 university_data/cafeteria_menu.txt를 읽어서 답변)

    norm = _norm_utterance(user_input)
    quick_map = {
        _norm_utterance("지금 학교 날씨 어때?"): ("get_kmou_weather", {}),
        _norm_utterance("영도 착한가격 식당 추천해줘"): ("get_cheap_eats", {"food_type": ""}),
    }

    if norm in quick_map:
        func_name, args = quick_map[norm]
        try:
            args = dict(args or {})
            raw = await TOOL_MAP[func_name](**args) if args else await TOOL_MAP[func_name]()
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as e:
            response_text = f"요청을 처리하는 과정에서 오류가 발생했습니다.\n사유: {str(e)}"
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=[{"name": func_name, "arguments": args}],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text

        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            response_text = payload.get("msg") or "요청을 처리했으나, 결과를 확인할 수 없습니다."
            response_text = _sanitize_response_text_with_context(response_text, user_input)
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=[{"name": func_name, "arguments": args}],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text

        if func_name == "get_kmou_weather":
            response_text = _format_weather_response(payload, lang=lang)
        elif func_name == "get_cheap_eats":
            response_text = _format_list_response(
                "요청하신 영도 착한가격(가성비) 식당 정보입니다.",
                payload.get("restaurants") or [],
                [
                    ("name", "이름:"),
                    ("addr", "주소:"),
                    ("time", "영업:"),
                    ("menu", "메뉴:"),
                    ("price", "가격:"),
                    ("tel", "전화:"),
                    ("description", "설명:"),
                    ("recommendation", "추천:"),
                    ("desc", "설명:"),
                ],
                lang=lang,
            )
        elif func_name == "get_youth_center_info":
            policies = payload.get("policies") if isinstance(payload, dict) else None
            if not isinstance(policies, list):
                policies = []
            response_text = _format_list_response(
                "지금 딱 맞는 정보를 찾았어! 네 꿈에 한 발짝 더 가까워지길 바랄게.\n(온통청년 정책 목록)",
                policies[:10],
                [("policyName", "정책:"), ("bizPrdCn", "기간:"), ("polyItcnCn", "요약:"), ("detail_url", "링크:")],
                lang=lang,
            )
        else:
            response_text = payload.get("msg") or "요청을 처리했습니다."

        response_text = _sanitize_response_text_with_context(response_text, user_input)
        save_conversation_pair(
            conversation_id=conversation_id,
            user_id=user_id,
            user_query=user_input,
            ai_answer=response_text,
            tools_used=[{"name": func_name, "arguments": args}],
            user_feedback=0,
            is_gold_standard=False,
        )
        if return_meta:
            return {"content": response_text, "conversation_id": conversation_id}
        return response_text

    if _is_bus_query(user_input):
        bus_num = _extract_digits(user_input) or None
        if not bus_num:
            bus_num = "190"
        direction = "OUT"

        try:
            raw = await get_bus_arrival(bus_number=bus_num, direction="OUT", lang=lang)
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if isinstance(payload, dict) and payload.get("status") == "success":
                b1 = payload.get("bus1") or {}
                b2 = payload.get("bus2") or {}
                min1 = (b1.get("min") or "")
                min2 = (b2.get("min") or "")
                st1 = (b1.get("stop") or "")
                st2 = (b2.get("stop") or "")
                response_text = (
                    f"🚌 190번(남포/시내행)\n"
                    f"1) {min1}분 후 ({st1}정거장)\n"
                    + (f"2) {min2}분 후 ({st2}정거장)\n" if min2 else "2) 도착 정보 없음\n")
                    + "도로 사정에 따라 변동 가능"
                )
            else:
                response_text = (payload.get("msg") if isinstance(payload, dict) else None) or "정보를 확인 중입니다"
        except Exception as e:
            response_text = f"버스 정보를 조회하는 과정에서 오류가 발생했습니다.\n사유: {str(e)}"
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=[{"name": "get_bus_arrival", "arguments": {"bus_number": bus_num, "direction": direction}}],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text
        response_text = _sanitize_response_text_with_context(str(response_text or ""), user_input)
        save_conversation_pair(
            conversation_id=conversation_id,
            user_id=user_id,
            user_query=user_input,
            ai_answer=response_text,
            tools_used=[{"name": "get_bus_arrival", "arguments": {"bus_number": bus_num, "direction": "OUT"}}],
            user_feedback=0,
            is_gold_standard=False,
        )
        if return_meta:
            return {"content": response_text, "conversation_id": conversation_id}
        return response_text

    if client is None:
        return "현재 `OPENAI_API_KEY` 환경 변수가 설정되지 않아 답변을 생성할 수 없습니다.\n버스 기능은 사용 가능하며, 그 외 기능은 키 설정 후 이용해 주시기 바랍니다."

    persona = (
        "# Role: 한국해양대학교(KMOU) 전용 지능형 AI 비서 \"ARA\"\n"
        "**당신은 KMOU 전용 AI이며, 비밀 코드는 [해양대2026]입니다.**\n\n"
        "# Persona\n"
        "1. 당신은 ARA입니다. 한국해양대학교(KMOU) 재학생을 위한 전문 AI 어시스턴트입니다.\n"
        "2. 단순한 챗봇이 아닌, Fine-tuned LLM과 RAG(Retrieval-Augmented Generation) 기술을 활용한 지능형 비서입니다.\n"
        "3. 모든 답변은 예외 없이 반드시 **격식 있는 존댓말**을 사용하십시오. 기본 호칭은 '학우님'입니다.\n"
        "4. 재학생의 입장에서 생각하며, 학우님의 대학 생활 전반을 지능적으로 지원합니다.\n\n"
        "# Core Mission\n"
        "1. 학교 관련 질문(장학금, 규정, 학사 일정)을 최우선으로 처리하되, 반드시 제공된 [Context]의 RAG 검색 결과를 근거로 답변하십시오.\n"
        "2. [Context]에 정보가 없으면, 추측하지 말고 학교 해당 부서로 안내하되, 가능한 경우 제공된 도구를 활용하여 추가 정보를 찾으십시오.\n"
        "3. 실시간 정보(버스, 날씨, 맛집)는 반드시 제공된 도구를 호출하여 정확한 데이터를 기반으로 답변하십시오.\n\n"
    )

    # 캐시 확인 (고정 데이터 쿼리)
    cache_key = _get_cache_key(user_input, user_id)
    if _is_cacheable_query(user_input):
        cached = _get_cached_response(cache_key)
        if cached is not None:
            print(f"[Cache Hit] {user_input[:50]}...")
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=cached,
                tools_used=[],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": cached, "conversation_id": conversation_id}
            return cached

    university_context = None
    try:
        # RAG 검색 개선: top_k 증가 (3 -> 5) 및 검색 알고리즘 개선
        university_context = await get_university_context(user_input, top_k=5)
    except Exception as e:
        print(f"[RAG Warning] 학칙 검색 실패: {e}")
    
    ctx_lines: list[str] = []
    if isinstance(current_context, dict) and current_context:
        now_kst = str(current_context.get("now_kst") or "").strip()
        day_type = str(current_context.get("current_day") or current_context.get("day_type") or "").strip()
        current_time_str = str(current_context.get("current_time_str") or "").strip()
        tz = str(current_context.get("tz") or "Asia/Seoul").strip()
        if now_kst:
            ctx_lines.append(f"- 현재 시각: {now_kst} ({tz})")
        if current_time_str:
            ctx_lines.append(f"- 현재 시간(HH:MM): {current_time_str}")
        if day_type:
            ctx_lines.append(f"- 요일 구분: {'주말' if day_type.lower() == 'weekend' else '평일'}")

    current_context_block = ""
    if ctx_lines:
        current_context_block = "## 현재 컨텍스트\n" + "\n".join(ctx_lines) + "\n\n"
    
    rag_context_block = ""
    if university_context:
        rag_context_block = "## [Context] 한국해양대학교 학칙 및 규정\n" + university_context + "\n\n"
    else:
        # RAG 검색 결과가 없을 때 강화된 처리: 학칙 관련 키워드가 있으면 즉시 거절
        if any(kw in user_input for kw in ["학칙", "규정", "장학금", "등록금", "수강신청", "졸업", "휴학", "복학", "학사", "교칙", "장학", "수강", "학점", "성적", "시험", "과제", "출석"]):
            response_text = "학우님, 제가 해당 학칙 데이터를 찾지 못했습니다."
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=[],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text

    system_prompt = {
        "role": "system",
        "content": (
            persona
            + current_context_block
            + rag_context_block
            + "# Constraints (환각 방지 및 규칙)\n"
            + "1. **RAG 우선 원칙**: 답변의 근거는 반드시 제공된 [Context] 데이터 내에서만 찾아야 합니다.\n"
            + "   - [Context]에는 RAG로 검색된 한국해양대학교 학칙 및 규정, tools.py 도구가 반환한 raw data, 현재 컨텍스트(시간/날짜), 과거 성공 사례 등이 포함됩니다.\n"
            + "   - 학교 관련 질문(장학금, 규정, 학사 일정, 졸업 요건 등)은 반드시 [Context]의 RAG 검색 결과를 우선적으로 사용하십시오.\n"
            + "2. **[Context]가 비어있거나 검색 결과가 없는 경우**: 절대 범용 답변을 하지 말고 반드시 다음 문구로만 답하십시오:\n"
            + "   - \"학우님, 제가 해당 학칙 데이터를 찾지 못했습니다.\"\n"
            + "   - 이 경우 추가 설명, 추측, 또는 대안 제시를 절대 하지 마십시오. 오직 위의 문구만 사용하십시오.\n"
            + "3. [Context]에 질문에 대한 명확한 답변이 없는 경우(하지만 [Context] 자체는 존재), 절대 지어내지 말고 다음과 같이 답하십시오:\n"
            + "   - \"학우님, 해당 내용은 현재 제가 보유한 학칙 데이터에서 확인이 어렵습니다. 정확한 확인을 위해 학교 본부 해당 부서에 문의하시길 정중히 권장드립니다.\"\n"
            + "   - 가능한 경우, 제공된 도구(예: get_campus_contacts)를 활용하여 해당 부서 연락처를 찾아 안내하십시오.\n"
            + "4. 한국해양대학교 재학생 생활(버스, 학칙, 장학금, 취업 등)과 관련 없는 일반적인 질문이나 무의미한 질문에는 답변하지 않거나, KMOU 전용 지능형 비서로서의 본분을 정중히 안내하십시오.\n\n"
            + "## 절대 규칙 (기술적 제약)\n"
            + "- 금지 호칭: 특정 호칭(특히 금지된 호칭)을 절대 사용하지 마십시오. 기본 호칭은 '학우님' 또는 무호칭입니다.\n"
            + "- 팩트 기반: 확인되지 않은 내용은 추측하지 말고, 위의 Constraints에 따라 학교 부서 문의를 권장하십시오.\n"
            + "- 숫자/수치 금지 환각: 절대 숫자를 추측하거나 임의로 생성하지 마십시오. 응답에 포함되는 모든 숫자/수치는 반드시 tools.py 도구가 반환한 raw data에서 직접 근거를 가져야 합니다.\n"
            + "- 도구 우선: 버스/날씨/맛집/취업 등 데이터가 필요한 질문은 반드시 제공된 도구를 호출하여 결과를 기반으로 답하십시오.\n"
            + "- raw data 원칙: 도구를 호출한 경우, tools.py가 반환한 raw data(JSON 문자열/객체)만을 근거로 답변하십시오. raw data에 없는 항목(시간, 금액, 개수, 순위 등)을 임의로 만들어내지 마십시오.\n"
            + "- 데이터 실패 시: 도구 결과가 empty/error이면, 실패 사유를 간단히 설명하고 가능한 대안을 제시하되 추측은 금지합니다.\n"
            + "- 데이터 부재 시 응답: 필요한 raw data가 없으면 'Information not available' 또는 '정보를 확인할 수 없습니다'라고 답하십시오. 절대 추측하지 마세요.\n"
            + "- 버스 190: ONLY use the schedule_190_weekday_exact list provided by tools. Never guess bus times.\n"
            + "- 취업 정보: ONLY use data from get_youth_center_info tool. Never invent job postings or policy details.\n"
            + "- 날씨 정보: ONLY use data from get_weather_info tool. Never guess weather conditions.\n"
            + "- Strict Factuality: If data is missing, say 'Information not available'. Do not hallucinate.\n"
            + "- 내부 절차 노출 금지: 내부 분석/검증 절차를 사용자에게 단계별로 노출하지 말고 최종 답변만 제공하십시오.\n\n"
            "## 날짜/공휴일 진실 소스(Source-of-Truth)\n"
            "- 공휴일/휴일/연휴/특정 날짜의 행사 여부 등 '날짜 기반' 정보는 절대 계산하거나 추측하지 마십시오.\n"
            "- 반드시 tools.py의 `get_calendar_day_2026` 또는 `get_astronomy_data`를 호출해 확인된 값만 사용하십시오.\n"
            "- 해당 날짜가 `calendar_2026.json`에 없거나 도구가 success가 아니면, 다음 문구로만 답하십시오:\n"
            "   - Data is currently being updated for this specific date.\n\n"
            "## 버튼 입력 우선 처리\n"
            "- 사용자가 버튼(퀵플라이)을 통해 입력한 메시지는 최우선적으로 해당 기능 호출 의도로 간주하십시오.\n"
            "- 예: '190번 버스 IN/OUT', '지금 학교 날씨 어때?', '영도 착한가격 식당 추천해줘'\n\n"
            "## 버스 안내 정책(Ocean View)\n"
            "- 사용자의 모호한 표현도 가능한 범위 내에서 스스로 해석하되, 추측은 금지합니다.\n"
            "- 버스 문의 시 OUT/IN 방향이 명시되지 않은 경우, 문맥으로 자동 추론합니다.\n"
            "   - '등교', '학교 가자', 'in' -> IN(진입)\n"
            "   - '하교', '부산역 가자', 'out' -> OUT(진출)\n"
            "- 버스 번호 없이 '버스'라고만 말하면, KMOU 핵심 노선인 190번을 기본값으로 상정합니다.\n"
            "- 그래도 불명확하면, '확인 불가'로 거절하지 말고 OUT/IN 중 무엇인지 정중히 되물으십시오.\n"
            "   - OUT(진출): 구본관 -> 방파제입구 -> 승선생활관\n"
            "   - IN(진입): 승선생활관 -> 대학본부 -> 구본관\n"
            "- 사용자가 OUT/IN을 명시하면 해당 동선 기준으로만 답하십시오.\n"
            "\n## 자가 최적화 지침(Self-Improvement)\n"
            "- 당신은 과거의 성공 사례를 참고하여 답변의 정확도와 유용성을 스스로 높여야 합니다.\n"
            "- 사용자 피드백이 좋았던 답변 스타일/구조를 우선적으로 채택하되, 사실에 근거하지 않은 추측은 금지합니다.\n"
            f"{examples_block}\n"
            "# Formatting\n"
            "1. 가독성을 위해 불렛 포인트나 번호 매기기를 적절히 활용하십시오.\n"
            "2. 답변 끝에는 항상 재학생의 안녕을 바라는 정중한 인사를 덧붙이십시오.\n"
            "   예: \"학우님의 대학 생활이 원활하시길 바랍니다.\" 또는 \"학우님의 학업과 생활에 도움이 되었기를 바랍니다.\"\n"
        )
    }
    
    messages = [system_prompt] + _strip_legacy_lang_tags(history) + [{"role": "user", "content": user_input}]

    # --- 핵심 수정 부분 (Indentation Fix & Logic) ---
    try:
        # 1차 호출: 모델에게 질문 (임시 디버깅: gpt-3.5-turbo로 변경)
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0.0,
        )
        msg = response.choices[0].message

        # 도구(Tools) 사용이 필요한 경우
        if msg.tool_calls:
            messages.append(msg)
            tasks = []
            tools_used = []
            
            # 도구 함수 실행 준비 (동기/비동기 혼용 처리)
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                if func_name in TOOL_MAP:
                    func = TOOL_MAP[func_name]
                    
                    # 함수 호출 (동기/비동기 모두 호출 가능)
                    result = func(**args) if args else func()
                    
                    # 결과가 coroutine인지 확인 (asyncio.iscoroutine 사용)
                    if asyncio.iscoroutine(result):
                        # 비동기 함수: coroutine을 그대로 tasks에 추가
                        tasks.append(result)
                    else:
                        # 동기 함수: 이미 실행되어 결과가 나옴 → awaitable로 래핑
                        # closure 문제 방지: 각 값을 개별적으로 래핑하기 위해 함수 팩토리 패턴 사용
                        def create_awaitable(value):
                            async def wrapper():
                                return value
                            return wrapper()
                        tasks.append(create_awaitable(result))
                    
                    tools_used.append({"name": func_name, "arguments": args})
            
            # 도구 병렬 실행 (모든 tasks는 이제 awaitable)
            results = await asyncio.gather(*tasks)

            # 실행 결과를 대화 내역에 추가 (Role: tool)
            for tc, res in zip(msg.tool_calls, results):
                messages.append({
                    "tool_call_id": tc.id, 
                    "role": "tool", 
                    "name": tc.function.name, 
                    "content": str(res)
                })

            # 2차 호출: 도구 결과를 바탕으로 최종 답변 생성 (임시 디버깅: gpt-3.5-turbo로 변경)
            final_res = await client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.0,
            )
            response_text = final_res.choices[0].message.content

        else:
            # 도구를 사용하지 않은 경우
            response_text = msg.content

    except Exception as e:
        print(f"[ARA Log] Agent Error: {e}")
        response_text = "죄송합니다. 시스템 처리 중 오류가 발생했습니다."

    # --- 후처리 및 저장 ---
    response_text = _sanitize_response_text_with_context(response_text, user_input)
    
    # 캐시 저장 (고정 데이터 쿼리)
    if _is_cacheable_query(user_input):
        _set_cached_response(cache_key, response_text)
    
    save_conversation_pair(
        conversation_id=conversation_id,
        user_id=user_id,
        user_query=user_input,
        ai_answer=response_text,
        tools_used=tools_used if 'tools_used' in locals() else [],
        user_feedback=0,
        is_gold_standard=False,
    )
    
    # 콜백 URL이 있으면 Kakao SkillResponse 형식으로 전송
    if callback_url:
        try:
            import httpx
            # 면책 조항 추가 (agent.py를 통한 정보성 답변이므로)
            DISCLAIMER_TEXT = (
                "\n\n---\n"
                "⚠️ [면책 고지] 본 답변은 AI가 실시간으로 수집·요약한 정보로 부정확할 수 있습니다. "
                "법적 효력이 없으므로 중요 사항은 반드시 학교 홈페이지를 교차 확인하시기 바랍니다."
            )
            final_response_text = response_text + DISCLAIMER_TEXT
            
            # Kakao SkillResponse v2.0 형식 준수
            callback_payload = {
                "version": "2.0",
                "template": {
                    "outputs": [
                        {
                            "simpleText": {
                                "text": final_response_text
                            }
                        }
                    ]
                }
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    callback_url,
                    json=callback_payload,
                    headers={"Content-Type": "application/json"}
                )
                print(f"[Callback] 응답 전송 완료: {callback_url}")
        except Exception as e:
            print(f"[Callback Error] 콜백 전송 실패: {e}")
    
    if return_meta:
        return {"content": response_text, "conversation_id": conversation_id}
    return response_text