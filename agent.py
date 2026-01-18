import os
import json
import asyncio
from openai import AsyncOpenAI
from tools import TOOLS_SPEC, get_bus_arrival, get_cheap_eats, get_medical_info, get_kmou_weather, get_festival_info

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TOOL_MAP = {
    "get_bus_arrival": get_bus_arrival,
    "get_cheap_eats": get_cheap_eats,
    "get_medical_info": get_medical_info,
    "get_kmou_weather": get_kmou_weather,
    "get_festival_info": get_festival_info
}

async def ask_ara(user_input, history=[]):
    # 시스템 프롬프트: 사용자의 입력을 분석하여 도구를 호출하도록 유도
    system_prompt = {
        "role": "system", 
        "content": (
            "당신은 한국해양대학교 학생들을 위한 AI 비서 'ARA'입니다. "
            "추측하지 말고 반드시 제공된 도구(API)를 사용하여 팩트 기반으로 답변하십시오. "
            "답변은 친절하지만 핵심만 간결하게, 3줄 내외로 요약하여 제공하십시오."
        )
    }
    
    messages = [system_prompt] + history + [{"role": "user", "content": user_input}]

    try:
        # 1. 도구 호출 여부 결정
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, tools=TOOLS_SPEC, tool_choice="auto"
        )
        msg = response.choices[0].message

        # 2. 도구 실행 (필요 시)
        if msg.tool_calls:
            messages.append(msg) # 대화 기록에 도구 호출 내역 추가
            
            # 병렬 처리를 위한 태스크 수집
            tasks = []
            for tc in msg.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                if func_name in TOOL_MAP:
                    func = TOOL_MAP[func_name]
                    tasks.append(func(**args) if args else func())
            
            # 비동기 병렬 실행
            results = await asyncio.gather(*tasks)

            # 결과 메시지 생성
            for tc, res in zip(msg.tool_calls, results):
                messages.append({
                    "tool_call_id": tc.id, "role": "tool", 
                    "name": tc.function.name, "content": str(res)
                })

            # 3. 최종 답변 생성
            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", messages=messages
            )
            return final_res.choices[0].message.content
        
        return msg.content

    except Exception as e:
        print(f"Error: {e}")
        return "죄송합니다. 통신 상태가 좋지 않아 정보를 불러오지 못했습니다."