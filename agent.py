import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import (
    TOOLS_SPEC, get_weather_real, search_kmou_web, 
    search_campus_knowledge, get_user_profile
)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    user_name = await get_user_profile(user_id)

    # 1. 히스토리 정제
    if history and history[0].get("role") not in ["system", "user"]:
        history = []

    if not history:
        history.append({
            "role": "system", 
            "content": f"너는 한국해양대학교 AI 비서 '아라'다. 사용자는 {user_name} 선장님이다. 반드시 도구를 사용하여 얻은 정보로만 3줄 이내로 친절히 답변하라."
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # 1차 호출 (도구 사용 판단)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS_SPEC,
            temperature=0 # 정확도 향상을 위해 0 설정
        )
        msg = response.choices[0].message
        
        if msg.tool_calls:
            # 2. Pydantic 객체 직렬화 (중요: exclude_none=True 권장)
            history.append(msg.model_dump(exclude_none=True))
            tasks = []
            
            for tc in msg.tool_calls:
                f_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except:
                    args = {}
                
                # 3. KeyError 'query' 방어 로직
                q = args.get('query') or args.get('argument') or user_input

                if f_name == "get_weather_real":
                    tasks.append(get_weather_real())
                elif f_name == "search_kmou_web":
                    tasks.append(search_kmou_web(q))
                elif f_name == "search_campus_knowledge":
                    tasks.append(search_campus_knowledge(q))

            results = await asyncio.gather(*tasks)
            
            for tc, res in zip(msg.tool_calls, results):
                history.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "name": tc.function.name,
                    "content": str(res)
                })
            
            # 2차 호출 (최종 답변 생성)
            final_res = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=history
            )
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer

    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "선장님, 지금 데이터 바다에 안개가 짙어 정보를 찾지 못했습니다. 잠시 후 다시 불러주세요! 🌊"