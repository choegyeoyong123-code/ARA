import os
import json
import asyncio
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

async def ask_ara(user_input, history=[]):
    system_prompt = {
        "role": "system", 
        "content": (
            "당신은 한국해양대학교 학생들을 위한 **최고급 AI 컨시어지 ARA**입니다.\n"
            "사용자의 질문에 대해 API에서 실시간으로 가져온 데이터를 기반으로 **가장 구체적이고 도움이 되는 답변**을 제공해야 합니다.\n\n"
            
            "**[사고 과정 (Chain of Thought)]**\n"
            "1. **데이터 확보:** 필요한 도구를 호출하여 최신 JSON 데이터를 수집합니다. (맛집 질문 시 날씨 정보도 함께 확인하면 좋습니다.)\n"
            "2. **데이터 검증:** 데이터가 비어있거나 에러가 있다면 솔직하게 '정보를 가져올 수 없다'고 말하십시오.\n"
            "3. **맥락 연결:**\n"
            "   - 날씨(풍속, 강수량)를 고려하여 외출/배달 여부를 조언하십시오.\n"
            "   - 버스 도착 시간이 3분 이내라면 '서두르라'고, 20분 이상이면 '다른 일정을 보라'고 조언하십시오.\n"
            "4. **최종 답변:** 전문적이면서도 따뜻한 톤('선장님' 호칭 권장)으로 답변을 작성하십시오."
        )
    }
    
    messages = [system_prompt] + history + [{"role": "user", "content": user_input}]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, tools=TOOLS_SPEC, tool_choice="auto"
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
                model="gpt-4o-mini", messages=messages, temperature=0.3
            )
            return final_res.choices[0].message.content
        
        return msg.content

    except Exception as e:
        print(f"Agent Error: {e}")
        return "죄송합니다. 시스템 과부하로 인해 답변을 드리기 어렵습니다."