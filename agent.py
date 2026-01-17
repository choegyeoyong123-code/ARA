import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import TOOLS_SPEC, get_weather_real, search_kmou_web # 임포트 일치 확인

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    
    # 꼬인 기록 초기화 (안전장치)
    if history and history[0].get("role") in ["tool", "assistant"]: history = []

    if not history:
        history.append({"role": "system", "content": "너는 해양대 AI 아라다. 도구 결과로만 3줄 이내 답변하라."})

    history.append({"role": "user", "content": user_input})

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini", messages=history, tools=TOOLS_SPEC, temperature=0
        )
        msg = response.choices[0].message
        
        # ⭐ 중요: Pydantic 객체를 딕셔너리로 변환하여 DB 에러 방지
        msg_dict = msg.model_dump()
        
        if msg.tool_calls:
            history.append(msg_dict)
            tasks = []
            for tc in msg.tool_calls:
                if tc.function.name == "get_weather_real": tasks.append(get_weather_real())
                elif tc.function.name == "search_kmou_web":
                    args = json.loads(tc.function.arguments)
                    tasks.append(search_kmou_web(args['query']))
            
            results = await asyncio.gather(*tasks)
            for tc, res in zip(msg.tool_calls, results):
                history.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": str(res)})
            
            final_res = await client.chat.completions.create(model="gpt-4o-mini", messages=history)
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer
    except Exception as e:
        print(f"🚨 에러 발생: {e}")
        return "잠시 후 다시 시도해주세요! 🌊"