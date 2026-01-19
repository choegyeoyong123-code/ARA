import os
import json
import asyncio
import re
import uuid
from openai import AsyncOpenAI
from tools import TOOLS_SPEC, get_bus_arrival, get_cheap_eats, get_medical_info, get_kmou_weather, get_festival_info
from database import init_db, save_conversation_pair, get_success_examples

_OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_cheap_eats": get_cheap_eats,
    "get_medical_info": get_medical_info,
    "get_kmou_weather": get_kmou_weather,
    "get_festival_info": get_festival_info
}

_BANNED_ADDRESSING_PATTERNS = [
    r"선장님",
    r"\bCaptain\b",
]

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

def _format_weather_response(payload: dict) -> str:
    status = payload.get("status")
    if status != "success":
        return payload.get("msg") or "날씨 정보를 확인할 수 없습니다."

    w = payload.get("weather") or {}
    lines = ["요청하신 해양대(영도구 동삼동) 날씨 정보입니다."]
    if w.get("temp"):
        lines.append(f"- 기온: {w.get('temp')}")
    if w.get("time"):
        lines.append(f"- 기준 시각: {w.get('time')}")
    if w.get("location"):
        lines.append(f"- 위치: {w.get('location')}")
    return "\n".join(lines).strip()

def _format_list_response(title: str, items: list, fields: list[tuple[str, str]]) -> str:
    if not items:
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

