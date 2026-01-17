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

# API 키 로드
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    
    if not history:
        history.append({
            "role": "system", 
            "content": """너는 한국해양대학교 내부 교통 특화 AI '아라'야. 🐬💙
            [필수 지침]
            1. 190번(구본관)과 88(A)번(승선생활관) 정보에만 집중해.
            2. 맛집은 반드시 '현재 영업 중' 여부와 지도 링크(🔗)를 포함해줘.
            3. 답변은 3줄 이내로, 친절한 존댓말로 해줘. (환각 금지)"""
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # [정밀도 확보] Temperature=0 설정으로 환각 방지
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0  
        )
        
        msg = response.choices[0].message
        
        # ⭐ [가장 중요] 객체를 딕셔너리로 변환하여 저장 에러 해결
        msg_dict = msg.model_dump()
        
        if msg.tool_calls:
            # history에 '객체'가 아닌 '변환된 딕셔너리'를 넣습니다.
            history.append(msg_dict) 
            tasks, call_ids = [], []
            
            for tool_call in msg.tool_calls:
                f_name = tool_call.function.name
                call_ids.append(tool_call.id)
                
                if f_name == "get_inside_bus_status": tasks.append(get_inside_bus_status())
                elif f_name == "get_shuttle_info": tasks.append(get_shuttle_info())
                elif f_name == "get_weather_real": tasks.append(get_weather_real())
                elif f_name == "get_meal": tasks.append(get_meal())
                elif f_name == "get_festivals": tasks.append(get_festivals())
                elif f_name == "get_busan_restaurants": tasks.append(get_busan_restaurants())
                elif f_name == "get_hospitals": tasks.append(get_hospitals())
            
            results = await asyncio.gather(*tasks)
            
            for cid, res in zip(call_ids, results):
                history.append({
                    "tool_call_id": cid,
                    "role": "tool",
                    "name": next(tc.function.name for tc in msg.tool_calls if tc.id == cid),
                    "content": str(res)
                })

            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", messages=history, temperature=0
            )
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        # 최종 답변 저장 및 반환
        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history) 
        return answer

    except Exception as e:
        print(f"🚨 Agent Error: {e}")
        return "아라가 잠시 기억을 정리 중이야! 🌊 3초 뒤에 다시 말 걸어줘!"