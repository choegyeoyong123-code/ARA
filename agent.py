import os
import json
import asyncio
from openai import AsyncOpenAI
from database import get_history, save_history # NameError 해결
from tools import ( # ImportError 해결
    TOOLS_SPEC, get_weather_real, search_kmou_web, 
    search_campus_knowledge, get_user_profile
)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def ask_ara(user_input, user_id):
    history = get_history(user_id)
    user_name = await get_user_profile(user_id)

    # 대화 맥락이 꼬이지 않도록 정리
    if history and history[0].get("role") in ["tool", "assistant"]:
        history = []

    if not history:
        history.append({
            "role": "system", 
            "content": f"너는 한국해양대학교 AI 비서 '아라'다. 사용자는 {user_name} 선장님이다. 모든 답변은 제공된 도구 결과를 바탕으로 친절하게 3줄 이내로 답변하라."
        })
    
    history.append({"role": "user", "content": user_input})

    try:
        # 1차 호출
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=history,
            tools=TOOLS_SPEC,
            temperature=0
        )
        msg = response.choices[0].message
        
        if msg.tool_calls:
            # Pydantic 객체를 딕셔너리로 변환하여 저장 (중요!)
            history.append(msg.model_dump())
            tasks = []
            
            for tc in msg.tool_calls:
                f_name = tc.function.name
                # KeyError: 'query' 방어 로직
                try:
                    args = json.loads(tc.function.arguments)
                except:
                    args = {}
                
                # 인자 이름이 다르게 들어와도 대응 가능하도록 설정
                q = args.get('query') or args.get('argument') or user_input

                if f_name == "get_weather_real":
                    tasks.append(get_weather_real())
                elif f_name == "search_kmou_web":
                    tasks.append(search_kmou_web(q))
                elif f_name == "search_campus_knowledge":
                    tasks.append(search_campus_knowledge(q))

            # 병렬 실행으로 속도 향상
            results = await asyncio.gather(*tasks)
            
            for tc, res in zip(msg.tool_calls, results):
                history.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "name": tc.function.name,
                    "content": str(res)
                })
            
            # 2차 호출 (도구 결과 기반 답변 생성)
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
        return "선장님, 통신 상태가 불안정하여 답변을 생성하지 못했습니다. 잠시 후 다시 불러주세요! 🌊"