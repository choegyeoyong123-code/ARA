import os
import json
import asyncio
import re
from openai import AsyncOpenAI
from tools import TOOLS_SPEC, get_bus_arrival, get_cheap_eats, get_medical_info, get_kmou_weather, get_festival_info

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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

async def ask_ara(user_input, history=None):
    if history is None:
        history = []

    system_prompt = {
        "role": "system", 
        "content": (
            "당신은 한국해양대학교(KMOU) 학생들을 위한 스마트 AI 비서 'ARA'입니다.\n"
            "항상 매우 정중하고 전문적인 비즈니스 어조로 한국어로 답변하십시오.\n\n"
            "## 절대 규칙\n"
            "- 금지 호칭: 특정 호칭(특히 금지된 호칭)을 절대 사용하지 마십시오. 기본 호칭은 '사용자님' 또는 무호칭입니다.\n"
            "- 팩트 기반: 확인되지 않은 내용은 추측하지 말고, 필요한 경우 '확인할 수 없습니다'라고 명시하십시오.\n"
            "- 도구 우선: 버스/날씨/의료/축제/맛집 등 데이터가 필요한 질문은 반드시 제공된 도구를 호출하여 결과를 기반으로 답하십시오.\n"
            "- 데이터 실패 시: 도구 결과가 empty/error이면, 실패 사유를 간단히 설명하고 가능한 대안을 제시하되 추측은 금지합니다.\n"
            "- 내부 절차 노출 금지: 내부 분석/검증 절차를 사용자에게 단계별로 노출하지 말고 최종 답변만 제공하십시오.\n\n"
            "## 버스 안내 정책(Ocean View)\n"
            "- 버스 문의 시 OUT/IN을 반드시 명시해야 합니다. 사용자가 방향을 명시하지 않으면, 도구 호출 대신 먼저 아래 중 하나를 선택하도록 요청하십시오.\n"
            "  - OUT(진출): 구본관 -> 방파제입구 -> 승선생활관\n"
            "  - IN(진입): 승선생활관 -> 대학본부 -> 구본관\n"
            "- 사용자가 OUT/IN을 명시하면 해당 동선 기준으로만 답하십시오.\n"
        )
    }
    
    messages = [system_prompt] + history + [{"role": "user", "content": user_input}]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0,
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            tasks = []
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                if func_name in TOOL_MAP:
                    tasks.append(TOOL_MAP[func_name](**args) if args else TOOL_MAP[func_name]())
            
            results = await asyncio.gather(*tasks)

            for tc, res in zip(msg.tool_calls, results):
                messages.append({
                    "tool_call_id": tc.id, "role": "tool", 
                    "name": tc.function.name, "content": str(res)
                })

            final_res = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0,
            )
            return _sanitize_response_text(final_res.choices[0].message.content)
        
        return _sanitize_response_text(msg.content)

    except Exception as e:
        print(f"Agent Error: {e}")
        return "죄송합니다. 시스템 과부하로 인해 답변을 드리기 어렵습니다."