async def ask_ara(user_input, history=None, user_id: str | None = None, return_meta: bool = False):
    if history is None:
        history = []

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
    quick_map = {
        _norm_utterance("지금 학교 날씨 어때?"): ("get_kmou_weather", {}),
        _norm_utterance("영도 착한가격 식당 추천해줘"): ("get_cheap_eats", {"food_type": ""}),
        _norm_utterance("학교 근처 약국이나 병원 알려줘"): ("get_medical_info", {"kind": ""}),
        _norm_utterance("지금 부산에 하는 축제 있어?"): ("get_festival_info", {}),
    }

    if norm in quick_map:
        func_name, args = quick_map[norm]
        try:
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
            response_text = _format_weather_response(payload)
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
            )
        elif func_name == "get_medical_info":
            response_text = _format_list_response(
                "요청하신 학교 근처 약국/병원 정보입니다.",
                payload.get("hospitals") or [],
                [("name", "기관:"), ("kind", "종류:"), ("addr", "주소:"), ("tel", "전화:"), ("time", "시간:")],
            )
        elif func_name == "get_festival_info":
            response_text = _format_list_response(
                "요청하신 부산 축제/행사 정보입니다.",
                payload.get("festivals") or [],
                [("title", "제목:"), ("place", "장소:"), ("date", "일정:")],
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

    # ---------------------
    # Deterministic bus intent handling (fuzzy + direction inference + fallback)
    # ---------------------
    if _is_bus_query(user_input):
        bus_num = _extract_digits(user_input) or None
        # 버스 번호가 없으면 KMOU 핵심 노선 190을 기본값으로 상정
        if not bus_num:
            bus_num = "190"
        direction = _infer_direction(user_input)
        if direction is None:
            # '확인 불가' 대신 되묻기
            response_text = (
                "버스 동선을 확인해야 정확히 안내드릴 수 있습니다.\n"
                "OUT(진출): 구본관 → 방파제입구 → 승선생활관\n"
                "IN(진입): 승선생활관 → 대학본부 → 구본관\n"
                "예) '190 OUT 버스', '101 IN 버스'\n"
                "참고: 발화에 '학교/등교'가 포함되면 IN, '부산역/하교'가 포함되면 OUT으로 자동 추론합니다."
            )
            save_conversation_pair(
                conversation_id=conversation_id,
                user_id=user_id,
                user_query=user_input,
                ai_answer=response_text,
                tools_used=[{"name": "get_bus_arrival", "arguments": {"bus_number": bus_num, "direction": None}}],
                user_feedback=0,
                is_gold_standard=False,
            )
            if return_meta:
                return {"content": response_text, "conversation_id": conversation_id}
            return response_text

        try:
            raw = await get_bus_arrival(bus_number=bus_num, direction=direction)
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
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

        # 가장 근접한 데이터 제공(필터가 비어있으면 동일 동선에서 필터 해제 재조회)
        if payload.get("status") == "empty" and bus_num:
            try:
                raw2 = await get_bus_arrival(bus_number=None, direction=direction)
                payload2 = json.loads(raw2) if isinstance(raw2, str) else (raw2 or {})
                response_text = _sanitize_response_text_with_context(
                    _format_bus_response(payload2, bus_num, direction, used_fallback=True),
                    user_input,
                )
                save_conversation_pair(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_query=user_input,
                    ai_answer=response_text,
                    tools_used=[
                        {"name": "get_bus_arrival", "arguments": {"bus_number": bus_num, "direction": direction}},
                        {"name": "get_bus_arrival", "arguments": {"bus_number": None, "direction": direction}},
                    ],
                    user_feedback=0,
                    is_gold_standard=False,
                )
                if return_meta:
                    return {"content": response_text, "conversation_id": conversation_id}
                return response_text
            except Exception:
                response_text = _sanitize_response_text_with_context(
                    _format_bus_response(payload, bus_num, direction, used_fallback=False),
                    user_input,
                )
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

        response_text = _sanitize_response_text_with_context(_format_bus_response(payload, bus_num, direction), user_input)
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

    # OpenAI 키가 없으면(버스 외) 답변 생성 불가
    if client is None:
        return (
            "현재 `OPENAI_API_KEY` 환경 변수가 설정되지 않아 답변을 생성할 수 없습니다.\n"
            "버스 기능은 사용 가능하며, 그 외 기능은 키 설정 후 이용해 주시기 바랍니다."
        )

    system_prompt = {
        "role": "system", 
        "content": (
            "당신은 한국해양대학교(KMOU) 학생들을 위한 스마트 AI 비서 'ARA'입니다.\n"
            "항상 매우 정중하고 전문적인 비즈니스 어조로 한국어로 답변하십시오.\n\n"
            "## 절대 규칙\n"
            "- 금지 호칭: 특정 호칭(특히 금지된 호칭)을 절대 사용하지 마십시오. 기본 호칭은 '사용자님' 또는 무호칭입니다.\n"
            "- 팩트 기반: 확인되지 않은 내용은 추측하지 말고, 필요한 경우 '확인할 수 없습니다'라고 명시하십시오.\n"
            "- 숫자/수치 금지 환각: 절대 숫자를 추측하거나 임의로 생성하지 마십시오. 응답에 포함되는 모든 숫자/수치는 반드시 tools.py 도구가 반환한 raw data에서 직접 근거를 가져야 합니다.\n"
            "- 도구 우선: 버스/날씨/의료/축제/맛집 등 데이터가 필요한 질문은 반드시 제공된 도구를 호출하여 결과를 기반으로 답하십시오.\n"
            "- raw data 원칙: 도구를 호출한 경우, tools.py가 반환한 raw data(JSON 문자열/객체)만을 근거로 답변하십시오. raw data에 없는 항목(시간, 금액, 개수, 순위 등)을 임의로 만들어내지 마십시오.\n"
            "- 데이터 실패 시: 도구 결과가 empty/error이면, 실패 사유를 간단히 설명하고 가능한 대안을 제시하되 추측은 금지합니다.\n"
            "- 데이터 부재 시 응답: 필요한 raw data가 없으면 '모르겠습니다/확인할 수 없습니다'라고 답하십시오.\n"
            "- 내부 절차 노출 금지: 내부 분석/검증 절차를 사용자에게 단계별로 노출하지 말고 최종 답변만 제공하십시오.\n\n"
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
    
    messages = [system_prompt] + history + [{"role": "user", "content": user_input}]

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
        print(f"Agent Error: {e}")
        return "죄송합니다. 시스템 과부하로 인해 답변을 드리기 어렵습니다."