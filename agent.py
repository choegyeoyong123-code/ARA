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
    
    # 1. 시스템 프롬프트 초기화 (환각 방지 지침 결합)
    if not history:
        history.append({
            "role": "system", 
            "content": """너는 한국해양대학교 AI 가이드 '아라'다. 🐬💙
            [초정밀 답변 지침]
            1. 제공된 도구(Tools)의 반환값에만 근거하여 답변하라. 사실 관계에 있어 1%의 추측도 허용하지 않는다.
            2. 도구가 "정보 없음" 또는 "확인 불가"를 반환하면, 절대로 예측하거나 지어내지 마라.
            3. 도구 결과에 없는 버스 번호나 장소를 묻는다면 "현재 실시간 데이터로는 확인할 수 없습니다"라고만 답하라.
            4. 190번(구본관)과 88(A)번(승선생활관) 정보에만 집중하되, 친절한 존댓말로 3줄 이내로 답변하라.
            5. 맛집 정보 제공 시 반드시 '현재 영업 중' 여부와 지도 링크(🔗)를 포함하라."""
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # 2. 1차 호출: 도구 사용 여부 결정 (Temperature=0으로 일관성 확보)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS_SPEC,
            tool_choice="auto",
            temperature=0  
        )
        
        msg = response.choices[0].message
        msg_dict = msg.model_dump() # 객체 저장 에러 방지를 위한 딕셔너리 변환
        
        # 3. 도구 실행 로직
        if msg.tool_calls:
            history.append(msg_dict) 
            tasks, call_ids = [], []
            
            for tool_call in msg.tool_calls:
                f_name = tool_call.function.name
                call_ids.append(tool_call.id)
                
                # 도구 매핑
                if f_name == "get_inside_bus_status": tasks.append(get_inside_bus_status())
                elif f_name == "get_shuttle_info": tasks.append(get_shuttle_info())
                elif f_name == "get_weather_real": tasks.append(get_weather_real())
                elif f_name == "get_meal": tasks.append(get_meal())
                elif f_name == "get_festivals": tasks.append(get_festivals())
                elif f_name == "get_busan_restaurants": tasks.append(get_busan_restaurants())
                elif f_name == "get_hospitals": tasks.append(get_hospitals())
            
            # 비동기 병렬 실행으로 속도 최적화
            results = await asyncio.gather(*tasks)
            
            for cid, res in zip(call_ids, results):
                history.append({
                    "tool_call_id": cid,
                    "role": "tool",
                    "name": next(tc.function.name for tc in msg.tool_calls if tc.id == cid),
                    "content": str(res)
                })

            # 4. 2차 호출: 도구 결과 바탕으로 최종 답변 생성
            final_res = await client.chat.completions.create(
                model="gpt-4o-mini", messages=history, temperature=0
            )
            answer = final_res.choices[0].message.content
        else:
            answer = msg.content

        # 5. 대화 기록 저장 및 최종 결과 반환
        history.append({"role": "assistant", "content": answer})
        save_history(user_id, history) 
        return answer

    except Exception as e:
        print(f"🚨 Agent Error: {e}") # 에러 로그 기록
        return "아라가 실시간 데이터를 분석하는 중에 잠시 파도가 쳤어! 🌊 잠시 후 다시 물어봐줘!"