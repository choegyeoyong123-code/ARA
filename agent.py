import os
import json
import asyncio
import re
import uuid
from typing import Any, Optional, Dict
from openai import AsyncOpenAI
from tools import (
    TOOLS_SPEC,
    get_bus_arrival,
    get_bus_190_tracker_busbusinfo,
    get_cheap_eats,
    get_medical_info,
    get_kmou_weather,
    get_weather_info,
    get_festival_info,
    get_shuttle_next_buses,
    search_restaurants,
    get_calendar_day_2026,
    get_astronomy_data,
    get_campus_contacts,
    get_academic_schedule,
)
from database import init_db, save_conversation_pair, get_success_examples, get_history, save_history

_OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_bus_190_tracker_busbusinfo": get_bus_190_tracker_busbusinfo,
    "get_cheap_eats": get_cheap_eats,
    "get_medical_info": get_medical_info,
    "get_kmou_weather": get_kmou_weather,
    "get_weather_info": get_weather_info,
    "get_festival_info": get_festival_info,
    "get_shuttle_next_buses": get_shuttle_next_buses,
    "search_restaurants": search_restaurants,
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

def _lang_to_tag(lang: str) -> str:
    return "[LANG:EN]" if (lang or "").strip().lower() == "en" else "[LANG:KO]"

def _lang_from_tag(content: str | None) -> str | None:
    if not content:
        return None
    m = _LANG_TAG_RE.match((content or "").strip())
    if not m:
        return None
    return "en" if m.group(1).upper() == "EN" else "ko"

def _extract_lang_from_history(history: list) -> str | None:
    # 태그는 history[0]에 유지(안전하게 앞 5개만 확인)
    for it in (history or [])[:5]:
        if isinstance(it, dict) and it.get("role") == "system":
            lang = _lang_from_tag(it.get("content"))
            if lang:
                return lang
    return None

def _strip_lang_tags(history: list) -> list:
    out = []
    for it in (history or []):
        if isinstance(it, dict) and it.get("role") == "system" and _lang_from_tag(it.get("content")):
            continue
        out.append(it)
    return out

def _ensure_lang_tag(history: list, lang: str) -> list:
    base = _strip_lang_tags(history)
    return [{"role": "system", "content": _lang_to_tag(lang)}] + base

def _save_history_preserve_lang(user_id: str, history: list, lang: str, limit: int = 20) -> None:
    # tag + last N non-tag messages
    base = _strip_lang_tags(history)
    trimmed = base[-max(0, int(limit)) :]
    save_history(user_id, [{"role": "system", "content": _lang_to_tag(lang)}] + trimmed)

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
    - '확인할 수 없습니다' 같은 단순 거절을, 질문 재구성/대안 안내로 보강합니다.
    """
    text = _sanitize_response_text(text)
    if not user_input:
        return text

    if _is_bus_query(user_input) and re.search(r"(확인할 수 없습니다|알 수 없습니다)", text):
        bus_num = _extract_digits(user_input) or "190"
        # 기존 거절 문구를 더 유용한 질문/안내로 치환
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
    # 오타 포함 번호만 있는 경우도 버스 의도로 간주(예: 190qjs)
    return bool(re.search(r"\d{2,4}", t)) and any(k in t for k in ["도착", "정류장", "위치", "언제", "몇분", "분"])

def _infer_direction(text: str) -> str | None:
    """
    사용자 발화에서 방향을 자동 추론합니다.
    - '학교'/'등교' 포함 -> IN
    - '부산역'/'하교' 포함 -> OUT
    - 명시(IN/OUT/진입/진출)가 있으면 우선
    """
    t = (text or "")
    tl = t.lower()
    # explicit
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
    """버튼 문구 매칭용 정규화(공백/대소문자/기호 차이를 완화)."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("?", "").replace("!", "").replace(".", "").replace(",", "")
    return t

