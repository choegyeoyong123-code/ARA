import os
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import (
    TOOLS_SPEC, get_inside_bus_status, get_shuttle_info, 
    get_weather_real, get_busan_restaurants, get_hospitals, get_meal
)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    
    # [보안] 깨진 기록(tool 역할이 첫 메시지인 경우 등) 청소
    if history and history[0].get("role") in ["tool", "assistant"]:
        history = []

    if not history:
        history.append({
            "role": "system", 
            "content": """너는 한국해양대학교 AI 가이드 '아라'다. 🐬
            [환각 방지 0% 지침]
            1. 제공된 도구(Tools)의 결과값에만 근거하여 답변하라.
            2. 도구가 "정보 없음"을 주면 절대 지어내지 말고 "데이터가 없습니다"라고 답하라.
            3. 실측 데이터가 없는 버스 시간이나 맛집은 절대 추측하지 마라."""
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # 1차 호출 (Temperature=0 고정)
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=history,
            tools=TOOLS_SPEC, tool_choice="auto", temperature=0
        )
        
        msg = response.choices[0].message
        # ⭐ 중요: OpenAI 객체를 딕셔너리로 변환하여 DB 에러 방지
        msg_dict = msg.model_dump()
        
        if msg.tool_calls:
            history.append(msg_dict)
            tasks = []
            for tool_call in msg.tool_calls:
                f_name = tool_call.function.name
                if f_name == "get_inside_bus_status": tasks.append(get_inside_bus_status())
                elif f_name == "get_shuttle_info": tasks.append(get_shuttle_info())
                elif f_name == "get_weather_real": tasks.append(get_weather_real())
                elif f_name == "get_busan_restaurants": tasks.append(get_busan_restaurants())
                # ... 기타 도구 추가
            
            results = await asyncio.gather(*tasks)
            
            for tool_call, res in zip(msg.tool_calls, results):
                history.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": tool_call.function.name,
                    "content": str(res)
                })

            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", messages=history, temperature=0
            )
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer
    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "데이터를 가져오는 중에 작은 파도가 쳤어요. 잠시 후 다시 시도해주세요! 🌊"