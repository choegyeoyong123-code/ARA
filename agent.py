import os
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import TOOLS_SPEC, get_inside_bus_status, get_weather_real # 주요 도구만 예시

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    
    # ⭐ [핵심] 기록 청소기: 첫 메시지가 tool이거나 assistant(call 없음)인 경우 리셋
    if history and (history[0].get("role") in ["tool", "assistant"] and "tool_calls" not in str(history[0])):
        history = []
        print(f"🧹 {user_id}의 깨진 기록을 초기화했습니다.")

    if not history:
        history.append({
            "role": "system", 
            "content": "너는 한국해양대 AI 아라야. 도구 결과값에만 근거하여 3줄 이내로 답해. (환각 금지)"
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # 1차 호출 (Temperature=0 고정)
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=history,
            tools=TOOLS_SPEC, tool_choice="auto", temperature=0
        )
        
        msg = response.choices[0].message
        msg_dict = msg.model_dump() # 직렬화 에러 방지
        
        if msg.tool_calls:
            history.append(msg_dict)
            tasks = []
            for tool_call in msg.tool_calls:
                # tools.py의 함수들과 매핑
                f_name = tool_call.function.name
                if f_name == "get_inside_bus_status": tasks.append(get_inside_bus_status())
                elif f_name == "get_weather_real": tasks.append(get_weather_real())
            
            results = await asyncio.gather(*tasks)
            
            for tc, res in zip(msg.tool_calls, results):
                history.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(res)})

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
        print(f"🚨 Agent 에러: {e}")
        return "데이터를 가져오는 중 파도가 쳤어요. 잠시 후 다시 말씀해주세요! 🛳️"