def _format_weather_response(payload: dict, lang: str) -> str:
    status = payload.get("status")
    if status != "success":
        if (lang or "ko") == "en":
            return payload.get("msg") or "Unable to fetch weather information."
        return payload.get("msg") or "날씨 정보를 확인할 수 없습니다."

    w = payload.get("weather") or {}
    if (lang or "ko") == "en":
        lines = ["Here is the current weather near KMOU (Yeongdo-gu)."]
        if w.get("temp"):
            lines.append(f"- Temperature: {w.get('temp')}")
        if w.get("time"):
            lines.append(f"- Time (base): {w.get('time')}")
        if w.get("location"):
            lines.append(f"- Location: {w.get('location')}")
    else:
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
        return f"{title} 정보를 확인할 수 없습니다." if (lang or "ko") != "en" else "No verified results found."
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
):
    if history is None:
        if user_id:
            try:
                history = get_history(user_id)
            except Exception:
                history = []
        else:
            history = []

    # 세션 언어 고정: history 태그([LANG:..]) 우선, 없으면 main.py가 전달한 session_lang 사용
    stored_lang = _extract_lang_from_history(history or [])
    lang = (stored_lang or (session_lang or "ko")).strip().lower()
    if lang not in {"ko", "en"}:
        lang = "ko"
    history = _ensure_lang_tag(history or [], lang)
    if user_id and not stored_lang:
        try:
            _save_history_preserve_lang(user_id, history, lang, limit=25)
        except Exception:
            pass

    # DB 초기화(테이블/컬럼 보장)
    init_db()

    conversation_id = str(uuid.uuid4())

    # 과거 성공 사례(피드백 기반) few-shot 주입용
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

    # ---------------------
    # Fast path: 6개 버튼 문구는 OpenAI 우회(즉시 tools 호출)
    # - 카카오 응답 타임아웃 방지 목적
    # ---------------------
    norm = _norm_utterance(user_input)
    if norm in {_norm_utterance("학식"), _norm_utterance("오늘 학식"), _norm_utterance("오늘의 식단")}:
        # 크롤링/추측 금지: 공식 Coop 사이트 링크만 제공
        response_text = (
            "KMOU Coop: http://www.kmoucoop.or.kr/\n"
            if lang == "en"
            else "학식은 KMOU Coop에서 확인하실 수 있습니다.\nhttp://www.kmoucoop.or.kr/"
        )
        response_text = _sanitize_response_text_with_context(response_text, user_input)
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

    quick_map = {
        _norm_utterance("지금 학교 날씨 어때?"): ("get_kmou_weather", {}),
        _norm_utterance("영도 착한가격 식당 추천해줘"): ("get_cheap_eats", {"food_type": ""}),
        _norm_utterance("학교 근처 약국이나 병원 알려줘"): ("get_medical_info", {"kind": ""}),
        _norm_utterance("지금 부산에 하는 축제 있어?"): ("get_festival_info", {}),
    }

    if norm in quick_map:
        func_name, args = quick_map[norm]
        try:
            # tools 로컬라이즈: lang 전달(가능한 함수만)
            args = dict(args or {})
            if func_name in {"get_bus_arrival", "get_shuttle_next_buses", "get_kmou_weather", "get_campus_contacts", "get_academic_schedule", "get_weather_info"} and "lang" not in args:
                args["lang"] = lang
            raw = await TOOL_MAP[func_name](**args) if args else await TOOL_MAP[func_name]()
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception as e:
            response_text = (
                f"An error occurred while processing your request.\nReason: {str(e)}"
                if lang == "en"
                else f"요청을 처리하는 과정에서 오류가 발생했습니다.\n사유: {str(e)}"
            )
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
            response_text = payload.get("msg") or ("Unable to fetch results." if lang == "en" else "요청을 처리했으나, 결과를 확인할 수 없습니다.")
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
                "요청하신 영도 착한가격(가성비) 식당 정보입니다." if lang != "en" else "Here are Good Price restaurants near Yeongdo/KMOU.",
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
        elif func_name == "get_medical_info":
            response_text = _format_list_response(
                "요청하신 학교 근처 약국/병원 정보입니다." if lang != "en" else "Here are pharmacies/hospitals near KMOU.",
                payload.get("hospitals") or [],
                [("name", "기관:"), ("kind", "종류:"), ("addr", "주소:"), ("tel", "전화:"), ("time", "시간:")],
                lang=lang,
            )
        elif func_name == "get_festival_info":
            response_text = _format_list_response(
                "요청하신 부산 축제/행사 정보입니다." if lang != "en" else "Here are festival/events in Busan (verified dates only).",
                payload.get("festivals") or [],
                [("title", "제목:"), ("place", "장소:"), ("date", "일정:")],
                lang=lang,
            )
        else:
            response_text = payload.get("msg") or ("Done." if lang == "en" else "요청을 처리했습니다.")

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

    # ---------------------
    # Deterministic bus intent handling (fuzzy + direction inference + fallback)
    # ---------------------
    if _is_bus_query(user_input):
        bus_num = _extract_digits(user_input) or None
        # 버스 번호가 없으면 KMOU 핵심 노선 190을 기본값으로 상정
        if not bus_num:
            bus_num = "190"
        # 버스 방향은 OUT(남포행)으로 고정합니다.
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
                response_text = (payload.get("msg") if isinstance(payload, dict) else None) or ("정보를 확인 중입니다" if lang != "en" else "Data is being verified.")
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

    # OpenAI 키가 없으면(버스 외) 답변 생성 불가
    if client is None:
        return (
            "OPENAI_API_KEY is not configured, so I cannot generate an LLM response right now.\n"
            "Bus features may still work via tool routing."
            if lang == "en"
            else "현재 `OPENAI_API_KEY` 환경 변수가 설정되지 않아 답변을 생성할 수 없습니다.\n"
            "버스 기능은 사용 가능하며, 그 외 기능은 키 설정 후 이용해 주시기 바랍니다."
        )

    persona = (
        f"{_lang_to_tag(lang)}\n"
        + (
            "You are 'ARA', a smart assistant for Korea Maritime and Ocean University (KMOU) students.\n"
            "IMPORTANT: Respond ONLY in English.\n"
            "Tone: confident but polite; like a young builder/founder.\n\n"
            if lang == "en"
            else "당신은 한국해양대학교(KMOU) 학생들을 위한 스마트 AI 비서 'ARA'입니다.\n"
            "중요: 반드시 한국어로만 답변하십시오.\n"
            "톤: 20대 대학생 창업가/AI 맥시멀리스트 느낌의 자신감 있는 말투를 사용하되, 반드시 정중함과 공손함을 유지하십시오.\n\n"
        )
    )

    # 실시간 컨텍스트(시간 인지) — main.py에서 KST로 계산된 값만 주입
    ctx_lines: list[str] = []
    if isinstance(current_context, dict) and current_context:
        now_kst = str(current_context.get("now_kst") or "").strip()
        day_type = str(current_context.get("current_day") or current_context.get("day_type") or "").strip()
        current_time_str = str(current_context.get("current_time_str") or "").strip()
        tz = str(current_context.get("tz") or "Asia/Seoul").strip()
        if now_kst:
            if lang == "en":
                ctx_lines.append(f"- Now: {now_kst} ({tz})")
            else:
                ctx_lines.append(f"- 현재 시각: {now_kst} ({tz})")
        if current_time_str:
            if lang == "en":
                ctx_lines.append(f"- Current time: {current_time_str}")
            else:
                ctx_lines.append(f"- 현재 시간(HH:MM): {current_time_str}")
        if day_type:
            if lang == "en":
                ctx_lines.append(f"- Day type: {day_type}")
            else:
                ctx_lines.append(f"- 요일 구분: {'주말' if day_type.lower() == 'weekend' else '평일'}")

    current_context_block = ""
    if ctx_lines:
        current_context_block = ("## Current context\n" if lang == "en" else "## 현재 컨텍스트\n") + "\n".join(ctx_lines) + "\n\n"

    system_prompt = {
        "role": "system",
        "content": (
            persona
            + current_context_block
            + ("## Absolute rules\n" if lang == "en" else "## 절대 규칙\n")
            + "- 금지 호칭: 특정 호칭(특히 금지된 호칭)을 절대 사용하지 마십시오. 기본 호칭은 '사용자님' 또는 무호칭입니다.\n"
            + "- 팩트 기반: 확인되지 않은 내용은 추측하지 말고, 필요한 경우 '확인할 수 없습니다'라고 명시하십시오.\n"
            + "- 숫자/수치 금지 환각: 절대 숫자를 추측하거나 임의로 생성하지 마십시오. 응답에 포함되는 모든 숫자/수치는 반드시 tools.py 도구가 반환한 raw data에서 직접 근거를 가져야 합니다.\n"
            + "- 도구 우선: 버스/날씨/의료/축제/맛집 등 데이터가 필요한 질문은 반드시 제공된 도구를 호출하여 결과를 기반으로 답하십시오.\n"
            + "- raw data 원칙: 도구를 호출한 경우, tools.py가 반환한 raw data(JSON 문자열/객체)만을 근거로 답변하십시오. raw data에 없는 항목(시간, 금액, 개수, 순위 등)을 임의로 만들어내지 마십시오.\n"
            + "- 데이터 실패 시: 도구 결과가 empty/error이면, 실패 사유를 간단히 설명하고 가능한 대안을 제시하되 추측은 금지합니다.\n"
            + "- 데이터 부재 시 응답: 필요한 raw data가 없으면 '모르겠습니다/확인할 수 없습니다'라고 답하십시오.\n"
            + "- 내부 절차 노출 금지: 내부 분석/검증 절차를 사용자에게 단계별로 노출하지 말고 최종 답변만 제공하십시오.\n\n"
            "## 날짜/공휴일 진실 소스(Source-of-Truth)\n"
            "- 공휴일/휴일/연휴/특정 날짜의 행사 여부 등 '날짜 기반' 정보는 절대 계산하거나 추측하지 마십시오.\n"
            "- 반드시 tools.py의 `get_calendar_day_2026` 또는 `get_astronomy_data`를 호출해 확인된 값만 사용하십시오.\n"
            "- 해당 날짜가 `calendar_2026.json`에 없거나 도구가 success가 아니면, 다음 문구로만 답하십시오:\n"
            "  - Data is currently being updated for this specific date.\n\n"
            "## 버튼 입력 우선 처리\n"
            "- 사용자가 버튼(퀵플라이)을 통해 입력한 메시지는 최우선적으로 해당 기능 호출 의도로 간주하십시오.\n"
            "- 예: '190번 버스 IN/OUT', '지금 학교 날씨 어때?', '영도 착한가격 식당 추천해줘', '학교 근처 약국이나 병원 알려줘', '지금 부산에 하는 축제 있어?'\n\n"
            "## 버스 안내 정책(Ocean View)\n"
            "- 사용자의 모호한 표현도 가능한 범위 내에서 스스로 해석하되, 추측은 금지합니다.\n"
            "- 버스 문의 시 OUT/IN 방향이 명시되지 않은 경우, 문맥으로 자동 추론합니다.\n"
            "  - '등교', '학교 가자', 'in' -> IN(진입)\n"
            "  - '하교', '부산역 가자', 'out' -> OUT(진출)\n"
            "- 버스 번호 없이 '버스'라고만 말하면, KMOU 핵심 노선인 190번을 기본값으로 상정합니다.\n"
            "- 그래도 불명확하면, '확인 불가'로 거절하지 말고 OUT/IN 중 무엇인지 정중히 되물으십시오.\n"
            "  - OUT(진출): 구본관 -> 방파제입구 -> 승선생활관\n"
            "  - IN(진입): 승선생활관 -> 대학본부 -> 구본관\n"
            "- 사용자가 OUT/IN을 명시하면 해당 동선 기준으로만 답하십시오.\n"
            "\n## 자가 최적화 지침(Self-Improvement)\n"
            "- 당신은 과거의 성공 사례를 참고하여 답변의 정확도와 유용성을 스스로 높여야 합니다.\n"
            "- 사용자 피드백이 좋았던 답변 스타일/구조를 우선적으로 채택하되, 사실에 근거하지 않은 추측은 금지합니다.\n"
            f"{examples_block}"
        )
    }
    
    # LLM에는 태그를 system_prompt에만 주입하고, history의 태그 메시지는 제거하여 토큰 낭비를 줄입니다.
    messages = [system_prompt] + _strip_lang_tags(history) + [{"role": "user", "content": user_input}]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0.5,
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            tasks = []
            tools_used = []
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                # tools 로컬라이즈: lang 전달(가능한 함수만)
                if func_name in {"get_bus_arrival", "get_shuttle_next_buses", "get_kmou_weather", "get_campus_contacts", "get_academic_schedule", "get_weather_info"} and "lang" not in args:
                    args["lang"] = lang
                if func_name in TOOL_MAP:
                    tasks.append(TOOL_MAP[func_name](**args) if args else TOOL_MAP[func_name]())
                    tools_used.append({"name": func_name, "arguments": args})
            
            results = await asyncio.gather(*tasks)

            for tc, res in zip(msg.tool_calls, results):
                messages.append({
                    "tool_call_id": tc.id, "role": "tool", 
                    "name": tc.function.name, "content": str(res)
                })

            final_res = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.5,
            )
            response_text = _sanitize_response_text_with_context(final_res.choices[0].message.content, user_input)
            if user_id:
                try:
                    new_history = (history or []) + [{"role": "user", "content": user_input}, {"role": "assistant", "content": response_text}]
                    _save_history_preserve_lang(user_id, new_history, lang, limit=25)
                except Exception:
                    pass
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=tools_used,
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text
        
        response_text = _sanitize_response_text_with_context(msg.content, user_input)
        if user_id:
            try:
                new_history = (history or []) + [{"role": "user", "content": user_input}, {"role": "assistant", "content": response_text}]
                _save_history_preserve_lang(user_id, new_history, lang, limit=25)
            except Exception:
                pass
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

    except Exception as e:
        print(f"[ARA Log] Agent Error: {e}")
        return "죄송합니다. 시스템 과부하로 인해 답변을 드리기 어렵습니다."