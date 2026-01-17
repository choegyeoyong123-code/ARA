import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history
from tools import (
    TOOLS_SPEC, get_weather_real, get_festivals, 
    get_busan_restaurants, get_hospitals, get_meal, 
    get_inside_bus_status, get_shuttle_info
)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    
    if not history:
        history.append({
            "role": "system", 
            "content": """너는 한국해양대학교 내부 교통 특화 AI '아라'야. 🐬💙
            [필수 지침]
            1. 학교 안까지 들어오는 190번(구본관)과 88(A)번(승선생활관) 정보에만 집중해.
            2. 나머지 외부 노선은 대기업 지도를 보라고 안내해.
            3. 답변은 무조건 3줄 이내로, 친절한 존댓말로 해줘."""
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # [정밀도 확보] 선장님 요청에 따른 Temperature=0 설정
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0  
        )
        
        msg = response.choices[0].message
        
        if msg.tool_calls:
            history.append(msg)
            tasks = []
            call_ids = []
            
            for tool_call in msg.tool_calls:
                f_name = tool_call.function.name
                call_ids.append(tool_call.id)
                
                # 도구 매핑 및 병렬 실행 준비
                if f_name == "get_inside_bus_status": tasks.append(get_inside_bus_status())
                elif f_name == "get_shuttle_info": tasks.append(get_shuttle_info())
                elif f_name == "get_weather_real": tasks.append(get_weather_real())
                elif f_name == "get_meal": tasks.append(get_meal())
                elif f_name == "get_festivals": tasks.append(get_festivals())
                elif f_name == "get_busan_restaurants": tasks.append(get_busan_restaurants())
                elif f_name == "get_hospitals": tasks.append(get_hospitals())
            
            results = await asyncio.gather(*tasks)
            
            for cid, res in zip(call_ids, results):
                history.append({"tool_call_id": cid, "role": "tool", "content": str(res)})

            final_response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=history,
                temperature=0
            )
            answer = final_response.choices[0].message.content
        else:
            answer = msg.content

        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history)
        return answer

    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "아라가 잠시 기억을 정리 중이야! 🌊 잠시 후에 다시 말 걸어줘!"

        # agent.py 내부의 system_prompt 수정
system_prompt_content = """너는 한국해양대학교 내부 교통 및 생활 밀착형 AI '아라'야. 🐬💙
[전략 가이드]
1. 190번(구본관)과 88(A)번(승선관) 정보에 집중할 것.
2. 맛집 추천 시 '현재 영업 중'인지 여부를 강조해서 알려줄 것.
3. 반드시 제공된 지도 링크(🔗)를 함께 전달하여 사용자가 바로 길찾기를 할 수 있게 해줘.
4. 답변은 3줄 이내로 간결하게!"""

# GPT 호출 시 temperature=0 설정을 통해 링크 주소를 임의로 지어내지 않게 함